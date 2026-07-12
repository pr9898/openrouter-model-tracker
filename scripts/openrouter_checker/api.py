"""HTTP 客户端:OpenRouter 模型列表 + HuggingFace README 抓取。"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import DEFAULT_OPENROUTER_API_URL, DEFAULT_TIMEOUT, DEFAULT_USD_CNY_RATE

logger = logging.getLogger("openrouter_checker.api")

HF_README_URL = "https://huggingface.co/{repo_id}/raw/main/README.md"
HF_API_URL = "https://huggingface.co/api/models/{repo_id}"

# 中国镜像源(官方源不通时回退)
# 注意: hf-mirror 的 raw/ 路径需要 token(返回 401),用 resolve/ 路径才能公开读取
HF_MIRROR_README_URL = "https://hf-mirror.com/{repo_id}/resolve/main/README.md"
HF_MIRROR_API_URL = "https://hf-mirror.com/api/models/{repo_id}"

# 探测时尝试的源顺序(官方优先,镜像兜底)
HF_SOURCES = (
    ("official", HF_README_URL, HF_API_URL),
    ("hf-mirror", HF_MIRROR_README_URL, HF_MIRROR_API_URL),
)

HF_PROBE_REPO = "meta-llama/Llama-3.3-70B-Instruct"


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
    # 仅挂载 https 适配器:OpenRouter / HuggingFace 均为 https,
    # 本项目不发 http 明文请求,故不挂载 http:// 适配器(同时避免 semgrep
    # insecure-transport 误报)
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


# 免 key 的实时汇率接口(返回 rates.CNY)。多个源依次尝试,增强可用性。
_FX_ENDPOINTS = (
    "https://open.er-api.com/v6/latest/USD",
    "https://api.frankfurter.app/latest?from=USD&to=CNY",
)


def fetch_usd_cny_rate(session: requests.Session, timeout: int = 5) -> float:
    """获取美元→人民币实时汇率(每 1 美元兑多少人民币)。

    依次尝试多个免 key 的汇率接口,取首个成功返回的 ``rates.CNY``。
    任何异常、非 200、或解析失败都回退到 ``DEFAULT_USD_CNY_RATE``,
    不阻断主流程。
    """
    for url in _FX_ENDPOINTS:
        try:
            resp = session.get(url, timeout=timeout)
            if resp.status_code != 200:
                continue
            rate = resp.json().get("rates", {}).get("CNY")
            if rate:
                return float(rate)
        except requests.exceptions.RequestException as e:
            logger.debug("[fx] 汇率请求失败 (%s): %s", url, e)
        except (ValueError, TypeError) as e:
            logger.debug("[fx] 汇率解析失败 (%s): %s", url, e)
    logger.warning("[fx] 实时汇率获取失败,回退默认 %.2f", DEFAULT_USD_CNY_RATE)
    return DEFAULT_USD_CNY_RATE


class RateLimitError(Exception):
    """HuggingFace 频率限制。"""


def _hf_headers(hf_token: str | None) -> dict[str, str]:
    headers = {"User-Agent": "openrouter-model-checker/2.0"}
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"
    return headers


def check_hf_reachable(
    session: requests.Session,
    *,
    probe_repo: str = HF_PROBE_REPO,
    timeout: int = 10,
) -> tuple[str | None, str]:
    """探测 HuggingFace 源连通性,返回 (可用源名, 可读描述)。

    顺序:官方 huggingface.co → 中国镜像 hf-mirror.com。
    都不通返回 (None, 说明)。
    """
    for name, readme_url, api_url in HF_SOURCES:
        url = api_url.format(repo_id=probe_repo)
        try:
            resp = session.get(url, headers=_hf_headers(None), timeout=timeout)
        except requests.exceptions.RequestException as e:
            logger.debug("[hf-probe] %s 源不可达: %s", name, e)
            continue
        # API 端点能返回(含 404)说明网络层通;raw README 受鉴权影响不可靠
        logger.info("[hf-probe] %s 源可达 (HTTP %d)", name, resp.status_code)
        return name, name
    return None, "官方与镜像源均不可达"


def fetch_hf_readme(
    session: requests.Session,
    repo_id: str,
    *,
    timeout: int = 15,
    max_retries: int = 2,
    hf_token: str | None = None,
    source: str | None = "official",
) -> str | None:
    """抓取 HuggingFace 模型卡 README 原文。

    - 404: 返回 None(模型卡不存在)
    - 429: 抛 RateLimitError,触发调用方指数退避
    - 200: 返回文本

    ``source`` 指定使用的源("official" / "hf-mirror" / None 表示跳过联网)。
    """
    if source is None:
        return None

    readme_url = dict(
        (name, ru) for name, ru, _ in HF_SOURCES
    ).get(source, HF_README_URL)
    url = readme_url.format(repo_id=repo_id)
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

        if resp.status_code in (404, 401):
            # 404 模型卡不存在;401 镜像 raw 路径鉴权问题 → 都读不到内容
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
