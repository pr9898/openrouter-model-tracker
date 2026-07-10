"""HTTP 客户端:OpenRouter 模型列表 + HuggingFace README 抓取。"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import DEFAULT_OPENROUTER_API_URL, DEFAULT_TIMEOUT

logger = logging.getLogger("openrouter_checker.api")

HF_README_URL = "https://huggingface.co/{repo_id}/raw/main/README.md"
HF_API_URL = "https://huggingface.co/api/models/{repo_id}"


class CheckerError(Exception):
    """基础异常,所有致命错误使用此类。"""

    def __init__(self, message: str, exit_code: int = 2):
        super().__init__(message)
        self.exit_code = exit_code


def create_retry_session() -> requests.Session:
    """创建预配置了传输层重试策略的 requests.Session。"""
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[502, 503, 504],
        respect_retry_after_header=True,
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def fetch_openrouter_models(
    session: requests.Session,
    api_url: str,
    api_key: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[dict[str, Any]]:
    """从 OpenRouter 获取全部模型列表。"""
    url = f"{api_url.rstrip('/')}/models"
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    logger.info("[fetch_openrouter] 查询模型列表...")
    try:
        resp = session.get(url, headers=headers or None, timeout=timeout)
    except requests.exceptions.Timeout:
        raise CheckerError(f"OpenRouter API 连接超时 ({timeout}s)")
    except requests.exceptions.ConnectionError:
        raise CheckerError("OpenRouter API 连接失败,请检查网络")
    except requests.exceptions.RequestException as e:
        raise CheckerError(f"OpenRouter API 请求失败: {e}")

    if resp.status_code == 401:
        raise CheckerError("OpenRouter API 认证失败 (401),请检查 OPENROUTER_API_KEY")
    if resp.status_code == 403:
        raise CheckerError("OpenRouter API 权限不足 (403),请检查 OPENROUTER_API_KEY")
    if resp.status_code == 429:
        raise CheckerError("OpenRouter API 触发频率限制 (429),请稍后重试")
    if resp.status_code != 200:
        raise CheckerError(
            f"OpenRouter API 请求失败 (HTTP {resp.status_code}): {resp.text[:200]}"
        )

    try:
        data = resp.json()
    except ValueError:
        raise CheckerError("OpenRouter API 返回非 JSON 响应")

    if "data" not in data:
        raise CheckerError("OpenRouter API 响应格式异常,缺少 'data' 字段")

    models = []
    for item in data["data"]:
        model_id = item.get("id", "")
        if not model_id:
            continue
        models.append(dict(item))

    logger.info("[fetch_openrouter] 获取 %d 个模型", len(models))
    return models


class RateLimitError(Exception):
    """HuggingFace 频率限制。"""


def _hf_headers(hf_token: str | None) -> dict[str, str]:
    headers = {"User-Agent": "openrouter-model-checker/2.0"}
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"
    return headers


def fetch_hf_readme(
    session: requests.Session,
    repo_id: str,
    *,
    timeout: int = 15,
    max_retries: int = 2,
    hf_token: str | None = None,
) -> str | None:
    """抓取 HuggingFace 模型卡 README 原文。

    - 404: 返回 None(模型卡不存在)
    - 429: 抛 RateLimitError,触发调用方指数退避
    - 200: 返回文本
    """
    url = HF_README_URL.format(repo_id=repo_id)
    headers = _hf_headers(hf_token)

    for attempt in range(max_retries + 1):
        try:
            resp = session.get(url, headers=headers, timeout=timeout)
        except requests.exceptions.RequestException as e:
            logger.debug("[hf] %s 请求异常: %s", repo_id, e)
            if attempt < max_retries:
                time.sleep(2 * (attempt + 1))
                continue
            return None

        if resp.status_code == 404:
            return None
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "10"))
            raise RateLimitError(f"HF 429 for {repo_id}, Retry-After={retry_after}")
        if resp.status_code != 200:
            logger.debug("[hf] %s 非 200: %d", repo_id, resp.status_code)
            return None
        return resp.text

    return None


def fetch_hf_model_card_path(
    session: requests.Session,
    repo_id: str,
    *,
    timeout: int = 15,
    hf_token: str | None = None,
) -> str | None:
    """通过 HF API 查询模型卡实际路径(如 README 在 main 分支以外的分支)。

    返回如 "README.md" 或 "model_card.md";不存在返回 None。
    """
    url = HF_API_URL.format(repo_id=repo_id)
    headers = _hf_headers(hf_token)
    try:
        resp = session.get(url, headers=headers, timeout=timeout)
    except requests.exceptions.RequestException:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    # HF API 在 model 对象上暴露 cardData / 文件列表有限;简单返回默认 README.md
    return "README.md"
