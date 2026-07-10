"""主流程:拉取 OpenRouter → 检测变化 → 抓中文介绍 → 写状态 → 推送通知。"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Any

from .api import CheckerError, create_retry_session, fetch_openrouter_models
from .config import (
    LOG_DIR,
    PROJECT_DIR,
    REPORT_DIR,
    get_env,
    get_env_bool,
    load_env,
)
from .diff import detect_changes, important_changes, mark_removed
from .hf_card import batch_fetch_cards
from .notify import build_summary_message, send_notifications
from .storage import (
    CheckerError as StorageError,  # noqa: F401
    build_known_state,
    file_lock,
    is_first_run,
    load_known_models,
    needs_zh_refresh,
    save_known_models,
)

logger = logging.getLogger("openrouter_checker")


def run(args: argparse.Namespace) -> int:
    dotenv_vars = load_env()
    api_key = get_env("OPENROUTER_API_KEY", "", dotenv_vars)
    api_url = get_env("OPENROUTER_API_URL", "https://openrouter.ai/api/v1", dotenv_vars)
    wechat_key = get_env("WECHAT_WEBHOOK_KEY", "", dotenv_vars)
    hf_token = get_env("HF_TOKEN", "", dotenv_vars)
    refresh_zh = args.refresh_zh or get_env_bool("REFRESH_ZH", False, dotenv_vars)

    current_time = time.strftime("%Y-%m-%dT%H:%M:%S")
    bj_time = time.strftime("%Y-%m-%d %H:%M:%S")
    session = create_retry_session()

    try:
        models = fetch_openrouter_models(session, api_url, api_key)
    except CheckerError as e:
        logger.error("%s", e)
        return e.exit_code

    has_failure = False
    total_models = len(models)
    new_models: list[dict[str, Any]] = []
    removed_ids: list[str] = []
    changes = []

    with file_lock(PROJECT_DIR / ".lock", force_no_lock=args.no_lock):
        known = load_known_models()
        first_run = is_first_run(known)

        new_models, removed_ids, changes = detect_changes(models, known)

        if args.dry_run:
            _print_dry_run(new_models, removed_ids, changes, total_models, bj_time, known)
        else:
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%S")

            # 抓取中文介绍(仅需要刷新的)
            zh_updates = _fetch_zh_if_needed(
                known, models, session, hf_token, refresh_zh, now_iso
            )

            # 合并状态
            new_known = build_known_state(
                known, models, now_iso, zh_updates=zh_updates
            )
            # 标记下线
            mark_removed(new_known, removed_ids, now_iso)

            if not save_known_models(new_known):
                has_failure = True

            # 写精简报告
            _write_report(new_models, removed_ids, changes, total_models, now_iso)

    # 通知(非首次 + 有变化 + 有 key)
    if not args.dry_run and not first_run:
        if new_models or removed_ids or important_changes(changes):
            if wechat_key:
                try:
                    ok = send_notifications(
                        session, wechat_key, new_models, removed_ids,
                        changes, total_models, current_time,
                        known=load_known_models(),
                    )
                    if not ok:
                        has_failure = True
                except Exception as e:
                    logger.warning("[notify] 通知异常: %s", e)
                    has_failure = True
            else:
                logger.warning("[main] 缺少 WECHAT_WEBHOOK_KEY,跳过通知")
        else:
            logger.info("[main] 无新增/下线/重要变更,跳过通知")
    elif first_run and not args.dry_run:
        logger.info("[main] 首次运行,已建立基线(%d 个模型),跳过通知", total_models)
    elif args.quiet and not new_models and not removed_ids and not changes:
        logger.info("[main] --quiet 模式,无变化")

    return 1 if has_failure else 0


def _fetch_zh_if_needed(
    known, models, session, hf_token, refresh_zh, now_iso
) -> dict[str, dict]:
    """收集需要抓中文介绍的模型,并发抓取。返回 {model_id: zh 缓存 dict}。"""
    now_ts = time.time()
    known_models = known.get("models", {})
    items: list[tuple[str, str | None, str]] = []
    for m in models:
        mid = m["id"]
        existing = known_models.get(mid, {})
        if not needs_zh_refresh(existing, now_ts, force=refresh_zh):
            continue
        hf_repo = m.get("hugging_face_id")
        desc = m.get("description", "") or ""
        items.append((mid, hf_repo, desc))

    if not items:
        return {}
    logger.info("[zh] 需抓取中文介绍 %d 个模型", len(items))
    cards = batch_fetch_cards(items, hf_token=hf_token)
    updates: dict[str, dict] = {}
    for mid, card in cards.items():
        if card.text:
            updates[mid] = {
                "zh_description": card.text,
                "zh_source": card.source,
                "zh_fetched_at": card.fetched_at,
            }
        else:
            # 即使是 none,也更新 fetched_at 避免反复重试
            updates[mid] = {
                "zh_description": "",
                "zh_source": card.source,
                "zh_fetched_at": card.fetched_at,
            }
    return updates


def _print_dry_run(new_models, removed_ids, changes, total_models, bj_time, known):
    print(f"[dry-run] 新增 {len(new_models)} / 下线 {len(removed_ids)} / 变更 {len(changes)}", file=sys.stderr)
    for m in new_models:
        print(f"  🆕 {m['id']} — {m.get('name', '')}", file=sys.stderr)
    for mid in removed_ids:
        print(f"  🔻 {mid}", file=sys.stderr)
    for c in changes:
        print(f"  ⚡ {c.model_id} [{c.importance}] {c.summary_zh}", file=sys.stderr)
    if new_models or removed_ids or important_changes(changes):
        print("[dry-run] 通知预览:", file=sys.stderr)
        print(
            build_summary_message(
                new_models, removed_ids, changes, total_models,
                time.strftime("%Y-%m-%dT%H:%M:%S"), known,
            ),
            file=sys.stderr,
        )


def _write_report(new_models, removed_ids, changes, total_models, now_iso):
    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        import json

        report = {
            "time": now_iso,
            "total_models": total_models,
            "new": [
                {"id": m["id"], "name": m.get("name", ""), "zh": m.get("zh_description", "")}
                for m in new_models
            ],
            "removed": removed_ids,
            "changed": [
                {
                    "id": c.model_id,
                    "importance": c.importance,
                    "summary": c.summary_zh,
                    "fields": [
                        {"field": d.field, "old": d.old, "new": d.new}
                        for d in c.field_diffs
                    ],
                }
                for c in changes
            ],
        }
        date_str = now_iso[:10]
        path = REPORT_DIR / f"{date_str}.json"
        # 追加式:一天内多次运行合并
        existing = []
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                existing = []
        existing.append(report)
        path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("[report] 写报告失败: %s", e)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="OpenRouter 模型上新检测(增强版)")
    p.add_argument("--verbose", action="store_true", help="输出 DEBUG 级别日志")
    p.add_argument("--log-file", action="store_true", help="同时输出日志到文件")
    p.add_argument("--quiet", action="store_true", help="无变化时跳过通知")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="调试模式:不写状态文件、不发通知,变化打印到 stderr",
    )
    p.add_argument(
        "--refresh-zh",
        action="store_true",
        help="强制重新抓取所有模型的中文介绍",
    )
    p.add_argument(
        "--no-lock",
        action="store_true",
        help="不使用文件锁(Windows / CI 单进程测试用)",
    )
    return p


def main() -> int:
    args = build_arg_parser().parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if args.log_file:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.FileHandler(LOG_DIR / "check_openrouter_models.log", encoding="utf-8")
        )
    logging.basicConfig(level=log_level, format=log_format, handlers=handlers)

    return run(args)


if __name__ == "__main__":
    sys.exit(main())
