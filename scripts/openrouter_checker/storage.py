"""状态文件读写:原子写、双备份、fcntl 文件锁(带降级)。"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import KNOWN_MODELS_PATH, LOCK_PATH, LOG_DIR

try:
    import fcntl  # macOS / Linux
    _HAS_FCNTL = True
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore
    _HAS_FCNTL = False

logger = logging.getLogger("openrouter_checker.storage")

# 缓存刷新阈值:zh_description 超过该天数则重抓
ZH_REFRESH_DAYS = 30


class CheckerError(Exception):
    """基础异常,所有致命错误使用此类。"""

    def __init__(self, message: str, exit_code: int = 2):
        super().__init__(message)
        self.exit_code = exit_code


@contextmanager
def file_lock(lock_path: Path, timeout: int = 30, *, force_no_lock: bool = False):
    """基于 fcntl.flock 的文件锁上下文管理器(三层降级)。

    1) fcntl 可用且未指定 --no-lock:真实文件锁
    2) fcntl 不可用(Windows):警告一次,降级为无锁(单进程跑)
    3) --no-lock:显式无锁(测试用)
    """
    if force_no_lock or not _HAS_FCNTL:
        if not _HAS_FCNTL and not force_no_lock:
            logger.warning("fcntl 不可用,降级为无锁模式(Windows?)")
        yield
        return

    acquired = False
    fd = None
    elapsed = 0
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = open(lock_path, "w")
        while elapsed < timeout:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                time.sleep(1)
                elapsed += 1
        if not acquired:
            logger.warning(
                "文件锁获取超时 (%ds),继续执行(可能导致并发冲突)", timeout
            )
        yield
    finally:
        if acquired and fd is not None:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        if fd is not None:
            fd.close()


def load_known_models(path: Path | None = None) -> dict[str, Any]:
    """读取已知模型状态文件。损坏时回退到 .bak,都坏回退空字典。"""
    path = path or KNOWN_MODELS_PATH

    def _read_json(p: Path) -> dict[str, Any]:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

    if not path.exists():
        logger.info("[load_known] 状态文件不存在,视为首次运行")
        return {"models": {}, "last_check": None}

    try:
        return _read_json(path)
    except (json.JSONDecodeError, ValueError):
        logger.warning("[load_known] 状态文件损坏,尝试从 .bak 恢复...")
        bak_path = Path(str(path) + ".bak")
        if bak_path.exists():
            try:
                return _read_json(bak_path)
            except (json.JSONDecodeError, ValueError):
                logger.warning("[load_known] 备份文件也损坏,回退为空字典")
        return {"models": {}, "last_check": None}


def save_known_models(data: dict[str, Any], path: Path | None = None) -> bool:
    """原子写入已知模型状态文件,并同步一份 .bak 备份。"""
    path = path or KNOWN_MODELS_PATH
    tmp_path = Path(str(path) + ".tmp")
    bak_path = Path(str(path) + ".bak")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        json_str = json.dumps(data, ensure_ascii=False, indent=2)
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(json_str)

        os.replace(tmp_path, path)

        with open(bak_path, "w", encoding="utf-8") as f:
            f.write(json_str)

        return True
    except (OSError, IOError) as e:
        logger.error("[save_known] 状态文件写入失败: %s", e)
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        return False


def build_known_state(
    known: dict[str, Any],
    current_models: list[dict[str, Any]],
    now_iso: str,
    *,
    zh_updates: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """合并当前模型列表到已知状态,持久化 API 返回的全量字段。

    ``zh_updates``: {model_id: {zh_description, zh_source, zh_fetched_at}} 由
    HF 抓取阶段提供,仅有该字段的模型被更新 zh 缓存,不影响 first_seen。
    """
    new_known: dict[str, Any] = {
        "models": dict(known.get("models", {})),
        "last_check": now_iso,
    }
    zh_updates = zh_updates or {}

    for model in current_models:
        model_id = model["id"]
        existing = new_known["models"].get(model_id, {})
        entry = {
            "first_seen": existing.get("first_seen", now_iso),
            "data": model,
        }
        # 保留历史 zh 缓存、下线标记
        for keep_key in ("zh_description", "zh_source", "zh_fetched_at", "removed_at"):
            if keep_key in existing:
                entry[keep_key] = existing[keep_key]
        # 应用本次 HF 抓取结果
        if model_id in zh_updates:
            entry.update(zh_updates[model_id])
        # 重新上线:清除 removed_at
        if entry.get("removed_at") is not None:
            entry.pop("removed_at", None)
        new_known["models"][model_id] = entry

    return new_known


def is_first_run(known: dict[str, Any]) -> bool:
    """是否为首次运行(无已知模型)。"""
    return len(known.get("models", {})) == 0


def needs_zh_refresh(entry: dict[str, Any], now_ts: float, *, force: bool = False) -> bool:
    """判断该模型是否需要重新抓取中文介绍。

    条件:force 强制 / 从未抓取过(无 zh_fetched_at) / 距上次抓取超过
    ZH_REFRESH_DAYS 天。注意:即使上次抓取失败(source='none'),只要已标记过
    zh_fetched_at 且未超期,也跳过 —— 避免每次运行都重复踩网络坑。
    """
    if force:
        return True
    fetched = entry.get("zh_fetched_at")
    if not fetched:
        return True
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(fetched)
        age_days = (now_ts - dt.timestamp()) / 86400.0
        return age_days > ZH_REFRESH_DAYS
    except (ValueError, TypeError):
        # 时间戳损坏,保守起见重抓
        return True
