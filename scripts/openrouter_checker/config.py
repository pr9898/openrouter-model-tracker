"""环境变量加载与项目路径常量。

路径约定: ``openrouter_checker`` 包位于 ``scripts/openrouter_checker/``,
项目根目录为 ``scripts/`` 的父目录。状态文件、日志等放在项目根目录。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------

PACKAGE_DIR = Path(__file__).resolve().parent          # scripts/openrouter_checker
SCRIPTS_DIR = PACKAGE_DIR.parent                       # scripts
PROJECT_DIR = SCRIPTS_DIR.parent                        # 项目根目录

KNOWN_MODELS_PATH = PROJECT_DIR / "known_models.json"
LOCK_PATH = PROJECT_DIR / ".lock"
LOG_DIR = PROJECT_DIR / "logs"
REPORT_DIR = LOG_DIR / "reports"

DEFAULT_OPENROUTER_API_URL = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT = 30

# ---------------------------------------------------------------------------
# 环境变量
# ---------------------------------------------------------------------------


def load_env(env_file: Path | None = None) -> dict[str, str]:
    """从 .env 文件加载环境变量,不修改 os.environ。

    支持 ``KEY=VALUE``、引号包裹、行内 ``#`` 注释。空行与 ``#`` 开头行忽略。
    """
    env_path = env_file or (PROJECT_DIR / ".env")
    if not env_path.exists():
        return {}
    env_vars: dict[str, str] = {}
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            value_part = line.split("#", 1)[0] if "#" in line else line
            key, _, value = value_part.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                env_vars[key] = value
    return env_vars


def get_env(
    key: str,
    default: str = "",
    dotenv_vars: dict[str, str] | None = None,
) -> str:
    """获取环境变量值。优先级：os.environ > .env 文件 > default。

    所有来源的值都会 ``strip()``,避免 Secret / 环境变量里混入的换行符或
    首尾空格污染 HTTP 请求头(如 ``Authorization: Bearer <脏值>`` 触发
    ``Invalid ... header value``)。
    """
    if key in os.environ:
        return os.environ[key].strip()
    if dotenv_vars and key in dotenv_vars:
        return dotenv_vars[key].strip()
    return default


def get_env_bool(
    key: str,
    default: bool = False,
    dotenv_vars: dict[str, str] | None = None,
) -> bool:
    """获取布尔型环境变量。"""
    raw = get_env(key, "", dotenv_vars)
    if not raw:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")
