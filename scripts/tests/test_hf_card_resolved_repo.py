"""复现 `get_zh_description` 中 `resolved` 未回写导致的裸 AssertionError,
并锁定 bug 条件不成立时的既有行为,防止修复引入回归。

对应 .kiro/specs/zh-description-blank/{bugfix,design,tasks}.md。

用法:
- 在**修复前**运行本文件:Property 1(bug 条件成立)的用例应全部 FAIL——
  前 4 个直接抛出未捕获的裸 AssertionError(崩溃发生在 session.get 被调用
  之前),第 5 个(经 batch_fetch_cards)因异常被通用 except Exception 吞掉、
  级联成空卡片而断言失败;Property 2(bug 条件不成立)的用例应全部 PASS。
- 实施修复(hf_card.py 中补上 `hf_repo_id = resolved`)后重新运行本文件:
  全部用例都应 PASS。
"""

from __future__ import annotations

import unittest.mock as mock

import pytest

from openrouter_checker.hf_card import HfCard, batch_fetch_cards, get_zh_description
from openrouter_checker.notify import build_summary_message

# ---------------------------------------------------------------------------
# 共享 fixtures
# ---------------------------------------------------------------------------

# 含中文段落的 README,结构比照 test_hf_card.py 的 QWEN_README
ZH_README = """---
license: apache-2.0
---

# Kat Coder Air V2.5

Kat Coder Air 是快手推出的代码生成大模型,专注于高效的代码补全与生成任务,支持多种编程语言与长上下文理解能力。

## 使用示例

```python
model = AutoModel.from_pretrained("kwaipilot/kat-coder-air-v2.5")
```
"""

# 纯英文 README,不含任何中文段落
EN_README = """---
license: apache-2.0
---

# Kat Coder Pro V2.5

Kat Coder Pro is a code generation model focused on efficient code completion
and generation tasks. It supports multiple programming languages and long
context understanding for complex repository-level tasks.

## Usage

```python
model = AutoModel.from_pretrained("kwaipilot/kat-coder-pro-v2.5")
```
"""


class _FakeResp:
    """轻量 fake response,匹配 requests.Response 的最小接口。"""

    def __init__(self, status_code, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


def _session_returning(status_code, text="", headers=None):
    """构造一个 .get() 固定返回给定状态码/正文/响应头的 mock session。"""
    session = mock.MagicMock()
    session.get.return_value = _FakeResp(status_code, text, headers)
    return session


# ---------------------------------------------------------------------------
# Property 1: Bug Condition —— hf_repo_id 为空,且 model_id 可解析出
# org/name,且 source 可达(design.md isBugCondition)。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_id, openrouter_description, session_factory, expected_source, expect_text",
    [
        pytest.param(
            "kwaipilot/kat-coder-air-v2.5",
            "",
            lambda: _session_returning(200, ZH_README),
            "hf-readme",
            "快手",
            id="readme-has-zh-paragraph",
        ),
        pytest.param(
            "kwaipilot/kat-coder-pro-v2.5",
            "Kat Coder Pro is a code model.",
            lambda: _session_returning(200, EN_README),
            "hf-readme-no-zh",
            "Kat Coder Pro is a code model.",
            id="readme-no-zh-paragraph",
        ),
        pytest.param(
            "kwaipilot/kat-coder-air-v2.5",
            "Kat Coder Air is a code model.",
            lambda: _session_returning(404),
            "openrouter-description",
            "Kat Coder Air is a code model.",
            id="readme-404-not-found",
        ),
        pytest.param(
            "kwaipilot/kat-coder-air-v2.5",
            "Kat Coder Air is a code model.",
            lambda: _session_returning(429, headers={"Retry-After": "10"}),
            "openrouter-description",
            "Kat Coder Air is a code model.",
            id="readme-429-rate-limited",
        ),
    ],
)
def test_bug_condition_resolves_and_does_not_crash(
    model_id, openrouter_description, session_factory, expected_source, expect_text
):
    """Property 1: hf_repo_id 为空但可从 model_id 推断时,应使用推断路径继续
    抓取,不得抛出未处理异常,并按现有兜底链落入正确的 HfCard.source。

    未修复代码上,四个用例都会在到达下面任何断言之前就因为裸
    AssertionError 崩溃(pytest 记为 ERROR),这正是复现 bug 的证据。
    """
    session = session_factory()
    card = get_zh_description(model_id, None, openrouter_description, session, source="official")
    assert isinstance(card, HfCard)
    assert card.source == expected_source
    assert expect_text in card.text


