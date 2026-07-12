"""主流程:拉取 OpenRouter → 检测变化 → 抓中文介绍 → 写状态 → 推送通知。"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Any

from .api import (
    CheckerError,
    check_hf_reachable,
    create_retry_session,
    fetch_openrouter_models,
    fetch_usd_cny_rate,
)
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
from .translate import TranslateConfig

logger = logging.getLogger("openrouter_checker")


def run(args: argparse.Namespace) -> int:
    dotenv_vars = load_env()
    api_key = get_env("OPENROUTER_API_KEY", "", dotenv_vars)
    api_url = get_env("OPENROUTER_API_URL", "https://openrouter.ai/api/v1", dotenv_vars)
    wechat_key = get_env("WECHAT_WEBHOOK_KEY", "", dotenv_vars)
    hf_token = get_env("HF_TOKEN", "", dotenv_vars)
    refresh_zh = args.refresh_zh or get_env_bool("REFRESH_ZH", False, dotenv_vars)

    # LLM 翻译配置:HF README 无中文时,调 OpenRouter free 模型翻译英文描述
    translate_cfg = TranslateConfig(
        api_url=api_url,
        api_key=api_key,
        model=get_env("ZH_TRANSLATE_MODEL", "tencent/hy3:free", dotenv_vars),
        enabled=get_env_bool("ZH_TRANSLATE_ENABLED", True, dotenv_vars),
    )
    if translate_cfg.enabled and not api_key:
        logger.info("[zh] 未配置 OPENROUTER_API_KEY,LLM 翻译禁用,回退英文描述")

    current_time = time.strftime("%Y-%m-%dT%H:%M:%S")
    bj_time = time.strftime("%Y-%m-%d %H:%M:%S")
    session = create_retry_session()
    # 实时美元→人民币汇率(失败兜底,不阻断)
    usd_cny_rate = fetch_usd_cny_rate(session)

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
            _print_dry_run(new_models, removed_ids, changes, total_models, bj_time, known, usd_cny_rate=usd_cny_rate)
        else:
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%S")

            # 抓取中文介绍(仅需要刷新的)
            zh_updates = _fetch_zh_if_needed(
                known, models, session, hf_token, refresh_zh, translate_cfg
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

    # 通知(非首次 + 有 key)—— 每天必发,无论是否有变化
    if not args.dry_run and not first_run:
        if args.quiet:
            logger.info("[main] --quiet 模式,跳过通知")
        elif wechat_key:
            try:
                # 通知前重读最新状态(含刚抓的 zh_description),
                # 给 new_models 的原始 API dict 补上中文简介,
                # 否则通知「简介」列取不到值显示为 -
                fresh_known = load_known_models()
                fresh_models = fresh_known.get("models", {})
                for m in new_models:
                    mid = m["id"]
                    zh = fresh_models.get(mid, {}).get("zh_description")
                    if zh and not m.get("zh_description"):
                        m["zh_description"] = zh
                ok = send_notifications(
                    session, wechat_key, new_models, removed_ids,
                    changes, total_models, current_time,
                    known=fresh_known,
                    usd_cny_rate=usd_cny_rate,
                )
                if not ok:
                    has_failure = True
            except Exception as e:
                logger.warning("[notify] 通知异常: %s", e)
                has_failure = True
        else:
            logger.warning("[main] 缺少 WECHAT_WEBHOOK_KEY,跳过通知")
    elif first_run and not args.dry_run:
        logger.info("[main] 首次运行,已建立基线(%d 个模型),跳过通知", total_models)

    return 1 if has_failure else 0


def _fetch_zh_if_needed(
    known, models, session, hf_token, refresh_zh, translate_cfg=None
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

    # 探测 HuggingFace 源连通性:官方优先,不通则回退中国镜像,都不通跳过联网
    source, source_desc = check_hf_reachable(session)
    if source is None:
        logger.warning(
            "[zh] HuggingFace 官方与镜像源均不可达,本批 %d 个模型回退英文描述",
            len(items),
        )
        # 源不可达时不写 zh_fetched_at,下次运行自动重试,避免一次网络抖动锁死 30 天
        updates: dict[str, dict] = {}
        for mid, _, desc in items:
            updates[mid] = {
                "zh_description": (desc or "").strip(),
                "zh_source": "openrouter-description" if desc else "none",
                "zh_fetched_at": "",
            }
        return updates

    logger.info("[zh] 使用 HF 源: %s,需抓取 %d 个模型", source_desc, len(items))
    cards = batch_fetch_cards(
        items, hf_token=hf_token, source=source, translate=translate_cfg
    )
    # hf_repo 是否为空,用于区分「本就无 HF 卡」与「抓取失败」
    has_repo = {mid: bool(repo) for mid, repo, _ in items}
    updates: dict[str, dict] = {}
    for mid, card in cards.items():
        # 长期缓存(写 zh_fetched_at,30 天内不重抓)的条件:
        # 1. 成功提取中文 README (hf-readme)
        # 2. README 抓到但无中文段落 (hf-readme-no-zh) —— 重抓也一样,无谓
        # 3. LLM 翻译成功 (llm-translate) —— 已是中文,无需重翻
        # 4. 模型本就无 HF repo (hugging_face_id 为空) —— 无处可抓
        # 其余(有 repo 但 README 抓取失败/不存在)不缓存,下次重试
        cache = card.source in ("hf-readme", "hf-readme-no-zh", "llm-translate") or not has_repo.get(
            mid, False
        )
        updates[mid] = {
            "zh_description": card.text,
            "zh_source": card.source,
            "zh_fetched_at": card.fetched_at if cache else "",
        }
    return updates


def _print_dry_run(new_models, removed_ids, changes, total_models, bj_time, known, *, usd_cny_rate=7.2):
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
                usd_cny_rate=usd_cny_rate,
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
