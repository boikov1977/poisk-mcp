import re
import time
import os
import logging
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List
from models import SearchResult, ProcessedQuery
from config import config
from cache import search_cache, rate_limiter
from backends import DuckDuckGoBackend, SearXNGBackend
from reranker import NeuralReranker

logger = logging.getLogger("SearchTool")

class QueryProcessor:
    @classmethod
    def process(cls, query):
        if len(query.strip()) < 2: raise ValueError("Too short")
        original = query.strip()
        # Максимально упрощаем: только очистка от лишних пробелов
        # Мы НЕ удаляем стоп-слова, так как они важны для контекста (например, "в 2016 году")
        normalized = original
        return ProcessedQuery(original, normalized, [original], [], "informational", [])

class DiversityEngine:
    def __init__(self):
        self.lambda_param = config.DIVERSITY_LAMBDA
        self.reranker = NeuralReranker.get_instance()

    def diversify(self, results, query=None, target_count=None):
        if not results or len(results) <= 1: return results
        target_count = target_count or min(len(results), config.MAX_SEARCH_RESULTS)

        # MMR на текстовом сходстве (работает с любым encoder'ом)
        return self._text_diversify(results, target_count)

    def _mmr_select(self, results, target_count, similarity_fn):
        """Общий greedy MMR-выбор.

        similarity_fn(idx, selected_indices) -> float: максимальная похожесть
        элемента idx на любой из уже выбранных. Возвращает список выбранных индексов.
        """
        selected = [0]
        remaining = list(range(1, len(results)))

        while len(selected) < target_count and remaining:
            best_score, best_idx = -float('inf'), None
            for idx in remaining:
                rel = results[idx].score
                max_sim = similarity_fn(idx, selected)
                mmr = self.lambda_param * rel - (1.0 - self.lambda_param) * max_sim
                if mmr > best_score:
                    best_score, best_idx = mmr, idx

            if best_idx is not None:
                selected.append(best_idx)
                remaining.remove(best_idx)

        return selected

    @staticmethod
    def _finalize(results, selected):
        """Собираем выбранные результаты и пересчитываем rank."""
        diversified = [results[i] for i in selected]
        for i, r in enumerate(diversified):
            r.rank = i + 1
        return diversified

    def _text_diversify(self, results, target_count):
        """MMR на основе текстового сходства (Jaccard similarity).
        Работает без эмбеддингов — подходит для FlashRank cross-encoder."""

        # Если тексты короткие — используем domain-based MMR
        texts = [f"{r.title} {r.snippet}" for r in results]
        avg_len = sum(len(t) for t in texts) / len(texts) if texts else 0
        if avg_len < 50:
            return self._domain_mmr(results, target_count)

        # Токенизация в слова
        def tokenize(text):
            return set(re.findall(r'\w+', text.lower()))

        tokens = [tokenize(t) for t in texts]

        def jaccard_similarity(idx, selected):
            max_sim = 0.0
            for sel in selected:
                if not tokens[idx] or not tokens[sel]:
                    continue
                intersection = len(tokens[idx] & tokens[sel])
                union = len(tokens[idx] | tokens[sel])
                sim = intersection / union if union > 0 else 0.0
                if sim > max_sim:
                    max_sim = sim
            return max_sim

        selected = self._mmr_select(results, target_count, jaccard_similarity)
        return self._finalize(results, selected)

    def _domain_mmr(self, results, target_count):
        """MMR на основе доменов. similarity=1 если домен совпадает, 0 иначе."""
        from urllib.parse import urlparse

        domains = [urlparse(r.url).netloc for r in results]

        def domain_similarity(idx, selected):
            return 1.0 if any(domains[idx] == domains[sel] for sel in selected) else 0.0

        selected = self._mmr_select(results, target_count, domain_similarity)
        return self._finalize(results, selected)

class SearchEngine:
    def __init__(self):
        self.backends = {}
        ddgs = DuckDuckGoBackend()
        if ddgs.is_available: self.backends["ddgs"] = ddgs
        searx = SearXNGBackend()
        if searx.is_available: self.backends["searxng"] = searx
        
        self.reranker = NeuralReranker.get_instance()
        self.diversity = DiversityEngine()
        # ИСПРАВЛЕНО: deque с maxlen для предотвращения утечки памяти
        self._stats = {"queries": 0, "cache_hits": 0, "backend_usage": {}, "latencies": deque(maxlen=1000)}
        # Общий пул потоков для параллельного поиска по бэкендам и языкам.
        # max_workers=6 покрывает худший случай: 2 бэкенда × 3 языка в multilingual.
        # Переиспользуем между вызовами — не создаём executor на каждый search.
        self._executor = ThreadPoolExecutor(max_workers=6)
    
    # Карта языков → регионы DuckDuckGo
    LANGUAGE_REGIONS = {
        "ru": "ru-ru",
        "en": "us-en",
        "zh": "cn-zh",
        "de": "de-de",
        "fr": "fr-fr",
        "es": "es-es",
        "ja": "jp-jp",
        "ko": "kr-kr",
    }

    def search(self, query, max_results=None, backend="auto", rerank=True, diversity=True, languages=None):
        t0 = time.time()
        mr = max_results or config.DEFAULT_MAX_RESULTS
        meta = {"original": query, "ms": 0, "backend": "", "count": 0, "cached": False, "reranked": False, "diversified": False, "languages": languages}
        self._stats["queries"] += 1

        try:
            pq = QueryProcessor.process(query)
        except ValueError as e:
            return [], {**meta, "error": str(e)}

        sq = pq.get_search_query()
        if not sq or not sq.strip(): return [], {**meta, "error": "Empty search query"}

        logger.info(f"🔎 Final Search Query: {sq}")

        # Кэш только для одноязычного поиска
        cache_key = f"search:{sq}" + (f":{','.join(sorted(languages))}" if languages else "")

        cached = search_cache.get("search", cache_key, mr) if not languages else None
        if cached:
            meta["cached"] = True; meta["ms"] = (time.time()-t0)*1000
            self._stats["cache_hits"] += 1
            return cached, meta

        if not rate_limiter.acquire(5): return [], {**meta, "error": "Rate limited"}

        if languages:
            results = self._multilingual_search(sq, mr, backend, languages)
        else:
            results = self._search(sq, mr, backend)

        if not results:
            logger.warning(f"⚠️ No results found for: {sq}")
            return [], {**meta, "error": "No results"}

        # Записываем бэкенд
        meta["backend"] = results[0].source_backend
        self._stats["backend_usage"][meta["backend"]] = self._stats["backend_usage"].get(meta["backend"], 0)+1

        if rerank and self.reranker.is_available:
            results = self._rerank(sq, results)
            meta["reranked"] = True

        if diversity and len(results) > 1:
            results = self.diversity.diversify(results, sq, mr)
            meta["diversified"] = True

        meta["count"] = len(results); meta["ms"] = (time.time()-t0)*1000
        self._stats["latencies"].append(meta["ms"])

        if not languages:
            search_cache.set("search", results, cache_key, mr)

        logger.info(f"✅ {len(results)} results in {meta['ms']:.0f}ms [langs={languages}]")
        return results, meta

    def _multilingual_search(self, query, mr, backend, languages):
        """Поиск по одному запросу в разных языковых контекстах.

        Все языки запускаются параллельно через self._executor.
        Каждый future вызывает self._search(), который сам параллелит бэкенды.
        Падение одного языка не роняет остальные.
        """
        all_results = []
        seen_urls = set()

        # Один future на каждый язык
        futures = {}
        for lang in languages:
            region = self.LANGUAGE_REGIONS.get(lang, "wt-wt")
            future = self._executor.submit(self._search, query, mr * 2, backend, region)
            futures[future] = lang

        # Собираем результаты по мере готовности
        for future in as_completed(futures):
            lang = futures[future]
            try:
                results = future.result()
            except Exception as e:
                logger.warning(f"Multilingual search failed for {lang}: {e}")
                continue
            if not results:
                continue
            for r in results:
                if r.url not in seen_urls:
                    seen_urls.add(r.url)
                    all_results.append(r)

        # Сортируем по исходному score
        all_results.sort(key=lambda x: x.score, reverse=True)
        return all_results[:mr]

    def _search(self, query, mr, backend, lang_hint=None):
        # Определяем порядок бэкендов
        order = [backend] if backend!="auto" and backend in self.backends else list(self.backends.keys())

        all_results = []

        # ИСПРАВЛЕНО: Агрегация результатов
        per_backend_limit = mr * 2

        # Запускаем все бэкенды параллельно — DDGS и SearXNG работают одновременно.
        # Падение одного future не роняет остальные (исключение ловим в result()).
        all_results = self._run_backends_parallel(order, query, per_backend_limit, lang_hint)

        # Если все еще пусто, пробуем упростить запрос (удаляем слова меньше 3 букв)
        if not all_results and len(query.split()) > 3:
            simple_query = " ".join([w for w in query.split() if len(w) > 2])
            if simple_query != query:
                logger.info(f"🔄 Retrying with simplified query: {simple_query}")
                all_results = self._run_backends_parallel(order, simple_query, per_backend_limit, lang_hint)

        if not all_results:
            return []

        # Дедупликация по URL
        seen_urls = set()
        unique_results = []
        for r in all_results:
            if r.url not in seen_urls:
                seen_urls.add(r.url)
                unique_results.append(r)

        # Сортируем по score (предварительный рейтинг)
        unique_results.sort(key=lambda x: x.score, reverse=True)

        # ИСПРАВЛЕНО: возвращаем не больше запрошенного лимита
        return unique_results[:mr]

    def _run_backends_parallel(self, order, query, per_backend_limit, lang_hint):
        """Параллельный запуск бэкендов из order с агрегацией результатов.

        Возвращает список SearchResult (без дедупликации — её делает вызывающий _search).
        Падение одного бэкенда логируется и не влияет на остальные.
        """
        if not order:
            return []

        # Один бэкенд — без overhead на потоки, зовём напрямую.
        if len(order) == 1:
            b = order[0]
            try:
                r = self.backends[b].search(query, per_backend_limit, lang_hint)
                return list(r) if r else []
            except Exception as e:
                logger.warning(f"Backend {b} failed: {e}")
                return []

        # Несколько бэкендов — запускаем параллельно через self._executor.
        futures = {}
        for b in order:
            future = self._executor.submit(self._backend_search_safe, b, query, per_backend_limit, lang_hint)
            futures[future] = b

        aggregated = []
        for future in as_completed(futures):
            b = futures[future]
            try:
                r = future.result()
            except Exception as e:
                # _backend_search_safe уже залогировал — но защита от неожиданных исключений
                logger.warning(f"Backend {b} future failed: {e}")
                continue
            if r:
                aggregated.extend(r)
        return aggregated

    def _backend_search_safe(self, backend_name, query, per_backend_limit, lang_hint):
        """Обёртка над backend.search() с поимкой исключений.
        Возвращает list[SearchResult] или [] — никогда не бросает.
        """
        try:
            r = self.backends[backend_name].search(query, per_backend_limit, lang_hint)
            return list(r) if r else []
        except Exception as e:
            logger.warning(f"Backend {backend_name} failed: {e}")
            return []
    
    def _rerank(self, query, results, top_k=20):
        if not results: return results
        try:
            texts = [f"{r.title} {r.snippet}" for r in results[:top_k]]
            if not texts or all(not t.strip() for t in texts):
                logger.warning("Empty texts for reranking")
                return results

            # FlashRank cross-encoder — оценивает релевантность пары запрос-документ
            scores = self.reranker.rerank(query, texts)

            if scores and len(scores) == len(results[:top_k]):
                scores_list = [float(s) for s in scores]

                for i, r in enumerate(results[:top_k]):
                    r.score = r.score * 0.4 + scores_list[i] * 0.6
                    r.features["neural"] = round(scores_list[i], 4)

                results.sort(key=lambda x: x.score, reverse=True)
                for i, r in enumerate(results):
                    r.rank = i + 1
        except Exception as e:
            logger.warning(f"Rerank failed: {e}")
        return results
    
    def news(self, query, mr=10, tl=None):
        if "ddgs" not in self.backends: return [], {"error": "DDGS not available"}
        try:
            res = self.backends["ddgs"].news(query, mr, tl)
            return res, {"count": len(res)}
        except Exception as e:
            return [], {"error": str(e)}
    
    def images(self, query, mr=5):
        if "ddgs" not in self.backends: return [], {"error": "DDGS not available"}
        try:
            res = self.backends["ddgs"].images(query, mr)
            return res, {"count": len(res)}
        except Exception as e:
            return [], {"error": str(e)}
    
    def get_stats(self):
        latencies = list(self._stats["latencies"])
        avg = sum(latencies)/len(latencies) if latencies else 0
        # ИСПРАВЛЕНО: используем синглтон вместо создания нового экземпляра
        reranker = NeuralReranker.get_instance()
        return {
            **self._stats, "avg_ms": round(avg, 2),
            # Преобразуем deque в list для JSON-сериализации
            "latencies_count": len(latencies),
            "cache": search_cache.get_stats(),
            "rl": rate_limiter.get_stats(),
            "backends": list(self.backends.keys()),
            "model_loaded": reranker.is_available,
            "mirror": os.environ.get("HF_ENDPOINT", "N/A")
        }