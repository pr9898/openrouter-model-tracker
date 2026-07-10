"""HF 中文提取离线测试(不联网)。"""

from openrouter_checker.hf_card import (
    detect_language,
    extract_zh_description,
    parse_openrouter_id,
)
from openrouter_checker.storage import needs_zh_refresh

QWEN_README = """---
license: apache-2.0
---

# 通义千问3-235B-A22B

通义千问3（Qwen3）是阿里巴巴集团于2025年开源的新一代大语言模型系列。Qwen3-235B-A22B 是一个拥有2350亿总参数、220亿激活参数的稀疏专家混合模型。

该模型支持长上下文理解与工具调用。

## 使用示例

```python
model = AutoModel.from_pretrained("Qwen/Qwen3-235B-A22B")
```
"""

LLAMA_README = """---
license: llama3.3
---

# Llama 3.3 70B Instruct

The Llama 3.3 multilingual large language model is a pretrained and instruction tuned generative model for text generation.

This 70B model is optimized for multilingual dialogue use cases.

## Usage

```python
model = AutoModel.from_pretrained("meta-llama/Llama-3.3-70B-Instruct")
```
"""


def test_parse_openrouter_id():
    assert parse_openrouter_id("meta-llama/llama-3.3-70b") == ("meta-llama", "llama-3.3-70b")
    assert parse_openrouter_id("openai/gpt-4o:free") == ("openai", "gpt-4o:free")
    assert parse_openrouter_id("gpt-4o") is None


def test_detect_language_zh():
    assert detect_language(QWEN_README) == "zh"


def test_detect_language_en():
    assert detect_language(LLAMA_README) == "en"


def test_extract_zh_from_qwen():
    text = extract_zh_description(QWEN_README)
    assert "通义千问" in text
    assert len(text) <= 300
    # 不应包含代码块
    assert "```" not in text


def test_extract_zh_from_llama_fallback_empty():
    text = extract_zh_description(LLAMA_README)
    assert text == ""


def test_needs_zh_refresh():
    assert needs_zh_refresh({}, 0, force=False) is True
    entry = {"zh_description": "x", "zh_fetched_at": "2026-07-09T00:00:00"}
    # 1 天前抓取,未超 30 天 → 不需刷新
    import time
    now = time.time()
    assert needs_zh_refresh(entry, now) is False
    # force
    assert needs_zh_refresh(entry, now, force=True) is True
