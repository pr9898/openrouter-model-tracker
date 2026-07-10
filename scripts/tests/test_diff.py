"""diff 检测测试。"""

import json
from pathlib import Path

import pytest

from openrouter_checker.diff import (
    classify_change_importance,
    detect_changes,
    important_changes,
    mark_removed,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def _load(name):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_new_model_detected():
    known = _load("known_models.fixture.json")
    current = _load("current_models.fixture.json")
    new, removed, changes = detect_changes(current, known)
    # qwen 是新增(llama 不在 current 中 → 视为下线)
    assert any(m["id"] == "qwen/qwen3-235b-a22b" for m in new)
    assert len(new) == 1
    assert removed == ["meta-llama/llama-3.3-70b-instruct"]
    # claude 价格 0.000003 → 0.00001 = +233%,major 变更
    claude_changes = [c for c in changes if c.model_id == "anthropic/claude-3.5-sonnet"]
    assert claude_changes
    assert claude_changes[0].importance == "major"


def test_price_change_threshold_ignored():
    known = {
        "models": {
            "x/model": {
                "first_seen": "2026-01-01T00:00:00",
                "data": {
                    "id": "x/model",
                    "pricing": {"prompt": "0.001", "completion": "0.001", "request": "0"},
                    "context_length": 1000,
                    "architecture": {"modality": "text->text"},
                },
            }
        }
    }
    current = [
        {
            "id": "x/model",
            "pricing": {"prompt": "0.00105", "completion": "0.001", "request": "0"},
            "context_length": 1000,
            "architecture": {"modality": "text->text"},
        }
    ]
    new, removed, changes = detect_changes(current, known, price_change_threshold=0.10)
    assert changes == []


def test_context_length_critical():
    known = {
        "models": {
            "x/model": {
                "first_seen": "2026-01-01T00:00:00",
                "data": {"id": "x/model", "context_length": 100000,
                         "architecture": {"modality": "text->text"}},
            }
        }
    }
    current = [{"id": "x/model", "context_length": 200000,
                "architecture": {"modality": "text->text"}}]
    new, removed, changes = detect_changes(current, known)
    assert changes and changes[0].importance == "critical"


def test_mark_removed_sets_timestamp():
    known = {
        "models": {
            "x/gone": {"first_seen": "2026-01-01T00:00:00",
                       "data": {"id": "x/gone"}}
        }
    }
    mark_removed(known, ["x/gone"], "2026-07-10T00:00:00")
    assert known["models"]["x/gone"]["removed_at"] == "2026-07-10T00:00:00"
    # data 保留
    assert "data" in known["models"]["x/gone"]


def test_removed_not_reported_twice():
    known = {
        "models": {
            "x/gone": {
                "first_seen": "2026-01-01T00:00:00",
                "removed_at": "2026-07-09T00:00:00",
                "data": {"id": "x/gone"},
            }
        }
    }
    # x/gone 不在 current → 但已标记 removed_at,不应再报告
    new, removed, changes = detect_changes([], known)
    assert removed == []
