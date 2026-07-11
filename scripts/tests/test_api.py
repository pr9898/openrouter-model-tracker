"""HF 源连通性探测与镜像回退测试(不联网)。"""

import unittest.mock as mock

from openrouter_checker.api import check_hf_reachable, fetch_hf_readme
from openrouter_checker.hf_card import get_zh_description


class _FakeResp:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text


def test_check_hf_reachable_official_ok():
    req = __import__("requests")
    sess = mock.MagicMock()
    sess.get.return_value = _FakeResp(404)  # 404 也说明网络通
    src, desc = check_hf_reachable(sess)
    assert src == "official"


def test_check_hf_reachable_fallback_to_mirror():
    req = __import__("requests")

    def fake_get(url, **kw):
        if "hf-mirror" not in url:
            raise req.exceptions.ConnectionError("timeout")
        return _FakeResp(200)

    sess = mock.MagicMock()
    sess.get.side_effect = fake_get
    src, desc = check_hf_reachable(sess)
    assert src == "hf-mirror"


def test_check_hf_reachable_none_when_all_down():
    req = __import__("requests")
    sess = mock.MagicMock()
    sess.get.side_effect = req.exceptions.ConnectionError("down")
    src, desc = check_hf_reachable(sess)
    assert src is None
    assert "不可达" in desc


def test_fetch_hf_readme_uses_mirror_source():
    req = __import__("requests")
    sess = mock.MagicMock()
    captured = {}

    def fake_get(url, **kw):
        captured["url"] = url
        return _FakeResp(200)

    sess.get.side_effect = fake_get
    text = fetch_hf_readme(sess, "Qwen/Qwen3", source="hf-mirror")
    assert text is not None
    assert "hf-mirror.com" in captured["url"]


def test_fetch_hf_readme_skips_when_source_none():
    sess = mock.MagicMock()
    result = fetch_hf_readme(sess, "Qwen/Qwen3", source=None)
    assert result is None
    sess.get.assert_not_called()


def test_get_zh_description_skips_network_when_source_none():
    req = __import__("requests")
    sess = mock.MagicMock()
    card = get_zh_description(
        "openai/gpt-4o", None, "GPT-4o model.", sess, source=None
    )
    assert card.source == "openrouter-description"
    assert "GPT-4o" in card.text
    sess.get.assert_not_called()
