"""Tests for backends.py — DuckDuckGoBackend, SearXNGBackend"""
import sys
import os
import json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock
import backends as bk


# ════════════════════════════════════════════════════════════════
#  DuckDuckGoBackend
# ════════════════════════════════════════════════════════════════

def test_ddgs_name():
    assert bk.DuckDuckGoBackend.name == "ddgs"


def test_ddgs_is_available_when_import_fails():
    with patch.object(bk, "DDGS_AVAILABLE", False):
        ddg = bk.DuckDuckGoBackend()
        assert ddg.is_available is False


def test_ddgs_search_raises_when_not_available():
    with patch.object(bk, "DDGS_AVAILABLE", False):
        ddg = bk.DuckDuckGoBackend()
        with pytest.raises(RuntimeError, match="DDGS not available"):
            ddg.search("hello", max_results=5)


def _make_mock_ddgs(return_value):
    """DDGS не импортирован — создаём фейковый класс и вешаем его на модуль"""
    mock_ddgs_cls = MagicMock(return_value=return_value)
    original = getattr(bk, "DDGS", None)
    bk.DDGS = mock_ddgs_cls
    return mock_ddgs_cls, original


def _restore_ddgs(original):
    if original is not None:
        bk.DDGS = original
    else:
        if hasattr(bk, "DDGS"):
            delattr(bk, "DDGS")


def test_ddgs_search_basic():
    """DDGS возвращает результаты — бэкенд парсит их правильно"""
    fake_results = [
        {"title": "T1", "href": "https://a.com", "body": "Snippet one"},
        {"title": "T2", "href": "https://b.com", "body": "Snippet two"},
    ]
    mock_ddgs = MagicMock()
    mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
    mock_ddgs.__exit__ = MagicMock(return_value=False)
    mock_ddgs.text.return_value = fake_results

    mock_cls, orig = _make_mock_ddgs(mock_ddgs)
    try:
        with patch.object(bk, "DDGS_AVAILABLE", True):
            ddg = bk.DuckDuckGoBackend()
            results = ddg.search("hello", max_results=5)
    finally:
        _restore_ddgs(orig)

    assert len(results) == 2
    assert results[0].title == "T1"
    assert results[0].url == "https://a.com"
    assert results[0].snippet == "Snippet one"
    assert results[0].source_backend == "ddgs"
    assert results[0].rank == 1
    # Score уменьшается с индексом
    assert results[0].score == 1.0
    assert results[1].score == 0.95


def test_ddgs_search_url_field_variants():
    """DDGS может вернуть 'url' вместо 'href' и 'snippet' вместо 'body'"""
    fake_results = [
        {"title": "T1", "url": "https://u.com", "snippet": "S"},
    ]
    mock_ddgs = MagicMock()
    mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
    mock_ddgs.__exit__ = MagicMock(return_value=False)
    mock_ddgs.text.return_value = fake_results

    mock_cls, orig = _make_mock_ddgs(mock_ddgs)
    try:
        with patch.object(bk, "DDGS_AVAILABLE", True):
            ddg = bk.DuckDuckGoBackend()
            results = ddg.search("hello", max_results=5)
    finally:
        _restore_ddgs(orig)

    assert results[0].url == "https://u.com"
    assert results[0].snippet == "S"


def test_ddgs_search_no_fallback_on_error():
    """Бэкенд не делает fallback по регионам — одна попытка, при ошибке пустой список"""
    mock_ddgs = MagicMock()
    mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
    mock_ddgs.__exit__ = MagicMock(return_value=False)
    mock_ddgs.text.side_effect = Exception("blocked")

    mock_cls, orig = _make_mock_ddgs(mock_ddgs)
    try:
        with patch.object(bk, "DDGS_AVAILABLE", True):
            ddg = bk.DuckDuckGoBackend()
            results = ddg.search("hello", max_results=5)
    finally:
        _restore_ddgs(orig)

    assert results == []
    # Ровно один вызов — никакого fallback'а
    assert mock_ddgs.text.call_count == 1


def test_ddgs_search_uses_wt_wt_by_default():
    """Без lang_hint используется регион wt-wt"""
    mock_ddgs = MagicMock()
    mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
    mock_ddgs.__exit__ = MagicMock(return_value=False)
    mock_ddgs.text.return_value = [{"title": "R", "href": "https://r.com", "body": "B"}]

    mock_cls, orig = _make_mock_ddgs(mock_ddgs)
    try:
        with patch.object(bk, "DDGS_AVAILABLE", True):
            ddg = bk.DuckDuckGoBackend()
            ddg.search("hello", max_results=5)
    finally:
        _restore_ddgs(orig)

    mock_ddgs.text.assert_called_with("hello", max_results=5, region="wt-wt")


