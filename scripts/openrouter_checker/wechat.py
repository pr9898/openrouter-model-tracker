"""企业微信 Webhook 推送。"""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from .config import DEFAULT_TIMEOUT

logger = logging.getLogger("openrouter_checker.wechat")

WECHAT_SAFE_BYTES = 4000
PYTHON_RETRY_COUNT = 3

_BASE = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"


def _resolve_webhook_url(webhook_key: str) -> str:
    """兼容多种输入形态,返回可用的完整 Webhook URL。

    - 完整 URL(含 qyapi.weixin.qq.com):直接复用
    - 含 key= 的字符串:提取 key 拼回标准地址
    - 纯 key:拼成标准地址
    """
    if not webhook_key:
        return ""
    raw = webhook_key.strip()
    # 已是完整 URL
    if "qyapi.weixin.qq.com" in raw:
        return raw
    # 形如 https://.../send?key=xxxx 或 ...?key=xxxx
    if "key=" in raw:
        try:
            qs = parse_qs(urlparse(raw).query)
            key = qs.get("key", [None])[0]
            if key:
                return f"{_BASE}?key={key}"
        except Exception:
            pass
    # 纯 key
    return f"{_BASE}?key={raw}"


def _check_wechat_response(resp_data: dict[str, Any]) -> bool:
    """验证企业微信 API 业务层响应是否成功。"""
    errcode = resp_data.get("errcode", -1)
    if errcode != 0:
        errmsg = resp_data.get("errmsg", "未知错误")
        logger.warning("[wechat] API 业务错误: errcode=%d, errmsg=%s", errcode, errmsg)
        return False
    return True


def send_wechat_message(
    session: requests.Session, webhook_key: str, content: str
) -> bool:
    """发送单条 markdown_v2 消息到企业微信。返回是否成功。"""
    url = _resolve_webhook_url(webhook_key)
    if not url:
        logger.warning("[wechat] 缺少 WECHAT_WEBHOOK_KEY,跳过通知")
        return False

    payload = {"msgtype": "markdown_v2", "markdown_v2": {"content": content}}

    for attempt in range(PYTHON_RETRY_COUNT):
        try:
            resp = session.post(url, json=payload, timeout=DEFAULT_TIMEOUT)
        except requests.exceptions.RequestException as e:
            logger.warning("[wechat] 通知发送异常: %s", e)
            if attempt < PYTHON_RETRY_COUNT - 1:
                time.sleep(2)
            continue
        if resp.status_code == 200:
            try:
                resp_data = resp.json()
                if _check_wechat_response(resp_data):
                    return True
            except ValueError:
                logger.warning("[wechat] 通知响应非 JSON: %s", resp.text[:200])
        else:
            logger.warning(
                "[wechat] 通知发送失败 (HTTP %d): %s",
                resp.status_code,
                resp.text[:200],
            )
        if attempt < PYTHON_RETRY_COUNT - 1:
            time.sleep(2)

    logger.error("[wechat] 通知发送失败(%d 次重试耗尽)", PYTHON_RETRY_COUNT)
    return False
