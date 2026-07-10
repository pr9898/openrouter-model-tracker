#!/usr/bin/env python3
"""一次性给存量模型批量补抓 HuggingFace 中文介绍。

用法:
  python scripts/tools/backfill_zh_descriptions.py [--limit N] [--force]

用途:首次基线后,known_models.json 里没有 zh_description,用本工具批量补抓。
已有缓存的会跳过(除非 --force)。
"""

import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent))  # scripts/

from openrouter_checker.api import create_retry_session
from openrouter_checker.config import get_env, load_env
from openrouter_checker.hf_card import batch_fetch_cards
from openrouter_checker.storage import KNOWN_MODELS_PATH, load_known_models, save_known_models


def main() -> int:
    import argparse

    p = argparse.ArgumentParser(description="批量补抓中文介绍")
    p.add_argument("--limit", type=int, default=0, help="最多处理 N 个(0=全部)")
    p.add_argument("--force", action="store_true", help="强制重抓已有缓存的")
    p.add_argument("--max-workers", type=int, default=5)
    args = p.parse_args()

    dotenv_vars = load_env()
    hf_token = get_env("HF_TOKEN", "", dotenv_vars)

    known = load_known_models()
    models_db = known.get("models", {})

    items = []
    for mid, entry in models_db.items():
        # 已有缓存且非强制 → 跳过
        if not args.force and entry.get("zh_description"):
            continue
        data = entry.get("data", {})
        hf_repo = data.get("hugging_face_id")
        desc = data.get("description", "") or ""
        items.append((mid, hf_repo, desc))
        if args.limit and len(items) >= args.limit:
            break

    if not items:
        print("没有需要补抓的模型。")
        return 0

    print(f"开始补抓 {len(items)} 个模型的中文介绍...")
    session = create_retry_session()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S")
    cards = batch_fetch_cards(
        items, hf_token=hf_token, max_workers=args.max_workers
    )

    updated = 0
    for mid, card in cards.items():
        if card.text:
            models_db[mid]["zh_description"] = card.text
            models_db[mid]["zh_source"] = card.source
            models_db[mid]["zh_fetched_at"] = card.fetched_at
            updated += 1
        elif card.source == "none":
            models_db[mid]["zh_fetched_at"] = card.fetched_at

    known["models"] = models_db
    save_known_models(known)
    print(f"完成,成功填充 {updated}/{len(items)} 个模型的中文介绍。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
