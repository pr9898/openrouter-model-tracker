---
license: llama3.3
language:
  - en
---

# Llama 3.3 70B Instruct

The Llama 3.3 multilingual large language model (LLM) is a pretrained and instruction tuned generative model. This 70B model is designed for text generation tasks and is optimized for multilingual dialogue use cases.

Llama 3.3 70B outperforms many available open source and closed chat models on common industry benchmarks, and is particularly useful for enterprise use cases.

## Model details

- Parameters: 70B
- Context length: 128K tokens
- Supported languages: English, German, French, Italian, Portuguese, Hindi, Spanish, Thai

## Usage

```python
from transformers import AutoModelForCausalLM
model = AutoModelForCausalLM.from_pretrained("meta-llama/Llama-3.3-70B-Instruct")
```
