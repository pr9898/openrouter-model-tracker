"""新增 / 下线 / 变更检测与重要性判定。"""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from .formatting import format_price, get_nested

logger = logging.getLogger("openrouter_checker.diff")

Severity = Literal["critical", "major", "minor", "ignore"]
ChangeType = Literal["added", "removed", "changed", "recovered"]


@dataclass
class FieldDiff:
    field: str
    old: Any
    new: Any
    severity: Severity
    reason: str = ""


@dataclass
class ModelChange:
    model_id: str
    change_type: ChangeType
    field_diffs: list[FieldDiff] = field(default_factory=list)
    importance: Severity = "ignore"
    summary_zh: str = ""
    old_data: dict | None = None
    new_data: dict | None = None


# 字段级严重度表
# compare: price_pct(价格百分比) / set_diff(集合差) / text_diff(文本相似度) / raw(原始比较)
FIELD_RULES: dict[str, dict] = {
    # critical: 永远值得通知
    "context_length": {"severity": "critical", "reason": "上下文窗口变化"},
    "architecture.modality": {"severity": "critical", "reason": "支持的模态变化"},
    # major: 单字段即报
    "pricing.prompt": {
        "severity": "major", "compare": "price_pct", "threshold": 0.10,
        "reason": "输入价格变化",
    },
    "pricing.completion": {
        "severity": "major", "compare": "price_pct", "threshold": 0.10,
        "reason": "输出价格变化",
    },
    "pricing.request": {
        "severity": "major", "compare": "price_pct", "threshold": 0.10,
        "reason": "请求价格变化",
    },
    "supported_parameters": {"severity": "major", "compare": "set_diff", "reason": "支持的推理/工具参数变化"},
    # minor: 仅在 >= 3 个 minor 字段同时变化时报告
    "name": {"severity": "minor"},
    "description": {"severity": "minor", "compare": "text_diff", "similarity_threshold": 0.8},
    # ignore: 永不报告
    "created": {"severity": "ignore"},
    "canonical_slug": {"severity": "ignore"},
    "top_provider": {"severity": "ignore", "compare": "ignore"},
    "default_parameters": {"severity": "ignore"},
    "per_request_limits": {"severity": "ignore"},
    "updated_at": {"severity": "ignore"},
}


def _cmp_price_pct(old: Any, new: Any, threshold: float) -> bool:
    """价格变化是否超过阈值。从 0 / 免费 变付费视为巨大变化。"""
    try:
        o = float(old) if old not in (None, "", "free") else 0.0
        n = float(new) if new not in (None, "", "free") else 0.0
    except (TypeError, ValueError):
        return str(old) != str(new)
    if o == n:
        return False
    denom = max(abs(o), 1e-9)
    pct = abs(n - o) / denom
    return pct > threshold


def _cmp_set_diff(old: Any, new: Any) -> bool:
    try:
        so = set(old or [])
        sn = set(new or [])
    except TypeError:
        return old != new
    return so != sn


def _cmp_text_diff(old: Any, new: Any, threshold: float) -> bool:
    o = str(old or "").strip()
    n = str(new or "").strip()
    if not o or not n:
        return o != n
    ratio = difflib.SequenceMatcher(None, o, n).ratio()
    return ratio < threshold


def _cmp_raw(old: Any, new: Any) -> bool:
    return old != new


def _compute_field_diffs(
    old: dict, new: dict, price_threshold: float
) -> list[FieldDiff]:
    """对每个注册的字段跑一次比较,生成 FieldDiff 列表。"""
    diffs: list[FieldDiff] = []
    for field_path, rule in FIELD_RULES.items():
        if rule["severity"] == "ignore":
            continue
        ov = get_nested(old, field_path)
        nv = get_nested(new, field_path)
        compare = rule.get("compare", "raw")

        if compare == "price_pct":
            changed = _cmp_price_pct(ov, nv, rule.get("threshold", price_threshold))
        elif compare == "set_diff":
            changed = _cmp_set_diff(ov, nv)
        elif compare == "text_diff":
            changed = _cmp_text_diff(ov, nv, rule.get("similarity_threshold", 0.8))
        else:
            changed = _cmp_raw(ov, nv)

        if changed:
            diffs.append(
                FieldDiff(
                    field=field_path,
                    old=ov,
                    new=nv,
                    severity=rule["severity"],
                    reason=rule.get("reason", ""),
                )
            )
    return diffs


def classify_change_importance(diffs: list[FieldDiff]) -> Severity:
    """聚合字段级 diff 到单一重要性等级。"""
    has_critical = any(d.severity == "critical" for d in diffs)
    if has_critical:
        return "critical"
    has_major = any(d.severity == "major" for d in diffs)
    if has_major:
        return "major"
    minor_count = sum(1 for d in diffs if d.severity == "minor")
    if minor_count >= 3:
        return "minor"
    return "ignore"


def _build_summary(diffs: list[FieldDiff]) -> str:
    parts = []
    for d in diffs:
        if d.field.startswith("pricing."):
            parts.append(
                f"{d.field}: {format_price(d.old)} → {format_price(d.new)}"
            )
        elif d.field == "context_length":
            parts.append(f"上下文: {d.old} → {d.new}")
        elif d.field == "architecture.modality":
            parts.append(f"模态: {d.old} → {d.new}")
        else:
            parts.append(f"{d.field}: 变化")
    return "; ".join(parts)


def _format_field_old_new(d: FieldDiff) -> tuple[str, str]:
    if d.field.startswith("pricing."):
        return format_price(d.old), format_price(d.new)
    if d.old is None and d.new is None:
        return "-", "-"
    return str(d.old), str(d.new)


def detect_changes(
    current_models: list[dict[str, Any]],
    known: dict[str, Any],
    *,
    price_change_threshold: float = 0.10,
) -> tuple[list[dict[str, Any]], list[str], list[ModelChange]]:
    """对比当前模型列表与已知状态。

    返回 (新增模型列表, 下线模型 id 列表, 变更 ModelChange 列表)。
    """
    known_models = known.get("models", {})
    known_set = set(known_models.keys())
    current_list = [m for m in current_models if m.get("id")]
    current_map = {m["id"]: m for m in current_list}
    current_set = set(current_map.keys())

    # 1) 新增
    new_models = [current_map[mid] for mid in current_set - known_set]

    # 2) 下线(只报刚消失的,已标记 removed_at 的不重报)
    removed_ids = [
        mid
        for mid in (known_set - current_set)
        if known_models[mid].get("removed_at") is None
    ]
    # 2b) 恢复(曾下线又出现)
    recovered_ids = [
        mid
        for mid in (known_set - current_set)
        if known_models[mid].get("removed_at") is not None
        and mid in current_set
    ]

    # 3) 变更
    changes: list[ModelChange] = []
    for mid in known_set & current_set:
        old_data = known_models[mid].get("data", {})
        new_data = current_map[mid]
        diffs = _compute_field_diffs(old_data, new_data, price_change_threshold)
        if diffs:
            importance = classify_change_importance(diffs)
            changes.append(
                ModelChange(
                    model_id=mid,
                    change_type="changed",
                    field_diffs=diffs,
                    importance=importance,
                    summary_zh=_build_summary(diffs),
                    old_data=old_data,
                    new_data=new_data,
                )
            )

    # 恢复事件记为一条 changed 记录(轻量)
    for mid in recovered_ids:
        changes.append(
            ModelChange(
                model_id=mid,
                change_type="recovered",
                field_diffs=[],
                importance="minor",
                summary_zh="模型重新上线",
                old_data=known_models[mid].get("data"),
                new_data=current_map[mid],
            )
        )

    if new_models:
        logger.info("[detect] 发现 %d 个新模型", len(new_models))
    if removed_ids:
        logger.info("[detect] 发现 %d 个下线模型", len(removed_ids))
    if changes:
        important = [c for c in changes if c.importance in ("critical", "major")]
        logger.info(
            "[detect] 发现 %d 个变更(%d 个重要)", len(changes), len(important)
        )

    return new_models, removed_ids, changes


def mark_removed(
    known: dict[str, Any], removed_ids: list[str], now_iso: str
) -> None:
    """状态合并时,不给下线 id 移除,只标记 removed_at 并保留 data 快照。"""
    models = known.setdefault("models", {})
    for mid in removed_ids:
        if mid in models:
            models[mid]["removed_at"] = now_iso
            # data 保留最后一次快照,便于审计


def important_changes(changes: list[ModelChange]) -> list[ModelChange]:
    """过滤出 critical/major 级别的变更(值得推送企微)。"""
    return [c for c in changes if c.importance in ("critical", "major")]
