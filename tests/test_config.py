"""Tests for config.py — SearchConfig, initialize_hf_mirror"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock
import config as config_module


# ═══════════════════════════════════════════════════════════════
#  SearchConfig
# ═══════════════════════════════════════════════════════════════

def test_search_config_defaults():
    from config import SearchConfig
    c = SearchConfig()
    assert c.REQUEST_TIMEOUT == 15
    assert c.MAX_REDIRECTS == 5
    assert c.MAX_CONTENT_LENGTH == 15000
    assert c.MAX_CONTENT_SIZE == 3 * 1024 * 1024
    assert c.MAX_CONCURRENT == 8
    assert c.MAX_SEARCH_RESULTS == 12
    assert c.DEFAULT_MAX_RESULTS == 8
    assert c.CACHE_TTL_SECONDS == 3600
    assert c.CACHE_MAX_SIZE == 1000
    assert c.ENABLE_NEURAL_RERANK is True
    assert c.NEURAL_MODEL_NAME == "ms-marco-MultiBERT-L-12"
    assert c.TOP_K_RERANK == 20
    assert c.ENABLE_DIVERSITY is True
    assert c.DIVERSITY_LAMBDA == 0.7
    assert c.RATE_LIMIT_REQUESTS == 30


def test_search_config_custom():
    from config import SearchConfig
    c = SearchConfig(REQUEST_TIMEOUT=30, DEFAULT_MAX_RESULTS=20, ENABLE_NEURAL_RERANK=False)
    assert c.REQUEST_TIMEOUT == 30
    assert c.DEFAULT_MAX_RESULTS == 20
    assert c.ENABLE_NEURAL_RERANK is False
    # Остальные поля по умолчанию
    assert c.MAX_REDIRECTS == 5


def test_config_singleton_exists():
    from config import config
    assert isinstance(config, config_module.SearchConfig)
    assert config.MAX_SEARCH_RESULTS == 12


# ═══════════════════════════════════════════════════════════════
#  initialize_hf_mirror
# ═══════════════════════════════════════════════════════════════

def _reset_mirror():
    """Сброс глобального состояния зеркала"""
    config_module.SELECTED_MIRROR = None
    os.environ.pop("HF_ENDPOINT", None)


def test_initialize_hf_mirror_first_success():
    _reset_mirror()
    resp = MagicMock()
    resp.status = 200
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=resp):
        config_module.initialize_hf_mirror()

    assert config_module.SELECTED_MIRROR == "https://hf-mirror.com"
    assert os.environ.get("HF_ENDPOINT") == "https://hf-mirror.com"


def test_initialize_hf_mirror_fallback_to_second():
    _reset_mirror()
    """ÐÐµÑÐ²Ð¾Ðµ Ð·ÐµÑÐºÐ°Ð»Ð¾ Ð¿Ð°Ð´Ð°ÐµÑ, Ð²ÑÐ¾ÑÐ¾Ðµ ÑÐ°Ð±Ð¾ÑÐ°ÐµÑ"""
    def side_effect(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "hf-mirror" in url:
            raise Exception("Connection refused")
        resp = MagicMock()
        resp.status = 200
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    with patch("urllib.request.urlopen", side_effect=side_effect):
        config_module.initialize_hf_mirror()

    assert config_module.SELECTED_MIRROR == "https://huggingface.co"
    assert os.environ.get("HF_ENDPOINT") == "https://huggingface.co"


def test_initialize_hf_mirror_all_fail():
    _reset_mirror()
    with patch("urllib.request.urlopen", side_effect=Exception("No internet")):
        config_module.initialize_hf_mirror()

    # ÐÐ¾Ð»Ð¶Ð½Ð¾ ÑÐ¾Ð»Ð±ÐµÐºÐ½ÑÑÑ Ð½Ð° huggingface.co
    assert config_module.SELECTED_MIRROR == "https://huggingface.co"
    assert os.environ.get("HF_ENDPOINT") == "https://huggingface.co"


def test_initialize_hf_mirror_skips_when_already_set():
    _reset_mirror()
    config_module.SELECTED_MIRROR = "https://already.set"
    os.environ["HF_ENDPOINT"] = "https://already.set"

    with patch("urllib.request.urlopen") as mock_urlopen:
        config_module.initialize_hf_mirror()
        mock_urlopen.assert_not_called()

    assert config_module.SELECTED_MIRROR == "https://already.set"


def test_initialize_hf_mirror_sets_user_agent():
    _reset_mirror()
    captured = {}

    def capture(req, timeout=None):
        captured["ua"] = req.get_header("User-agent")
        resp = MagicMock()
        resp.status = 200
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    with patch("urllib.request.urlopen", side_effect=capture):
        config_module.initialize_hf_mirror()

    assert "MCP-SearchTool" in captured.get("ua", "")