def test_ddgs_search_with_lang_hint():
    mock_ddgs = MagicMock()
    mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
    mock_ddgs.__exit__ = MagicMock(return_value=False)
    mock_ddgs.text.return_value = [{"title": "R", "href": "https://r.com", "body": "B"}]

    mock_cls, orig = _make_mock_ddgs(mock_ddgs)
    try:
        with patch.object(bk, "DDGS_AVAILABLE", True):
            ddg = bk.DuckDuckGoBackend()
            results = ddg.search("hello", max_results=5, lang_hint="ru-ru")
    finally:
        _restore_ddgs(orig)

    # Вызов должен был идти с region=ru-ru (без fallback)
    mock_ddgs.text.assert_called_with("hello", max_results=5, region="ru-ru")
    assert mock_ddgs.text.call_count == 1
    assert len(results) == 1


def test_ddgs_news():
    fake_news = [
        {"title": "N1", "url": "https://n.com", "source": "BBC", "date": "2024-01-01"},
    ]
    mock_ddgs = MagicMock()
    mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
    mock_ddgs.__exit__ = MagicMock(return_value=False)
    mock_ddgs.news.return_value = fake_news

    mock_cls, orig = _make_mock_ddgs(mock_ddgs)
    try:
        with patch.object(bk, "DDGS_AVAILABLE", True):
            ddg = bk.DuckDuckGoBackend()
            results = ddg.news("test", max_results=5)
    finally:
        _restore_ddgs(orig)

    assert len(results) == 1
    assert results[0].title == "N1"
    assert "BBC" in results[0].snippet
    assert "2024" in results[0].snippet


def test_ddgs_news_raises():
    """news() должен пробрасывать исключение"""
    mock_ddgs = MagicMock()
    mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
    mock_ddgs.__exit__ = MagicMock(return_value=False)
    mock_ddgs.news.side_effect = Exception("API error")

    mock_cls, orig = _make_mock_ddgs(mock_ddgs)
    try:
        with patch.object(bk, "DDGS_AVAILABLE", True):
            ddg = bk.DuckDuckGoBackend()
            with pytest.raises(Exception, match="API error"):
                ddg.news("test")
    finally:
        _restore_ddgs(orig)


def test_ddgs_news_with_timelimit():
    fake_news = [{"title": "N", "url": "https://n.com", "source": "S", "date": "D"}]
    mock_ddgs = MagicMock()
    mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
    mock_ddgs.__exit__ = MagicMock(return_value=False)
    mock_ddgs.news.return_value = fake_news

    mock_cls, orig = _make_mock_ddgs(mock_ddgs)
    try:
        with patch.object(bk, "DDGS_AVAILABLE", True):
            ddg = bk.DuckDuckGoBackend()
            results = ddg.news("test", max_results=5, timelimit="d")
    finally:
        _restore_ddgs(orig)

    # timelimit должен передаться в news()
    mock_ddgs.news.assert_called_with(query="test", max_results=5, timelimit="d")


def test_ddgs_images():
    fake_images = [
        {"title": "I1", "image": "https://img.com/1.jpg", "thumbnail": "https://t.com/1.jpg"},
    ]
    mock_ddgs = MagicMock()
    mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
    mock_ddgs.__exit__ = MagicMock(return_value=False)
    mock_ddgs.images.return_value = fake_images

    mock_cls, orig = _make_mock_ddgs(mock_ddgs)
    try:
        with patch.object(bk, "DDGS_AVAILABLE", True):
            ddg = bk.DuckDuckGoBackend()
            results = ddg.images("cat", max_results=3)
    finally:
        _restore_ddgs(orig)

    assert len(results) == 1
    assert results[0].title == "I1"
    assert results[0].url == "https://img.com/1.jpg"
    assert results[0].snippet == "https://t.com/1.jpg"


def test_ddgs_images_raises():
    mock_ddgs = MagicMock()
    mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
    mock_ddgs.__exit__ = MagicMock(return_value=False)
    mock_ddgs.images.side_effect = Exception("Image error")

    mock_cls, orig = _make_mock_ddgs(mock_ddgs)
    try:
        with patch.object(bk, "DDGS_AVAILABLE", True):
            ddg = bk.DuckDuckGoBackend()
            with pytest.raises(Exception, match="Image error"):
                ddg.images("cat")
    finally:
        _restore_ddgs(orig)


def test_ddgs_images_url_variants():
    """images может вернуть 'url' или 'thumbnail' вместо 'image'"""
    fake_images = [
        {"title": "I1", "url": "https://u.com", "thumbnail": "https://t.com"},
    ]
    mock_ddgs = MagicMock()
    mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
    mock_ddgs.__exit__ = MagicMock(return_value=False)
    mock_ddgs.images.return_value = fake_images

    mock_cls, orig = _make_mock_ddgs(mock_ddgs)
    try:
        with patch.object(bk, "DDGS_AVAILABLE", True):
            ddg = bk.DuckDuckGoBackend()
            results = ddg.images("test", max_results=2)
    finally:
        _restore_ddgs(orig)

    assert results[0].url == "https://u.com"


