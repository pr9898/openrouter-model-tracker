"""格式化辅助:中文模态、上下文长度、表格单元格清理。"""

from __future__ import annotations

from typing import Any

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
    """格式化价格(每百万 token 美元),0 显示为免费。"""
    try:
        price = float(value)
    except (TypeError, ValueError):
        return "-"
    if price <= 0:
        return "免费"
    return f"${price:.6g}"


def get_nested(data: dict, dotted_path: str, default: Any = None) -> Any:
    """按点路径取嵌套字段,如 'architecture.modality'。"""
    cur: Any = data
    for part in dotted_path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur
