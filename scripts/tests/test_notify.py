"""通知构建与分片测试。"""

import json
from pathlib import Path

from openrouter_checker.diff import ModelChange, detect_changes
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
