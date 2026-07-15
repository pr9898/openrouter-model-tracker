"""三段式企业微信通知消息构建与智能分片。"""

from __future__ import annotations

from datetime import datetime, timezone

from .diff import ModelChange, important_changes
from .formatting import (
    format_context_length,
    format_modality_chinese,
    format_price,
    format_price_both,
    get_nested,
    sanitize_table_cell,
)
from .config import DEFAULT_USD_CNY_RATE
from .wechat import WECHAT_SAFE_BYTES, send_wechat_message

HEADER_MARKER = "# 🆕 OpenRouter 模型变动"
MAX_ROWS_PER_SECTION = 10
UTC_TZ = timezone.utc


def _beijing_time(utc_time: str) -> str:
    """把 UTC 时间戳字符串转成北京时间字符串(UTC+8)。"""
    try:
        dt = datetime.fromisoformat(utc_time)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC_TZ)
        bj = dt.astimezone(timezone(offset=__import__("datetime").timedelta(hours=8)))
        return bj.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return utc_time


def _model_zh(model: dict) -> str:
    """取模型的中文介绍。

    优先缓存的 ``zh_description``;若为空(翻译失败/无中文/未抓取),
    回退到 OpenRouter 英文 ``description``,避免显示 ``-``。
    """
    zh = model.get("zh_description")
    if zh:
        return zh
    return model.get("description") or ""


def _change_zh(
    model_id: str,
    known_models: dict,
    new_data: dict | None,
    old_data: dict | None,
) -> str:
    """取变更模型的简介。

    优先 ``known_models`` 中缓存的 ``zh_description``(与模型条目同级,
    非 ``data`` 内);缓存为空则回退新/旧数据里的英文 ``description``。
    """
    zh = (known_models.get(model_id) or {}).get("zh_description")
    if zh:
        return zh
    data = new_data or old_data or {}
    return data.get("description") or ""


def build_summary_message(
    new_models: list[dict],
    removed_ids: list[str],
    changes: list[ModelChange],
    total_models: int,
    current_time: str,
    known: dict | None = None,
    *,
    max_new_rows: int = MAX_ROWS_PER_SECTION,
    max_change_rows: int = MAX_ROWS_PER_SECTION,
    max_removed_rows: int = MAX_ROWS_PER_SECTION,
    usd_cny_rate: float = DEFAULT_USD_CNY_RATE,
) -> str:
    """构建三段式(新增 / 下线 / 重要变更)通知内容。"""
    known_models = (known or {}).get("models", {})
    important = important_changes(changes)

    bj = _beijing_time(current_time)
    parts = [
        HEADER_MARKER,
        f"> 🕐 检测时间: {bj} (北京时间)",
        f"> 📊 OpenRouter 共 **{total_models}** 个模型 | "
        f"新增 **{len(new_models)}** | 下线 **{len(removed_ids)}** | 变更 **{len(important)}**",
        "",
    ]

    # 新增段
    if new_models:
        parts.append("## ✨ 新增模型")
        parts.append("")
        parts.append("| 🤖 名称 | 🆔 ID | 🔀 模态 | 📏 上下文 | 💰 价格 | 📝 简介 |")
        parts.append("| --- | --- | --- | --- | --- | --- |")
        for m in new_models[:max_new_rows]:
            arch = get_nested(m, "architecture", {}) or {}
            modality = arch.get("modality", "unknown") if isinstance(arch, dict) else "unknown"
            zh = _model_zh(m)
            price = format_price_both(m.get("pricing"), usd_cny_rate)
            parts.append(
                "| "
                + " | ".join(
                    [
                        sanitize_table_cell(m.get("name", m["id"])),
                        sanitize_table_cell(m["id"]),
                        sanitize_table_cell(format_modality_chinese(modality)),
                        sanitize_table_cell(format_context_length(m.get("context_length", 0))),
                        sanitize_table_cell(price),
                        sanitize_table_cell(zh[:120] if zh else "-"),
                    ]
                )
                + " |"
            )
        if len(new_models) > max_new_rows:
            parts.append(f"📋 还有 **{len(new_models) - max_new_rows}** 个新模型,见日志")
        parts.append("")

    # 下线段
    if removed_ids:
        parts.append("## 🔻 下线模型")
        for mid in removed_ids[:max_removed_rows]:
            first_seen = known_models.get(mid, {}).get("first_seen", "未知")
            parts.append(f"- `{sanitize_table_cell(mid)}` (首次发现: {first_seen})")
        if len(removed_ids) > max_removed_rows:
            parts.append(f"📋 还有 **{len(removed_ids) - max_removed_rows}** 个下线模型,见日志")
        parts.append("")

    # 重要变更段
    if important:
        parts.append("## ⚡ 重要变更")
        parts.append("")
        parts.append("| 🆔 ID | 字段 | 旧 | 新 | 📝 简介 |")
        parts.append("| --- | --- | --- | --- | --- |")
        for c in important[:max_change_rows]:
            # 取第一个 critical/major diff 展示
            d = c.field_diffs[0]
            if d.field.startswith("pricing."):
                old_s, new_s = format_price(d.old), format_price(d.new)
            else:
                old_s, new_s = str(d.old), str(d.new)
            zh = _change_zh(c.model_id, known_models, c.new_data, c.old_data)
            parts.append(
                "| "
                + " | ".join(
                    [
                        sanitize_table_cell(c.model_id),
                        sanitize_table_cell(d.field),
                        sanitize_table_cell(old_s),
                        sanitize_table_cell(new_s),
                        sanitize_table_cell(zh[:120] if zh else "-"),
                    ]
                )
                + " |"
            )
        if len(important) > max_change_rows:
            parts.append(f"📋 还有 **{len(important) - max_change_rows}** 个变更,见日志")
        parts.append("")

    if not (new_models or removed_ids or important):
        parts.append("✅ 本次无新增 / 下线 / 重要变更")

    return "\n".join(parts)


