"""LLM 翻译:把英文模型描述翻译成中文简介。

调 OpenRouter chat completions(默认用免费模型),将英文 description /
README 首段翻译成简短中文介绍。无 API key 或调用失败时优雅降级。

设计要点:
- 翻译结果不缓存进 zh_description 时,上层会回退英文,所以这里失败不影响主流程
- 限流:并发抓取时每个 worker 串行调一次翻译,由 batch_fetch_cards 控制并发
- prompt 约束输出为「一句话中文介绍」,避免长篇
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import requests

from .config import DEFAULT_TIMEOUT

logger = logging.getLogger("openrouter_checker.translate")

_DEFAULT_MODEL = "tencent/hy3:free"
_CHAT_PATH = "/chat/completions"

# 翻译结果最长字符数(与 extract_zh_description 的 MAX_ZH_CHARS 对齐)
_MAX_ZH_CHARS = 300


@dataclass
class TranslateConfig:
    """LLM 翻译配置。api_key 为空时禁用翻译。"""

    api_url: str
    api_key: str
    model: str = _DEFAULT_MODEL
    enabled: bool = True


def _build_prompt(text: str, model_name: str) -> list[dict[str, str]]:
    """构造翻译 prompt,约束输出为简短中文一句话介绍。"""
    sys_msg = (
        "你是一个模型卡片翻译助手。把用户给定的英文模型描述翻译成简洁的中文介绍,"
        "要求:1) 一段话,不超过 80 字;2) 保留模型名、参数量、核心能力等关键信息;"
        "3) 只输出翻译结果,不要解释、不要引号、不要前后缀。"
    )
    user_msg = f"模型:{model_name}\n描述:{text[:1000]}"
    return [
        {"role": "system", "content": sys_msg},
        {"role": "user", "content": user_msg},
    ]


def _clean_translation(text: str) -> str:
    """清理翻译输出:去引号、首尾空白、多余换行,截断到合理长度。"""
    if not text:
        return ""
    cleaned = text.strip().strip('"\'""''').strip()
    # 折叠内部换行/多空格为单空格
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) > _MAX_ZH_CHARS:
        cleaned = cleaned[:_MAX_ZH_CHARS]
        # 在最近标点断句
        for cut in ("。", "，", ",", "；", ";"):
            idx = cleaned.rfind(cut)
            if idx > _MAX_ZH_CHARS * 0.6:
                cleaned = cleaned[: idx + 1]
                break
        else:
            cleaned = cleaned.rstrip() + "…"
    return cleaned


def translate_to_zh(
    session: requests.Session,
    text: str,
    model_name: str,
    *,
    api_url: str,
    api_key: str,
    translate_model: str = _DEFAULT_MODEL,
    timeout: int = DEFAULT_TIMEOUT,
) -> str | None:
    """把英文 text 翻译成中文,返回清理后的中文介绍。

    无 api_key、无 text、或调用失败时返回 None(由上层降级)。
    """
    if not text or not api_key:
        return None

    url = f"{api_url.rstrip('/')}{_CHAT_PATH}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": translate_model,
        "messages": _build_prompt(text, model_name),
        "temperature": 0.3,
        # 推理模型(如 tencent/hy3:free)会先在 reasoning 字段思考,
        # 需给足 token 让思考完成并输出正文 content
        "max_tokens": 2000,
    }

    try:
        resp = session.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.exceptions.RequestException as e:
        logger.debug("[translate] 请求异常: %s", e)
        return None

    if resp.status_code != 200:
        logger.debug(
            "[translate] 翻译失败 (HTTP %d): %s",
            resp.status_code,
            resp.text[:200],
        )
        return None

    try:
        data = resp.json()
        msg = data["choices"][0]["message"]
        content = msg.get("content")
    except (ValueError, KeyError, IndexError, TypeError) as e:
        logger.debug("[translate] 响应解析失败: %s", e)
        return None

    cleaned = _clean_translation(content)
    # 若翻译结果几乎不含中文(模型没按要求翻),视为失败
    if not re.search(r"[一-鿿]", cleaned):
        logger.debug("[translate] 翻译结果无中文,丢弃: %s", cleaned[:80])
        return None
    return cleaned or None
