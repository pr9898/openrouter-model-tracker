"""格式化函数测试。"""

from openrouter_checker.formatting import (
    format_context_length,
    format_modality_chinese,
    format_price,
    get_nested,
    sanitize_table_cell,
)


def test_format_modality_plain():
    assert format_modality_chinese("text") == "文本"


def test_format_modality_io():
    assert format_modality_chinese("text+image->text") == "文本+图像→文本"


def test_format_modality_unknown():
    assert format_modality_chinese("unknown") == "未知"
    assert format_modality_chinese("") == "未知"


def test_format_context_length_k():
    assert format_context_length(128000) == "128K"
    assert format_context_length(200000) == "200K"


def test_format_context_length_m():
    assert format_context_length(1_000_000) == "1M"
    assert format_context_length(1_048_576) == "1M"


def test_format_context_length_invalid():
    assert format_context_length(0) == "-"
    assert format_context_length("abc") == "-"


def test_format_price():
    assert format_price(0) == "免费"
    assert format_price("0") == "免费"
    assert format_price(0.000003) == "$3e-06" or "$0.000003" in format_price(0.000003)


def test_sanitize_table_cell():
    assert sanitize_table_cell("a|b") == "a／b"
    assert sanitize_table_cell("") == "-"
    assert sanitize_table_cell(None) == "None"


def test_get_nested():
    d = {"a": {"b": {"c": 1}}}
    assert get_nested(d, "a.b.c") == 1
    assert get_nested(d, "a.x.y", "default") == "default"
