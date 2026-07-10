#!/usr/bin/env python3
"""测试用:在 known_models.json 中制造新增/下线/变更场景,便于验证检测逻辑。

用法:
  # 模拟某模型下线(从 data 移除,标记 absent)
  python scripts/tools/simulate_changes.py --remove <model_id>

  # 模拟某模型价格变化
  python scripts/tools/simulate_changes.py --price-change <model_id> --new-price 0.01

  # 模拟某模型上下文变化
  python scripts/tools/simulate_changes.py --context-change <model_id> --new-ctx 200000

  # 模拟某模型"新增"(删除 known 中的记录,下次运行视为新增)
  python scripts/tools/simulate_changes.py --add <model_id>

注意:本工具只修改 known_models.json,不调用 API。运行主脚本 --dry-run 观察结果。
"""

import argparse
import copy
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))  # scripts/

from openrouter_checker.storage import KNOWN_MODELS_PATH, load_known_models, save_known_models


def _load() -> dict:
    if not KNOWN_MODELS_PATH.exists():
        print("known_models.json 不存在,请先运行一次建立基线。")
        sys.exit(1)
    return load_known_models()


def cmd_remove(known: dict, model_id: str) -> None:
    models = known.get("models", {})
    if model_id not in models:
        print(f"模型中无 {model_id},无法模拟下线。")
        return
    # 保存快照到 _sim_removed 以便恢复,然后从 data 移除
    entry = models[model_id]
    entry["_sim_removed"] = copy.deepcopy(entry.get("data"))
    entry.pop("data", None)
    entry["removed_at_sim"] = "2099-01-01T00:00:00"
    print(f"已模拟下线: {model_id} (运行主脚本将检测到下线)")


def cmd_add(known: dict, model_id: str) -> None:
    models = known.get("models", {})
    if model_id in models:
        models.pop(model_id)
        print(f"已删除 {model_id},下次运行将视为新增")
    else:
        print(f"{model_id} 本就不在 known,无需操作")


def cmd_price_change(known: dict, model_id: str, new_price: str) -> None:
    models = known.get("models", {})
    if model_id not in models:
        print(f"模型中无 {model_id}")
        return
    data = models[model_id].setdefault("data", {})
    pricing = data.setdefault("pricing", {})
    pricing["prompt"] = new_price
    print(f"已将 {model_id} 的 pricing.prompt 改为 {new_price}")


def cmd_context_change(known: dict, model_id: str, new_ctx: int) -> None:
    models = known.get("models", {})
    if model_id not in models:
        print(f"模型中无 {model_id}")
        return
    data = models[model_id].setdefault("data", {})
    data["context_length"] = new_ctx
    print(f"已将 {model_id} 的 context_length 改为 {new_ctx}")


def main() -> int:
    p = argparse.ArgumentParser(description="模拟模型变化(测试用)")
    p.add_argument("--remove", metavar="MODEL_ID")
    p.add_argument("--add", metavar="MODEL_ID")
    p.add_argument("--price-change", metavar="MODEL_ID")
    p.add_argument("--new-price", default="0.01")
    p.add_argument("--context-change", metavar="MODEL_ID")
    p.add_argument("--new-ctx", type=int, default=200000)
    args = p.parse_args()

    if not any([args.remove, args.add, args.price_change, args.context_change]):
        p.print_help()
        return 1

    known = _load()
    if args.remove:
        cmd_remove(known, args.remove)
    if args.add:
        cmd_add(known, args.add)
    if args.price_change:
        cmd_price_change(known, args.price_change, args.new_price)
    if args.context_change:
        cmd_context_change(known, args.context_change, args.new_ctx)

    save_known_models(known)
    print("已写入 known_models.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