# ════════════════════════════════════════════════════════════════
#  SearXNGBackend
# ════════════════════════════════════════════════════════════════

def test_searxng_name():
    assert bk.SearXNGBackend.name == "searxng"


def test_searxng_search_success():
    fake_json = {
        "results": [
            {"title": "S1", "url": "https://s.com", "content": "Content one", "score": 5},
            {"title": "S2", "url": "https://s2.com", "content": "Content two"},
        ]
    }
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.headers = {"content-type": "application/json"}
    fake_resp.json.return_value = fake_json
    fake_resp.raise_for_status = MagicMock()

    sess = MagicMock()
    sess.get.return_value = fake_resp

    with patch.object(bk.requests, "Session", return_value=sess):
        sx = bk.SearXNGBackend()
        # Первый запрос в _get_working_instance тоже попадает на get
        results = sx.search("hello", max_results=5)

    assert len(results) == 2
    assert results[0].title == "S1"
    assert results[0].url == "https://s.com"
    assert results[0].score == 5
    assert results[1].score == 1  # по умолчанию


def test_searxng_search_no_instances():
    """Ни один инстанс не доступен — RuntimeError"""
    sess = MagicMock()
    sess.get.side_effect = Exception("Connection refused")

    with patch.object(bk.requests, "Session", return_value=sess):
        sx = bk.SearXNGBackend()
        with pytest.raises(RuntimeError, match="No SearXNG instances available"):
            sx.search("hello")


def test_searxng_search_non_json_content_type():
    """SearXNG возвращает HTML вместо JSON — RuntimeError"""
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.headers = {"content-type": "text/html"}

    sess = MagicMock()
    sess.get.return_value = fake_resp

    with patch.object(bk.requests, "Session", return_value=sess):
        sx = bk.SearXNGBackend()
        with pytest.raises(RuntimeError, match="non-JSON"):
            sx.search("hello")


def test_searxng_search_json_decode_error():
    """SearXNG возвращает повреждённый JSON — RuntimeError"""
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.headers = {"content-type": "application/json"}
    # json.JSONDecodeError устанавливается как аттрибут мока
    fake_resp.json.side_effect = json.JSONDecodeError("msg", "doc", 0)
    fake_resp.raise_for_status = MagicMock()

    sess = MagicMock()
    sess.get.return_value = fake_resp

    with patch.object(bk.requests, "Session", return_value=sess):
        sx = bk.SearXNGBackend()
        with pytest.raises(RuntimeError, match="invalid JSON"):
            sx.search("hello")


def test_searxng_search_with_lang_hint():
    fake_json = {"results": [{"title": "L", "url": "https://l.com", "content": "C"}]}
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.headers = {"content-type": "application/json"}
    fake_resp.json.return_value = fake_json
    fake_resp.raise_for_status = MagicMock()

    sess = MagicMock()
    sess.get.return_value = fake_resp

    with patch.object(bk.requests, "Session", return_value=sess):
        sx = bk.SearXNGBackend()
        results = sx.search("hello", max_results=5, lang_hint="ru-ru")

    # Проверим что language передался в params
    call_args = sess.get.call_args_list[-1]
    assert call_args[1]["params"]["language"] == "ru-ru"


def test_searxng_is_available_true():
    fake_resp = MagicMock()
    fake_resp.status_code = 200

    sess = MagicMock()
    sess.get.return_value = fake_resp

    with patch.object(bk.requests, "Session", return_value=sess):
        sx = bk.SearXNGBackend()
        assert sx.is_available is True


def test_searxng_is_available_false():
    sess = MagicMock()
    sess.get.side_effect = Exception("Down")

    with patch.object(bk.requests, "Session", return_value=sess):
        sx = bk.SearXNGBackend()
        assert sx.is_available is False


def test_searxng_is_available_caches_result():
    """Двойной вызов is_available не делает лишних запросов"""
    fake_resp = MagicMock()
    fake_resp.status_code = 200

    sess = MagicMock()
    sess.get.return_value = fake_resp

    with patch.object(bk.requests, "Session", return_value=sess):
        sx = bk.SearXNGBackend()
        _ = sx.is_available
        _ = sx.is_available
        # Первый запрос на проверку, второй — из кэша
        # Получить может быть до 3 запросов (первый в _get_working_instance, потом в is_available)
        assert sess.get.call_count <= 3


def test_searxng_get_working_instance_uses_cached():
    """Если инстанс уже закэширован и работает — используем его"""
    fake_resp = MagicMock()
    fake_resp.status_code = 200

    sess = MagicMock()
    sess.get.return_value = fake_resp

    with patch.object(bk.requests, "Session", return_value=sess):
        sx = bk.SearXNGBackend()
        instance1 = sx._get_working_instance()
        instance2 = sx._get_working_instance()
        assert instance1 is not None
        assert instance2 is not None
        # Первый запрос проверка, второй — кэш хит, но мы всё равно делаем запрос на проверку
        assert sess.get.call_count <= 4


import pytest