def test_bug_condition_fetch_targets_resolved_repo_path():
    """修复后,推断出的仓库路径应被真正用于发起请求,而不是被忽略。"""
    session = _session_returning(200, ZH_README)
    get_zh_description("kwaipilot/kat-coder-air-v2.5", None, "", session, source="official")
    session.get.assert_called_once()
    requested_url = session.get.call_args[0][0]
    assert "kwaipilot/kat-coder-air-v2.5" in requested_url


def test_bug_condition_via_batch_fetch_cards_no_longer_becomes_empty_card():
    """经 batch_fetch_cards 间接调用:修复前裸异常被通用 except Exception
    吞掉,级联成 HfCard(text="", source="none");修复后应拿到真实抓取结果。
    """
    items = [("kwaipilot/kat-coder-air-v2.5", None, "")]
    fake_session = _session_returning(200, ZH_README)
    with mock.patch(
        "openrouter_checker.hf_card.create_retry_session", return_value=fake_session
    ):
        results = batch_fetch_cards(items, source="official", per_request_sleep=0)
    card = results["kwaipilot/kat-coder-air-v2.5"]
    assert card.source == "hf-readme"
    assert card.text != ""


# ---------------------------------------------------------------------------
# Property 2: Preservation —— bug 条件不成立时,行为必须与修复前完全一致。
# ---------------------------------------------------------------------------


def test_preserved_existing_hf_repo_id_used_directly():
    """已有 hugging_face_id 的模型:直接用该 id 抓取,完全不经过被修改的分支。"""
    session = _session_returning(200, ZH_README)
    card = get_zh_description(
        "meta-llama/llama-3.3-70b-instruct",
        "meta-llama/Llama-3.3-70B-Instruct",
        "",
        session,
        source="official",
    )
    assert card.source == "hf-readme"
    requested_url = session.get.call_args[0][0]
    assert "meta-llama/Llama-3.3-70B-Instruct" in requested_url


def test_preserved_unparsable_model_id_skips_network():
    """model_id 不含 '/'、无法解析出 org/name:提前 return 英文兜底,不联网。"""
    session = mock.MagicMock()
    card = get_zh_description("standalone-model", None, "Some desc", session, source="official")
    assert card.source == "openrouter-description"
    assert card.text == "Some desc"
    session.get.assert_not_called()


def test_preserved_source_none_skips_network():
    """source=None(HF 源不可达/跳过联网):提前 return 英文兜底,不联网。"""
    session = mock.MagicMock()
    card = get_zh_description("org/name", None, "Some desc", session, source=None)
    assert card.source == "openrouter-description"
    assert card.text == "Some desc"
    session.get.assert_not_called()


def test_preserved_existing_repo_id_rate_limited_falls_back():
    """已有仓库路径抓取时触发 429:被 RateLimitError 捕获并回退,行为不变。"""
    session = _session_returning(429, headers={"Retry-After": "10"})
    card = get_zh_description(
        "openai/gpt-4o",
        "openai/gpt-4o",
        "GPT-4o multimodal model.",
        session,
        source="official",
    )
    assert card.source == "openrouter-description"
    assert card.text == "GPT-4o multimodal model."


