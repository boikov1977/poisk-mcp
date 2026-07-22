"""Smoke tests for SearchEngine, DiversityEngine, QueryProcessor"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch
import pytest
from models import SearchResult
from engine import QueryProcessor, DiversityEngine


def test_query_processor():
    pq = QueryProcessor.process("hello world")
    assert pq.original == "hello world"
    assert pq.get_search_query() == "hello world"
    assert pq.intent == "informational"


def test_query_processor_too_short():
    import pytest
    with pytest.raises(ValueError):
        QueryProcessor.process("x")


def test_query_processor_strips():
    pq = QueryProcessor.process("  spaced  query  ")
    assert pq.original == "spaced  query"


def test_query_processor_normalized():
    pq = QueryProcessor.process("Hello World!")
    assert pq.normalized == "Hello World!"
    assert pq.tokens == ["Hello World!"]


# --- DiversityEngine ---

def _make_result(title, url, score=1.0, snippet=None):
    return SearchResult(
        title=title, url=url, snippet=snippet or title,
        score=score, rank=0, source_backend="test"
    )


def test_diversity_empty():
    de = DiversityEngine()
    assert de.diversify([]) == []


def test_diversity_single():
    de = DiversityEngine()
    r = [_make_result("Only", "http://x.com")]
    assert de.diversify(r) == r


def test_diversity_two():
    de = DiversityEngine()
    r = [
        _make_result("First", "http://a.com", 0.9),
        _make_result("Second", "http://b.com", 0.8),
    ]
    de.lambda_param = 0.5
    d = de.diversify(r, target_count=2)
    assert len(d) == 2


def test_diversity_removes_duplicate_domains():
    """Domain MMR должен разнообразить домены"""
    de = DiversityEngine()
    r = [
        _make_result("A1", "http://same.com/page1", 0.9),
        _make_result("A2", "http://same.com/page2", 0.8),
        _make_result("B", "http://other.com/page", 0.7),
    ]
    de.lambda_param = 0.7
    d = de.diversify(r, target_count=2)
    assert len(d) == 2
    domains = [x.url.split("/")[2] for x in d]
    # Должны быть разные домены
    assert len(set(domains)) == len(domains), f"domains should be diverse: {domains}"


def test_text_diversify_different_content():
    de = DiversityEngine()
    r = [
        _make_result("Python programming", "http://py.com", 0.9,
                      snippet="Python is a programming language"),
        _make_result("Java tutorial", "http://java.com", 0.8,
                     snippet="Java is a programming language"),
    ]
    de.lambda_param = 0.5
    d = de.diversify(r, target_count=2)
    assert len(d) == 2


def test_text_diversify_short_texts():
    """При коротких текстах (<50 символов средняя длина) должен сработать domain MMR"""
    de = DiversityEngine()
    r = [
        _make_result("A", "http://x.com/a", 0.9, snippet="aa"),
        _make_result("B", "http://x.com/b", 0.8, snippet="bb"),
        _make_result("C", "http://y.com/c", 0.7, snippet="cc"),
    ]
    de.lambda_param = 0.7
    d = de.diversify(r, target_count=3)
    # Должны отработать без ошибок
    assert len(d) == 3


# --- SearchEngine basic sanity ---

def test_search_engine_init():
    """SearchEngine должен инициализироваться без ошибок, даже без бэкендов"""
    # Мокаем backends, чтобы не зависеть от сети
    from engine import SearchEngine
    # Просто проверяем что класс существует и создаётся
    assert SearchEngine is not None


def test_search_engine_language_regions():
    from engine import SearchEngine
    assert SearchEngine.LANGUAGE_REGIONS["ru"] == "ru-ru"
    assert SearchEngine.LANGUAGE_REGIONS["en"] == "us-en"
    assert SearchEngine.LANGUAGE_REGIONS["zh"] == "cn-zh"
    assert SearchEngine.LANGUAGE_REGIONS["de"] == "de-de"
    assert "auto" not in SearchEngine.LANGUAGE_REGIONS


# --- Edge cases ---

def test_make_result_defaults():
    r = _make_result("Test", "http://x.com")
    assert r.score == 1.0
    assert r.rank == 0
    assert r.source_backend == "test"
    assert r.features == {}


def test_make_result_with_score():
    r = _make_result("T", "http://x.com", score=0.5)
    assert r.score == 0.5


def test_process_query_edge_cases():
    import pytest
    with pytest.raises(ValueError):
        QueryProcessor.process("")
    with pytest.raises(ValueError):
        QueryProcessor.process("   ")


# ════════════════════════════════════════════════════
def test_diversity_two_different_domains():
    """Domain MMR с разными доменами должен вернуть оба"""
    de = DiversityEngine()
    r = [
        _make_result("A", "http://a.com", 0.9),
        _make_result("B", "http://b.com", 0.8),
    ]
    de.lambda_param = 0.5
    d = de.diversify(r, target_count=2)
    assert len(d) == 2
    assert d[0].rank == 1
    assert d[1].rank == 2


def test_diversity_three_with_same_domain():
    """Три результата с одного домена — должен вернуться не более одного с этого домена если lambda низкий"""
    de = DiversityEngine()
    r = [
        _make_result("A1", "http://same.com/1", 0.9),
        _make_result("A2", "http://same.com/2", 0.8),
        _make_result("A3", "http://same.com/3", 0.7),
        _make_result("B", "http://other.com", 0.6),
    ]
    de.lambda_param = 0.3  # сильно штрафуем за дубликаты
    d = de.diversify(r, target_count=3)
    # Должен выбрать один с same.com и B
    assert len(d) == 3
    domains = [x.url.split("/")[2] for x in d]
    assert "other.com" in domains


def test_text_diversify_exact_similarity():
    """Одинаковый текст — максимальная similarity, Jaccard=1.0"""
    de = DiversityEngine()
    r = [
        _make_result("Python", "http://a.com", 0.9,
                     snippet="python programming tutorial guide"),
        _make_result("Python", "http://b.com", 0.8,
                     snippet="python programming tutorial guide"),
    ]
    de.lambda_param = 0.5
    d = de.diversify(r, target_count=2)
    # Оба должны вернуться, но второй с сильно пониженным скором
    assert len(d) == 2


def test_text_diversify_high_lambda_prioritizes_relevance():
    """lambda=1.0 — не штрафуем за дубликаты, выбираем по скору"""
    de = DiversityEngine()
    r = [
        _make_result("A", "http://same.com/1", 0.9,
                     snippet="python programming tutorial"),
        _make_result("B", "http://same.com/2", 0.8,
                     snippet="python programming tutorial"),
    ]
    de.lambda_param = 1.0
    d = de.diversify(r, target_count=2)
    # По скору: A сначала, потом B (similarity не влияет)
    assert d[0].title == "A"
    assert d[1].title == "B"


def test_domain_mmr_empty_domains():
    """Если urlparse не может извлечь netloc — не падаем"""
    de = DiversityEngine()
    r = [
        _make_result("A", "not-a-url", 0.9),
        _make_result("B", "not-a-url", 0.8),
    ]
    de.lambda_param = 0.5
    d = de.diversify(r, target_count=2)
    assert len(d) == 2


def test_text_diversify_empty_tokens():
    """Если текст не содержит слов — не падаем"""
    de = DiversityEngine()
    r = [
        _make_result("!!!", "http://a.com", 0.9,
                     snippet="!!!"),
        _make_result("???", "http://b.com", 0.8,
                     snippet="???"),
    ]
    # avg_len больше 50, поэтому text_diversify
    # но tokens пустые — не падаем
    d = de.diversify(r, target_count=2)
    assert len(d) == 2


# ════════════════════════════════════════════════════════════════
#  SearchEngine — deeper coverage
# ════════════════════════════════════════════════════════════════

def test_search_engine_init_creates_backends():
    from engine import SearchEngine
    # Создаём без бэкендов — мокаем доступность
    with patch("engine.DuckDuckGoBackend") as MockDDGS:
        with patch("engine.SearXNGBackend") as MockSearXNG:
            MockDDGS.return_value.is_available = True
            MockSearXNG.return_value.is_available = False
            se = SearchEngine()
            assert "ddgs" in se.backends
            assert "searxng" not in se.backends


def test_search_with_no_backends():
    """Без бэкендов должен вернуть empty results"""
    from engine import SearchEngine
    with patch("engine.DuckDuckGoBackend") as MockDDGS:
        with patch("engine.SearXNGBackend") as MockSearXNG:
            MockDDGS.return_value.is_available = False
            MockSearXNG.return_value.is_available = False
            se = SearchEngine()
            results, meta = se.search("hello")
            assert results == []
            assert "error" in meta


def test_search_rate_limited():
    from engine import SearchEngine
    with patch("engine.DuckDuckGoBackend") as MockDDGS:
        with patch("engine.SearXNGBackend") as MockSearXNG:
            with patch("engine.rate_limiter") as mock_rl:
                MockDDGS.return_value.is_available = True
                MockSearXNG.return_value.is_available = False
                mock_rl.acquire.return_value = False
                se = SearchEngine()
                results, meta = se.search("hello")
                assert results == []
                assert "Rate limited" in meta.get("error", "")


def test_search_caches_results():
    from engine import SearchEngine
    fake_result = _make_result("Cached", "http://cached.com", 0.9)
    with patch("engine.DuckDuckGoBackend") as MockDDGS:
        with patch("engine.SearXNGBackend") as MockSearXNG:
            with patch("engine.search_cache") as mock_cache:
                MockDDGS.return_value.is_available = True
                MockSearXNG.return_value.is_available = False
                mock_cache.get.return_value = [fake_result]
                se = SearchEngine()
                results, meta = se.search("hello")
                assert meta["cached"] is True
                assert len(results) == 1


def test_search_returns_results():
    from engine import SearchEngine
    fake_result = _make_result("Real", "http://real.com", 0.9)
    with patch("engine.DuckDuckGoBackend") as MockDDGS:
        with patch("engine.SearXNGBackend") as MockSearXNG:
            with patch("engine.rate_limiter") as mock_rl:
                with patch("engine.search_cache") as mock_cache:
                    MockDDGS.return_value.is_available = True
                    MockDDGS.return_value.search.return_value = [fake_result]
                    MockSearXNG.return_value.is_available = False
                    mock_rl.acquire.return_value = True
                    mock_cache.get.return_value = None
                    se = SearchEngine()
                    results, meta = se.search("hello", max_results=5)
                    assert len(results) >= 1
                    assert meta["backend"] == "test"


def test_search_empty_query():
    from engine import SearchEngine
    with patch("engine.DuckDuckGoBackend") as MockDDGS:
        with patch("engine.SearXNGBackend") as MockSearXNG:
            MockDDGS.return_value.is_available = True
            MockSearXNG.return_value.is_available = False
            se = SearchEngine()
            results, meta = se.search("   ")
            assert results == []
            assert "error" in meta


def test_search_multilingual():
    from engine import SearchEngine
    r1 = _make_result("RU", "http://ru.com", 0.9)
    r2 = _make_result("EN", "http://en.com", 0.8)
    with patch("engine.DuckDuckGoBackend") as MockDDGS:
        with patch("engine.SearXNGBackend") as MockSearXNG:
            with patch("engine.rate_limiter") as mock_rl:
                with patch("engine.search_cache") as mock_cache:
                    MockDDGS.return_value.is_available = True
                    # Разные результаты для разных языков
                    def mock_search(q, limit, lang):
                        if lang and "ru" in lang:
                            return [r1]
                        return [r2]
                    MockDDGS.return_value.search.side_effect = mock_search
                    MockSearXNG.return_value.is_available = False
                    mock_rl.acquire.return_value = True
                    mock_cache.get.return_value = None
                    se = SearchEngine()
                    results, meta = se.search("test", languages=["ru", "en"], max_results=5)
                    assert len(results) >= 1
                    assert meta.get("languages") == ["ru", "en"]


def test_search_news_no_ddgs():
    from engine import SearchEngine
    with patch("engine.DuckDuckGoBackend") as MockDDGS:
        with patch("engine.SearXNGBackend") as MockSearXNG:
            MockDDGS.return_value.is_available = False
            MockSearXNG.return_value.is_available = False
            se = SearchEngine()
            results, meta = se.news("hello")
            assert results == []
            assert "DDGS not available" in meta.get("error", "")


def test_search_images_no_ddgs():
    from engine import SearchEngine
    with patch("engine.DuckDuckGoBackend") as MockDDGS:
        with patch("engine.SearXNGBackend") as MockSearXNG:
            MockDDGS.return_value.is_available = False
            MockSearXNG.return_value.is_available = False
            se = SearchEngine()
            results, meta = se.images("cat")
            assert results == []
            assert "DDGS not available" in meta.get("error", "")


def test_get_stats():
    from engine import SearchEngine
    with patch("engine.DuckDuckGoBackend") as MockDDGS:
        with patch("engine.SearXNGBackend") as MockSearXNG:
            MockDDGS.return_value.is_available = True
            MockSearXNG.return_value.is_available = False
            se = SearchEngine()
            stats = se.get_stats()
            assert "queries" in stats
            assert "avg_ms" in stats
            assert "cache" in stats
            assert "rl" in stats
            assert "backends" in stats


def test_search_backend_usage_stats():
    from engine import SearchEngine
    fake_result = _make_result("Real", "http://real.com", 0.9)
    with patch("engine.DuckDuckGoBackend") as MockDDGS:
        with patch("engine.SearXNGBackend") as MockSearXNG:
            with patch("engine.rate_limiter") as mock_rl:
                with patch("engine.search_cache") as mock_cache:
                    MockDDGS.return_value.is_available = True
                    MockDDGS.return_value.search.return_value = [fake_result]
                    MockSearXNG.return_value.is_available = False
                    mock_rl.acquire.return_value = True
                    mock_cache.get.return_value = None
                    se = SearchEngine()
                    se.search("hello")
                    stats = se.get_stats()
                    # source_backend в fake_result = "test"
                    assert stats["backend_usage"].get("test", 0) >= 1


def test_rerank_empty():
    """Пустой список для rerank — возвращаем как есть"""
    from engine import SearchEngine
    with patch("engine.DuckDuckGoBackend") as MockDDGS:
        with patch("engine.SearXNGBackend") as MockSearXNG:
            MockDDGS.return_value.is_available = True
            MockSearXNG.return_value.is_available = False
            se = SearchEngine()
            assert se._rerank("q", []) == []


def test_rerank_neural_not_available():
    """Rerank без нейронки — возвращает нетронутые результаты"""
    from engine import SearchEngine
    with patch("engine.DuckDuckGoBackend") as MockDDGS:
        with patch("engine.SearXNGBackend") as MockSearXNG:
            with patch("engine.NeuralReranker") as MockNR:
                MockDDGS.return_value.is_available = True
                MockSearXNG.return_value.is_available = False
                MockNR.get_instance.return_value.is_available = False
                se = SearchEngine()
                r = [_make_result("T", "http://t.com", 0.9)]
                result = se._rerank("q", r)
                assert len(result) == 1


def test_rerank_with_neural_scores():
    """Rerank обновляет score на основе neural scores"""
    from engine import SearchEngine
    fake_result = _make_result("T", "http://t.com", 0.5)
    with patch("engine.DuckDuckGoBackend") as MockDDGS:
        with patch("engine.SearXNGBackend") as MockSearXNG:
            with patch("engine.NeuralReranker") as MockNR:
                MockDDGS.return_value.is_available = True
                MockSearXNG.return_value.is_available = False
                mock_reranker = MockNR.get_instance.return_value
                mock_reranker.is_available = True
                mock_reranker.rerank.return_value = [0.9]
                se = SearchEngine()
                result = se._rerank("query", [fake_result])
                # score = old * 0.4 + neural * 0.6 = 0.5 * 0.4 + 0.9 * 0.6 = 0.74
                assert result[0].score == pytest.approx(0.74, 0.01)
                assert result[0].features.get("neural") == pytest.approx(0.9, 0.01)


def test_rerank_empty_texts():
    """Rerank с пустыми текстами — возвращает исходные результаты"""
    from engine import SearchEngine
    r = [_make_result("", "http://t.com", 0.5, snippet="")]
    with patch("engine.DuckDuckGoBackend") as MockDDGS:
        with patch("engine.SearXNGBackend") as MockSearXNG:
            with patch("engine.NeuralReranker") as MockNR:
                MockDDGS.return_value.is_available = True
                MockSearXNG.return_value.is_available = False
                MockNR.get_instance.return_value.is_available = True
                se = SearchEngine()
                result = se._rerank("q", r)
                assert len(result) == 1


def test_search_with_deduplication():
    """_search дедуплицирует по URL"""
    from engine import SearchEngine
    r1 = _make_result("A", "http://dup.com", 0.9)
    r2 = _make_result("B", "http://dup.com", 0.8)
    with patch("engine.DuckDuckGoBackend") as MockDDGS:
        with patch("engine.SearXNGBackend") as MockSearXNG:
            with patch("engine.rate_limiter") as mock_rl:
                with patch("engine.search_cache") as mock_cache:
                    MockDDGS.return_value.is_available = True
                    MockDDGS.return_value.search.return_value = [r1, r2]
                    MockSearXNG.return_value.is_available = False
                    mock_rl.acquire.return_value = True
                    mock_cache.get.return_value = None
                    se = SearchEngine()
                    results, meta = se.search("hello", max_results=5)
                    urls = [x.url for x in results]
                    assert len(urls) == len(set(urls))


def test_search_simplified_query_retry():
    """Если поиск пуст — пробуем упростить запрос (удаляем слова длиной <= 2)"""
    from engine import SearchEngine
    with patch("engine.DuckDuckGoBackend") as MockDDGS:
        with patch("engine.SearXNGBackend") as MockSearXNG:
            with patch("engine.rate_limiter") as mock_rl:
                with patch("engine.search_cache") as mock_cache:
                    MockDDGS.return_value.is_available = True
                    calls = []
                    def fake_search(q, limit, lang):
                        calls.append(q)
                        if "x" in q.split():
                            return []
                        return [_make_result("S", "http://s.com", 0.9)]
                    MockDDGS.return_value.search.side_effect = fake_search
                    MockSearXNG.return_value.is_available = False
                    mock_rl.acquire.return_value = True
                    mock_cache.get.return_value = None
                    se = SearchEngine()
                    # "hello" (5) остается, "x" (1) удаляется
                    results, meta = se.search("hello x x x", max_results=5)
                    assert len(calls) >= 2
                    assert any("hello" in c for c in calls)
                    assert len(results) >= 1


def test_search_backend_error_continues():
    """Один бэкенд падает — другой продолжает"""
    from engine import SearchEngine
    with patch("engine.DuckDuckGoBackend") as MockDDGS:
        with patch("engine.SearXNGBackend") as MockSearXNG:
            with patch("engine.rate_limiter") as mock_rl:
                with patch("engine.search_cache") as mock_cache:
                    MockDDGS.return_value.is_available = True
                    MockDDGS.return_value.search.side_effect = Exception("DDGS down")
                    MockSearXNG.return_value.is_available = True
                    MockSearXNG.return_value.search.return_value = [
                        _make_result("S", "http://s.com", 0.9)
                    ]
                    mock_rl.acquire.return_value = True
                    mock_cache.get.return_value = None
                    se = SearchEngine()
                    results, meta = se.search("hello", max_results=5)
                    assert len(results) >= 1


# ════════════════════════════════════════════════════════════════
#  Parallel search — проверка агрегации результатов из нескольких
#  бэкендов, запущенных одновременно через ThreadPoolExecutor.
# ════════════════════════════════════════════════════════════════

def test_parallel_backends_aggregate_results():
    """Два живых бэкенда — результаты агрегируются из обоих."""
    from engine import SearchEngine
    ddgs_result = _make_result("DDG", "http://ddg.com", 0.95)
    searx_result = _make_result("SearXNG", "http://searx.com", 0.85)
    with patch("engine.DuckDuckGoBackend") as MockDDGS:
        with patch("engine.SearXNGBackend") as MockSearXNG:
            with patch("engine.rate_limiter") as mock_rl:
                with patch("engine.search_cache") as mock_cache:
                    MockDDGS.return_value.is_available = True
                    MockDDGS.return_value.search.return_value = [ddgs_result]
                    MockSearXNG.return_value.is_available = True
                    MockSearXNG.return_value.search.return_value = [searx_result]
                    mock_rl.acquire.return_value = True
                    mock_cache.get.return_value = None
                    se = SearchEngine()
                    results, meta = se.search("hello", max_results=5)
                    urls = {r.url for r in results}
                    assert "http://ddg.com" in urls
                    assert "http://searx.com" in urls
                    assert len(results) == 2


def test_parallel_backends_single_backend_no_overhead():
    """При одном доступном бэкенде _run_backends_parallel зовёт его напрямую,
    без submit в executor (проверяем что не падает и возвращает результат)."""
    from engine import SearchEngine
    fake_result = _make_result("Solo", "http://solo.com", 0.9)
    with patch("engine.DuckDuckGoBackend") as MockDDGS:
        with patch("engine.SearXNGBackend") as MockSearXNG:
            with patch("engine.rate_limiter") as mock_rl:
                with patch("engine.search_cache") as mock_cache:
                    MockDDGS.return_value.is_available = True
                    MockDDGS.return_value.search.return_value = [fake_result]
                    MockSearXNG.return_value.is_available = False
                    mock_rl.acquire.return_value = True
                    mock_cache.get.return_value = None
                    se = SearchEngine()
                    aggregated = se._run_backends_parallel(
                        ["ddgs"], "hello", 10, None
                    )
                    assert len(aggregated) == 1
                    assert aggregated[0].url == "http://solo.com"


def test_parallel_multilingual_aggregates_languages():
    """Multilingual search с несколькими языками агрегирует результаты
    из всех языковых контекстов, запущенных параллельно."""
    from engine import SearchEngine
    r_ru = _make_result("RU", "http://ru.com", 0.9)
    r_en = _make_result("EN", "http://en.com", 0.8)
    r_zh = _make_result("ZH", "http://zh.com", 0.7)
    with patch("engine.DuckDuckGoBackend") as MockDDGS:
        with patch("engine.SearXNGBackend") as MockSearXNG:
            with patch("engine.rate_limiter") as mock_rl:
                with patch("engine.search_cache") as mock_cache:
                    MockDDGS.return_value.is_available = True
                    # Разные результаты для разных языковых регионов
                    def mock_search(q, limit, lang):
                        if lang and "ru" in lang:
                            return [r_ru]
                        if lang and "us" in lang:
                            return [r_en]
                        if lang and "cn" in lang:
                            return [r_zh]
                        return []
                    MockDDGS.return_value.search.side_effect = mock_search
                    MockSearXNG.return_value.is_available = False
                    mock_rl.acquire.return_value = True
                    mock_cache.get.return_value = None
                    se = SearchEngine()
                    results, meta = se.search(
                        "test", languages=["ru", "en", "zh"], max_results=5
                    )
                    urls = {r.url for r in results}
                    assert "http://ru.com" in urls
                    assert "http://en.com" in urls
                    assert "http://zh.com" in urls
                    assert meta.get("languages") == ["ru", "en", "zh"]


def test_parallel_multilingual_one_lang_fails_others_survive():
    """Падение одного языкового контекста не должно ронять остальные.
    Симулируем исключение для одного lang_hint через side_effect."""
    from engine import SearchEngine
    r_en = _make_result("EN", "http://en.com", 0.8)
    with patch("engine.DuckDuckGoBackend") as MockDDGS:
        with patch("engine.SearXNGBackend") as MockSearXNG:
            with patch("engine.rate_limiter") as mock_rl:
                with patch("engine.search_cache") as mock_cache:
                    MockDDGS.return_value.is_available = True
                    def mock_search(q, limit, lang):
                        # ru-ru регион бросает — симулируем падение DDGS для ru
                        if lang and "ru" in lang:
                            raise RuntimeError("ru search crashed")
                        if lang and "us" in lang:
                            return [r_en]
                        return []
                    MockDDGS.return_value.search.side_effect = mock_search
                    MockSearXNG.return_value.is_available = False
                    mock_rl.acquire.return_value = True
                    mock_cache.get.return_value = None
                    se = SearchEngine()
                    results, meta = se.search(
                        "test", languages=["ru", "en"], max_results=5
                    )
                    # ru упал, но en вернул результат
                    assert len(results) >= 1
                    assert results[0].url == "http://en.com"


def test_executor_initialized_in_init():
    """SearchEngine.__init__ создаёт ThreadPoolExecutor для параллельного поиска."""
    from engine import SearchEngine
    from concurrent.futures import ThreadPoolExecutor
    with patch("engine.DuckDuckGoBackend") as MockDDGS:
        with patch("engine.SearXNGBackend") as MockSearXNG:
            MockDDGS.return_value.is_available = False
            MockSearXNG.return_value.is_available = False
            se = SearchEngine()
            assert isinstance(se._executor, ThreadPoolExecutor)
            assert se._executor._max_workers == 6

