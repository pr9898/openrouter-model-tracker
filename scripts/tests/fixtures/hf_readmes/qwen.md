---
license: apache-2.0
language:
- zh
- en
---

# 通义千问3-235B-A22B

通义千问3（Qwen3）是阿里巴巴集团于2025年4月29日开源的新一代大语言模型系列。Qwen3-235B-A22B 是一个拥有2350亿总参数、220亿激活参数的稀疏专家混合（MoE）模型，在保持较小推理开销的同时提供旗舰级的性能。

该模型支持长上下文理解、工具调用与代码生成，并原生支持thinking模式，可在深刻推理与快速响应之间灵活切换。预训练语料覆盖多语言，中文能力显著增强。

## 模型细节

- 参数规模：235B 总参数 / 22B 激活参数
- 上下文长度：128K tokens
- 支持语言：中、英、法、西、德、日、韩等多语言

## 使用示例

```python
from transformers import AutoModelForCausalLM
model = AutoModelForCausalLM.from_pretrained("Qwen/Qwen3-235B-A22B")
```