def test_preserved_notify_renders_dash_when_both_descriptions_empty():
    """中英文简介均为空时,通知表格单元格继续显示 '-'(notify.py 渲染逻辑不变)。"""
    model = {
        "id": "acme/empty-desc",
        "name": "empty-desc",
        "architecture": {"modality": "text"},
        "context_length": 8192,
        "zh_description": "",
        "description": "",
    }
    msg = build_summary_message([model], [], [], 1, "2026-07-14T00:00:00")
    row = next(l for l in msg.splitlines() if l.startswith("|") and "acme/empty-desc" in l)
    cells = [c.strip() for c in row.split("|")]
    assert cells[-2] == "-"


# ---------------------------------------------------------------------------
# Integration tests: 混合批次 (batch_fetch_cards) 与通知渲染 (notify.py)
# ---------------------------------------------------------------------------


def test_batch_fetch_cards_mixed_batch_bug_and_non_bug_models():
    """混合批次:一个满足 bug 条件的模型(无 hugging_face_id,可从 model_id
    推断仓库路径)与一个不满足的模型(已有 hugging_face_id)同批处理。

    修复后,前者不再落入 source="none",能拿到真实抓取结果;后者结果与
    修复前一致;且前者从崩溃变为成功不影响后者的处理(逐模型隔离不变)。
    """
    items = [
        ("kwaipilot/kat-coder-air-v2.5", None, ""),
        (
            "meta-llama/llama-3.3-70b-instruct",
            "meta-llama/Llama-3.3-70B-Instruct",
            "Llama 3.3 model.",
        ),
    ]

    def fake_get(url, **kwargs):
        if "kwaipilot/kat-coder-air-v2.5" in url:
            return _FakeResp(200, ZH_README)
        if "meta-llama/Llama-3.3-70B-Instruct" in url:
            return _FakeResp(200, ZH_README)
        return _FakeResp(404)

    fake_session = mock.MagicMock()
    fake_session.get.side_effect = fake_get

    with mock.patch(
        "openrouter_checker.hf_card.create_retry_session", return_value=fake_session
    ):
        results = batch_fetch_cards(items, source="official", per_request_sleep=0)

    bug_card = results["kwaipilot/kat-coder-air-v2.5"]
    non_bug_card = results["meta-llama/llama-3.3-70b-instruct"]

    # 曾经因 bug 崩溃、被吞异常置空的模型:现在应拿到真实抓取结果
    assert bug_card.source == "hf-readme"
    assert bug_card.text != ""

    # 不满足 bug 条件的模型:结果与修复前一致(直接用已有 hugging_face_id 抓取)
    assert non_bug_card.source == "hf-readme"
    assert non_bug_card.text != ""

    # 两者互不影响:批次整体没有任何模型退化为空卡片
    assert all(card.source != "none" for card in results.values())


def test_notify_shows_real_zh_description_for_previously_affected_model():
    """此前受 bug 影响的模型(hf_repo_id 为空、可从 model_id 推断路径):
    修复后 zh_description 被正确填充,build_summary_message 应渲染出真实
    中文简介,而不是 '-'。与 Property 2 中"两者皆空 → '-'" 的用例互补。
    """
    session = _session_returning(200, ZH_README)
    card = get_zh_description(
        "kwaipilot/kat-coder-air-v2.5", None, "", session, source="official"
    )
    assert card.text  # 确认修复后确实拿到了非空中文简介

    model = {
        "id": "kwaipilot/kat-coder-air-v2.5",
        "name": "kat-coder-air-v2.5",
        "architecture": {"modality": "text"},
        "context_length": 128000,
        "zh_description": card.text,
        "description": "",
    }
    msg = build_summary_message([model], [], [], 1, "2026-07-14T00:00:00")
    row = next(
        l for l in msg.splitlines()
        if l.startswith("|") and "kwaipilot/kat-coder-air-v2.5" in l
    )
    cells = [c.strip() for c in row.split("|")]
    assert cells[-2] != "-"
    assert "快手" in cells[-2]
