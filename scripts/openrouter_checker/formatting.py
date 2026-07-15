"""格式化辅助:中文模态、上下文长度、表格单元格清理。"""

from __future__ import annotations

import math
from typing import Any

from .config import DEFAULT_USD_CNY_RATE

_MODALITY_LABELS = {
    "text": "文本",
    "image": "图像",
    "file": "文件",
    "audio": "音频",
    "video": "视频",
}


def _translate_part(part: str) -> str:
    return "+".join(_MODALITY_LABELS.get(token, token) for token in part.split("+"))


def format_modality_chinese(modality: str) -> str:
    """将 OpenRouter modality 转为中文,如 text+image->text → 文本+图像→文本。"""
    if not modality or modality == "unknown":
        return "未知"

    if "->" not in modality:
        return _translate_part(modality)
    inputs, outputs = modality.split("->", 1)
    return f"{_translate_part(inputs)}→{_translate_part(outputs)}"


def format_context_length(value: Any) -> str:
    """将上下文长度格式化为 1M、200K 等可读形式。"""
    try:
        length = int(value)
    except (TypeError, ValueError):
        return "-"
    if length <= 0:
        return "-"
    if length >= 1_000_000:
        millions = round(length / 1_000_000, 1)
        if millions == int(millions):
            return f"{int(millions)}M"
        return f"{millions}M"
    if length >= 1_000:
        thousands = round(length / 1_000, 1)
        if thousands == int(thousands):
            return f"{int(thousands)}K"
        return f"{thousands}K"
    return str(length)


def sanitize_table_cell(value: Any) -> str:
    """清理表格单元格内容,避免破坏 Markdown 表格语法。"""
    text = str(value).replace("\n", " ").replace("|", "／").strip()
    return text or "-"


def format_price(value: Any) -> str:
    """格式化价格(每百万 token 美元),0 显示为免费,避免科学计数法。

    OpenRouter 的价格为「每 token 美元」(极小,如 8e-07),直接格式化会得到
    ``$8e-07`` 这种科学计数法。这里统一换算成「每百万 token」并用普通小数展示,
    与新增模型的价格列保持一致(8e-07/token → $0.8)。
    """
    try:
        price = float(value)
    except (TypeError, ValueError):
        return "-"
    if price <= 0:
        return "免费"
    return f"＄{_fmt_usd(price * 1_000_000)}"


def _per_million_usd(value: Any) -> float | None:
    """OpenRouter 价格为每 token 美元(字符串),转成每百万 token 美元。

    非法 / 缺失 / ≤0 返回 None。
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return v * 1_000_000


def _fmt_usd(per_million: float) -> str:
    """每百万 token 美元的定点表示:避免科学计数法,自适应小数位并去尾零。

    例:``0.8`` → ``"0.8"``,``1.25`` → ``"1.25"``,``10000`` → ``"10000"``,
    ``8e-07``/token 换算的 ``0.8`` → ``"0.8"``(不会变成 ``8e-07``)。
    """
    if per_million == 0:
        return "0"
    magnitude = math.floor(math.log10(abs(per_million)))
    decimals = max(0, min(12, 4 - magnitude))
    s = f"{per_million:.{decimals}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def format_price_both(
    pricing: Any,
    usd_cny_rate: float = DEFAULT_USD_CNY_RATE,
) -> str:
    """价格列:输入/输出 每百万 token 的 美元 + 人民币。

    ``pricing`` 为 OpenRouter 的 ``pricing`` 对象(含 prompt / completion 等,
    每 token 美元字符串)。两侧均缺失或免费 → ``免费``;否则分别格式化,
    例: ``入 $1.25·¥8.98 出 $10·¥71.80``(¥ = 每百万美元 × 汇率)。
    """
    if not isinstance(pricing, dict):
        return "-"
    prompt = _per_million_usd(pricing.get("prompt"))
    completion = _per_million_usd(pricing.get("completion"))

    segments: list[str] = []
    if prompt is not None:
        segments.append(f"入 ＄{_fmt_usd(prompt)}·¥{prompt * usd_cny_rate:.2f}")
    if completion is not None:
        segments.append(f"出 ＄{_fmt_usd(completion)}·¥{completion * usd_cny_rate:.2f}")

    if not segments:
        return "免费"
    return " ".join(segments)


def get_nested(data: dict, dotted_path: str, default: Any = None) -> Any:
    """按点路径取嵌套字段,如 'architecture.modality'。"""
    cur: Any = data
    for part in dotted_path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur
