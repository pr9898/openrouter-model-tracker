"""HuggingFace 模型卡抓取与中文介绍提取。

策略:
1. 从 OpenRouter model.id 解析 org/name(优先 model.hugging_face_id)
2. GET https://huggingface.co/{repo_id}/raw/main/README.md
3. 剥离 YAML frontmatter,按段落扫描中文描述
4. 命中中文 → 缓存;否则 fallback 到 OpenRouter 英文 description
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Literal

from .api import RateLimitError, create_retry_session, fetch_hf_readme

logger = logging.getLogger("openrouter_checker.hf_card")

_ZH_CHAR_RE = re.compile(r"[一-鿿]")
_LATIN_CHAR_RE = re.compile(r"[A-Za-z]")
_YAML_FENCE_RE = re.compile(r"^---\s*$", re.MULTILINE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_SPLIT_PARA_RE = re.compile(r"\n\s*\n")

MAX_ZH_CHARS = 300
MIN_ZH_CHARS = 30
SAMPLE_CHARS = 5000

# 单 worker 完成后 sleep,控制请求节奏(5 worker × 0.2s ≈ 1 req/s)
_PER_REQUEST_SLEEP = 0.2
_DEFAULT_MAX_WORKERS = 5


@dataclass
class HfCard:
    """一次中文介绍抓取的结果。"""

    text: str
    source: Literal["hf-readme", "hf-readme-no-zh", "openrouter-description", "none"]
    language: Literal["zh", "en", "mixed", "unknown"]
    fetched_at: str


def parse_openrouter_id(model_id: str) -> tuple[str, str] | None:
    """如 'meta-llama/llama-3.3-70b-instruct' → ('meta-llama', 'llama-3.3-70b-instruct')。

    'openai/gpt-4o-mini:free' → ('openai', 'gpt-4o-mini:free')
    无法构造 repo_id(无斜杠)返回 None。
    """
    if "/" not in model_id:
        return None
    org, _, name = model_id.partition("/")
    return org, name


def _strip_yaml_frontmatter(text: str) -> str:
    """剥掉开头的 YAML frontmatter(--- ... ---)。"""
    matches = list(_YAML_FENCE_RE.finditer(text))
    if len(matches) >= 2:
        start = matches[0].start()
        end = matches[1].end()
        if start == 0 or text[:start].strip() == "":
            return text[end:].lstrip("\n")
    return text


def detect_language(text: str) -> Literal["zh", "en", "mixed", "unknown"]:
    """统计前 SAMPLE_CHARS 字符的中文字符占比判定语言。"""
    sample = text[:SAMPLE_CHARS]
    if not sample.strip():
        return "unknown"
    zh = len(_ZH_CHAR_RE.findall(sample))
    latin = len(_LATIN_CHAR_RE.findall(sample))
    total = zh + latin
    if total < 100:
        # 字符太少,看绝对中文数
        return "zh" if zh >= 5 else "unknown"
    ratio = zh / total
    if ratio > 0.3:
        return "zh"
    if latin > 50 and ratio < 0.05:
        return "en"
    if zh > 0:
        return "mixed"
    return "en"


def extract_zh_description(
    readme: str,
    *,
    max_chars: int = MAX_ZH_CHARS,
    min_zh_chars: int = MIN_ZH_CHARS,
) -> str:
    """从 README 提取第一段含中文的描述。

    1) 剥离 YAML frontmatter
    2) 去 HTML 标签 / Markdown 链接
    3) 按双换行分段,扫描每段(长度 < 30 或中文 < min_zh_chars 跳过)
    4) 截断到 max_chars,在最近标点断句
    """
    body = _strip_yaml_frontmatter(readme)
    paragraphs = [p.strip() for p in _SPLIT_PARA_RE.split(body)]
    # 过滤掉纯标题块(单行且以 # 开头)
    paragraphs = [p for p in paragraphs if not (len(p.splitlines()) == 1 and p.startswith("#"))]

    for para in paragraphs:
        clean = _HTML_TAG_RE.sub("", para)
        clean = _MARKDOWN_LINK_RE.sub(r"\1", clean)
        clean = clean.strip()
        if len(clean) < 30:
            continue
        if len(_ZH_CHAR_RE.findall(clean)) < min_zh_chars:
            continue
        # 截断
        if len(clean) > max_chars:
            clean = clean[:max_chars]
            # 在最近标点处断句
            for cut in (".", "。", "，", ",", "；", ";", "、"):
                idx = clean.rfind(cut)
                if idx > max_chars * 0.6:
                    clean = clean[: idx + 1]
                    break
            else:
                clean = clean.rstrip() + "…"
        return clean

    return ""


def get_zh_description(
    model_id: str,
    hf_repo_id: str | None,
    openrouter_description: str,
    session: Any,
    *,
    hf_token: str | None = None,
    now_iso: str = "",
    source: str | None = "official",
) -> HfCard:
    """主入口:抓取并提取中文介绍,带 fallback。

    fallback 链:hf-readme → openrouter-description → none
    ``source`` 指定 HF 源("official"/"hf-mirror"/None 跳过联网)。
    """
    fetched_at = now_iso or time.strftime("%Y-%m-%dT%H:%M:%S")

    # 没有 repo_id 或跳过联网,直接用 OpenRouter description
    if source is None or not hf_repo_id:
        repo = parse_openrouter_id(model_id)
        resolved = hf_repo_id or ("/".join(repo) if repo else None)
        if not resolved:
            text = (openrouter_description or "").strip()
            return HfCard(
                text=text,
                source="openrouter-description" if text else "none",
                language="en" if text else "unknown",
                fetched_at=fetched_at,
            )
        if source is None:
            text = (openrouter_description or "").strip()
            return HfCard(
                text=text,
                source="openrouter-description" if text else "none",
                language="en" if text else "unknown",
                fetched_at=fetched_at,
            )

    # 到达此处:source 非 None 且 hf_repo_id 非空(否则上方已 return)
    assert hf_repo_id is not None
    try:
        readme = fetch_hf_readme(
            session, hf_repo_id, hf_token=hf_token, source=source
        )
    except RateLimitError as e:
        logger.warning("[hf_card] %s 频率限制,fallback 到 OR 描述: %s", model_id, e)
        text = (openrouter_description or "").strip()
        return HfCard(
            text=text,
            source="openrouter-description" if text else "none",
            language="en" if text else "unknown",
            fetched_at=fetched_at,
        )

    if not readme:
        text = (openrouter_description or "").strip()
        return HfCard(
            text=text,
            source="openrouter-description" if text else "none",
            language="en" if text else "unknown",
            fetched_at=fetched_at,
        )

    lang = detect_language(readme)
    zh_text = extract_zh_description(readme)
    if zh_text:
        return HfCard(text=zh_text, source="hf-readme", language=lang, fetched_at=fetched_at)

    # README 存在但无中文 → fallback 到 OR 英文描述,但标记为 hf-readme-no-zh
    # (README 确实抓到了,只是没中文段落),以便上层据此长期缓存,避免每次重抓
    text = (openrouter_description or "").strip()
    return HfCard(
        text=text,
        source="hf-readme-no-zh" if text else "none",
        language=lang,
        fetched_at=fetched_at,
    )


def batch_fetch_cards(
    items: list[tuple[str, str | None, str]],
    *,
    max_workers: int = _DEFAULT_MAX_WORKERS,
    per_request_sleep: float = _PER_REQUEST_SLEEP,
    hf_token: str | None = None,
    timeout_per_req: int = 15,
    source: str | None = "official",
) -> dict[str, HfCard]:
    """并发抓取中文介绍,信号量限流。

    ``items``: [(model_id, hf_repo_id, openrouter_description), ...]
    ``source``: HF 源("official"/"hf-mirror"/None 跳过联网)。
    返回 {model_id: HfCard}。单个失败不影响整体。
    """
    session = create_retry_session()
    results: dict[str, HfCard] = {}
    rate_limited_until = 0.0

    def _worker(item: tuple[str, str | None, str]) -> tuple[str, HfCard]:
        model_id, hf_repo_id, desc = item
        nonlocal rate_limited_until
        # 全局 429 暂停
        if rate_limited_until > time.time():
            sleep = rate_limited_until - time.time()
            if sleep > 0:
                time.sleep(sleep)
        card = get_zh_description(
            model_id, hf_repo_id, desc, session,
            hf_token=hf_token, source=source,
        )
        time.sleep(per_request_sleep)
        return model_id, card

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_worker, it): it[0] for it in items}
        done = 0
        for future in as_completed(futures):
            model_id = futures[future]
            done += 1
            try:
                mid, card = future.result()
                results[mid] = card
            except Exception as e:  # noqa: BLE001
                logger.debug("[hf_card] %s 抓取失败: %s", model_id, e)
                results[model_id] = HfCard(
                    text="", source="none", language="unknown",
                    fetched_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                )
            if done % 25 == 0:
                logger.info("[hf_card] 进度 %d/%d", done, len(items))

    return results