def _split_into_sections(content: str) -> list[list[str]]:
    """按 '## ' 标题把消息切分为 [preamble_lines, section_lines, ...]。"""
    lines = content.split("\n")
    preamble: list[str] = []
    sections: list[list[str]] = []
    current: list[str] | None = None

    for line in lines:
        if line.startswith("## "):
            if current is not None:
                sections.append(current)
            current = [line]
        elif current is None:
            preamble.append(line)
        else:
            current.append(line)
    if current is not None:
        sections.append(current)

    return [preamble, *sections]


def split_message_by_sections(content: str, max_bytes: int = WECHAT_SAFE_BYTES) -> list[str]:
    """按段落分片,避开 4000 字节限制。

    先按 '## ' 标题切,单 section 超长再按表格行切。每条消息附 (i/N) 标记。
    """
    if len(content.encode("utf-8")) <= max_bytes:
        return [content]

    wrapped = content.replace(HEADER_MARKER, f"{HEADER_MARKER} (1/1)")
    blocks = _split_into_sections(wrapped)
    preamble = blocks[0]

    messages: list[str] = []
    for section in blocks[1:]:
        section_text = "\n".join(preamble + section)
        if len(section_text.encode("utf-8")) <= max_bytes:
            messages.append(section_text)
            continue
        # 单 section 超长,按表格行切
        title = section[0]
        body_lines = section[1:]
        # 保留表头行
        header_line = next((l for l in body_lines if "---" in l and l.strip().startswith("|")), None)
        data_rows = [l for l in body_lines if l.strip().startswith("|") and "---" not in l]
        other_lines = [
            l for l in body_lines
            if not (l.strip().startswith("|") and "---" not in l) and l != header_line
        ]
        chunk: list[str] = [title]
        if header_line:
            chunk.append(header_line)
        chunk.extend(other_lines)

        cur_rows: list[str] = []
        for row in data_rows:
            candidate = "\n".join(preamble + chunk + cur_rows + [row])
            if len(candidate.encode("utf-8")) > max_bytes and cur_rows:
                messages.append("\n".join(preamble + chunk + cur_rows))
                cur_rows = [row]
            else:
                cur_rows.append(row)
        if cur_rows:
            messages.append("\n".join(preamble + chunk + cur_rows))

    if not messages:
        return [content]

    # 标记 (i/N)
    total = len(messages)
    out = []
    for i, msg in enumerate(messages, 1):
        if total > 1:
            msg = msg.replace(HEADER_MARKER, f"{HEADER_MARKER} ({i}/{total})")
        out.append(msg)
    return out


def send_notifications(
    session: requests.Session,
    webhook_key: str,
    new_models: list[dict],
    removed_ids: list[str],
    changes: list[ModelChange],
    total_models: int,
    current_time: str,
    known: dict | None = None,
    *,
    usd_cny_rate: float = DEFAULT_USD_CNY_RATE,
) -> bool:
    """构建并发送三段式通知。无内容或无 webhook key 时跳过。"""
    if not webhook_key:
        logger_no_key()
        return True
    content = build_summary_message(
        new_models, removed_ids, changes, total_models, current_time, known,
        usd_cny_rate=usd_cny_rate,
    )
    messages = split_message_by_sections(content)
    success = True
    for i, msg in enumerate(messages):
        if len(messages) > 1:
            msg = msg.replace(HEADER_MARKER, f"{HEADER_MARKER} ({i + 1}/{len(messages)})")
        if not send_wechat_message(session, webhook_key, msg):
            success = False
    return success


def logger_no_key() -> None:
    import logging

    logging.getLogger("openrouter_checker.notify").warning(
        "[notify] 缺少 WECHAT_WEBHOOK_KEY,跳过通知"
    )
