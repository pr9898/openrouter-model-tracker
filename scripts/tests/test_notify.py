"""通知构建与分片测试。"""

import json
from pathlib import Path

from openrouter_checker.diff import FieldDiff, ModelChange, detect_changes
from openrouter_checker.formatting import format_price_both
from openrouter_checker.notify import (
    build_summary_message,
    split_message_by_sections,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def _load(name):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_build_summary_contains_three_sections():
    known = _load("known_models.fixture.json")
    current = _load("current_models.fixture.json")
    new, removed, changes = detect_changes(current, known)
    msg = build_summary_message(new, removed, changes, len(current), "2026-07-10T00:00:00", known)
    assert "## ✨ 新增模型" in msg
    assert "## ⚡ 重要变更" in msg
    assert "qwen/qwen3-235b-a22b" in msg
    assert "anthropic/claude-3.5-sonnet" in msg
    # 重要变更表带简介列,回退英文描述
    assert "| 🆔 ID | 字段 | 旧 | 新 | 📝 简介 |" in msg
    assert "Claude 3.5 Sonnet is a strong model." in msg


def test_split_short_message_single():
    msg = "短消息\n无超长内容"
    out = split_message_by_sections(msg, max_bytes=4000)
    assert len(out) == 1


def test_split_long_message_multiple():
    # 构造超长消息(重复表格行)
    rows = "\n".join(f"| m{i} | f{i} | old{i} | new{i} |" for i in range(200))
    content = (
        "# 🆕 OpenRouter 模型变动\n"
        "> 检测时间\n\n"
        "## ⚡ 重要变更\n"
        "| ID | 字段 | 旧 | 新 |\n| --- | --- | --- | --- |\n"
        + rows
    )
    out = split_message_by_sections(content, max_bytes=4000)
    assert len(out) >= 2
    # 每条应附 (i/N) 标记
    assert "(1/" in out[0]
    # 每条不超过限制
    for part in out:
        assert len(part.encode("utf-8")) <= 4000 + 200  # 容差


def test_no_changes_message():
    msg = build_summary_message([], [], [], 100, "2026-07-10T00:00:00")
    assert "无新增" in msg


def _new_model(mid, *, zh="", description="", pricing=None):
    m = {
        "id": mid,
        "name": mid.split("/")[-1],
        "architecture": {"modality": "text"},
        "context_length": 128000,
    }
    if zh:
        m["zh_description"] = zh
    if description:
        m["description"] = description
    if pricing is not None:
        m["pricing"] = pricing
    return m


def test_new_model_table_has_price_column_before_intro():
    new = [_new_model(
        "acme/foo", zh="中文介绍",
        pricing={"prompt": "0.00000125", "completion": "0.00001"},
    )]
    msg = build_summary_message(new, [], [], 1, "2026-07-10T00:00:00", usd_cny_rate=7.2)
    # 表头:价格列位于中文介绍列之前
    assert "| 🤖 名称 | 🆔 ID | 🔀 模态 | 📏 上下文 | 💰 价格 | 📝 简介 |" in msg
    # 价格格含美元 + 人民币
    assert "1.25美元" in msg and "¥9.00" in msg
    assert "出 10美元·¥72.00" in msg


def test_intro_falls_back_to_english_description():
    new = [_new_model("acme/bar", description="An English model description.")]
    msg = build_summary_message(new, [], [], 1, "2026-07-10T00:00:00")
    row = [l for l in msg.splitlines() if l.startswith("|") and "acme/bar" in l][0]
    # 无中文时回退英文介绍,而非显示 -
    assert "An English model description." in row


def test_change_table_uses_cached_zh_description():
    known = {
        "models": {
            "acme/baz": {
                "first_seen": "2026-01-01T00:00:00",
                "zh_description": "缓存的中文简介",
                "data": {"id": "acme/baz", "description": "English fallback."},
            }
        }
    }
    change = ModelChange(
        model_id="acme/baz",
        change_type="changed",
        field_diffs=[
            FieldDiff(
                field="pricing.prompt", old="0.000001", new="0.000002",
                severity="major", reason="输入价格变化",
            )
        ],
        importance="major",
        summary_zh="pricing.prompt: $1 → $2",
        old_data={"id": "acme/baz"},
        new_data={"id": "acme/baz", "description": "English fallback."},
    )
    msg = build_summary_message([], [], [change], 1, "2026-07-10T00:00:00", known)
    row = [l for l in msg.splitlines() if l.startswith("|") and "acme/baz" in l][0]
    # 优先取缓存的中文简介,而非英文 description
    assert "缓存的中文简介" in row
    assert "English fallback." not in row


def test_format_price_both_usd_and_cny():
    assert format_price_both(
        {"prompt": "0.00000125", "completion": "0.00001"}, 7.2
    ) == "入 1.25美元·¥9.00 出 10美元·¥72.00"
    # 仅输入价
    assert format_price_both({"prompt": "0.000001"}) == "入 1美元·¥7.20"
    # 免费
    assert format_price_both({"prompt": "0", "completion": "0"}) == "免费"
    # 无 pricing
    assert format_price_both(None) == "-"
    # 汇率默认值兜底
    assert "¥" in format_price_both({"prompt": "0.000001"})
