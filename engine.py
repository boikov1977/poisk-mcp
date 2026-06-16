import re
import time
import os
import logging
from collections import deque
from typing import List
from models import SearchResult, ProcessedQuery
from config import config
from cache import search_cache, rate_limiter
from backends import DuckDuckGoBackend, SearXNGBackend
from reranker import NeuralReranker, TRANSFORMERS_AVAILABLE, TORCH_AVAILABLE, NUMPY_AVAILABLE, util
import numpy as np

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
        if not self.reranker.is_available: return self._domain_diversify(results, target_count)
        
        try:
            texts = [f"{r.title} {r.url}" for r in results]
            embs = self.reranker.encode(texts, convert_to_tensor=True)
            selected = [0]
            remaining = list(range(1, len(results)))
            
            while len(selected) < target_count and remaining:
                best_score, best_idx = -float('inf'), None
                for idx in remaining:
                    rel = results[idx].score
                    sim_matrix = util.cos_sim(embs[idx:idx+1], embs[selected])
                    max_sim = sim_matrix.max().item()
                    mmr = self.lambda_param * rel - (1-self.lambda_param)*max_sim
                    if mmr > best_score: best_score, best_idx = mmr, idx
                if best_idx is not None:
                    selected.append(best_idx)
                    remaining.remove(best_idx)
            
            diversified = [results[i] for i in selected]
            for i, r in enumerate(diversified): r.rank = i+1
            return diversified
        except Exception as e:
            logger.warning(f"Diversity failed: {e}")
            return results[:target_count]
    
    def _domain_diversify(self, results, count):
        from urllib.parse import urlparse
        seen, out = set(), []
        for r in results:
            d = urlparse(r.url).netloc
            if d not in seen:
                seen.add(d); out.append(r)
                if len(out) >= count: break
        for r in results:
            if r not in out: out.append(r)
            if len(out) >= count: break
        return out

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
    
    def search(self, query, max_results=None, backend="auto", rerank=True, diversity=True):
        t0 = time.time()
        mr = max_results or config.DEFAULT_MAX_RESULTS
        meta = {"original": query, "ms": 0, "backend": "", "count": 0, "cached": False, "reranked": False, "diversified": False}
        self._stats["queries"] += 1
        
        try:
            pq = QueryProcessor.process(query)
        except ValueError as e:
            return [], {**meta, "error": str(e)}
        
        sq = pq.get_search_query()
        if not sq or not sq.strip(): return [], {**meta, "error": "Empty search query"}
        
        logger.info(f"🔎 Final Search Query: {sq}")
        
        cached = search_cache.get("search", sq, mr)
        if cached:
            meta["cached"] = True; meta["ms"] = (time.time()-t0)*1000
            self._stats["cache_hits"] += 1
            return cached, meta
        
        if not rate_limiter.acquire(5): return [], {**meta, "error": "Rate limited"}

        results = self._search(sq, mr, backend)
        if not results: 
            logger.warning(f"⚠️ No results found for: {sq}")
            return [], {**meta, "error": "No results"}

        # Записываем, какие бэкенды дали результаты (хотя бы один)
        meta["backend"] = results[0].source_backend
        self._stats["backend_usage"][meta["backend"]] = self._stats["backend_usage"].get(meta["backend"], 0)+1

        if rerank and self.reranker.is_available:
            results = self._rerank(sq, results)
            meta["reranked"] = True

        # ИСПРАВЛЕНО: diversity применяем для любого количества результатов > 1
        if diversity and len(results) > 1:
            results = self.diversity.diversify(results, sq, mr)
            meta["diversified"] = True

        meta["count"] = len(results); meta["ms"] = (time.time()-t0)*1000
        self._stats["latencies"].append(meta["ms"])
        search_cache.set("search", results, sq, mr)
        
        logger.info(f"✅ {len(results)} results in {meta['ms']:.0f}ms [{meta['backend']}]")
        return results, meta
    
    def _search(self, query, mr, backend):
        # Определяем порядок бэкендов
        order = [backend] if backend!="auto" and backend in self.backends else list(self.backends.keys())

        all_results = []

        # ИСПРАВЛЕНО: Агрегация результатов
        per_backend_limit = mr * 2

        for b in order:
            try:
                r = self.backends[b].search(query, per_backend_limit)
                if r:
                    all_results.extend(r)
            except Exception as e:
                logger.warning(f"Backend {b} failed: {e}")
                continue

        # Если все еще пусто, пробуем упростить запрос (удаляем слова меньше 3 букв)
        if not all_results and len(query.split()) > 3:
            simple_query = " ".join([w for w in query.split() if len(w) > 2])
            if simple_query != query:
                logger.info(f"🔄 Retrying with simplified query: {simple_query}")
                for b in order:
                    try:
                        r = self.backends[b].search(simple_query, per_backend_limit)
                        if r: all_results.extend(r)
                    except: continue

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
    
    def _rerank(self, query, results, top_k=20):
        if not results: return results
        try:
            texts = [f"{r.title} {r.snippet}" for r in results[:top_k]]
            if not texts or all(not t.strip() for t in texts):
                logger.warning("Empty texts for reranking")
                return results
            
            q_emb = self.reranker.encode(query, convert_to_tensor=True)
            d_emb = self.reranker.encode(texts, convert_to_tensor=True)
            
            sims = util.cos_sim(q_emb, d_emb)[0]
            
            if TORCH_AVAILABLE and hasattr(sims, 'cpu'):
                scores = sims.cpu().detach().numpy()
            elif NUMPY_AVAILABLE:
                scores = np.array(sims.tolist() if hasattr(sims, 'tolist') else sims)
            else:
                scores = sims.tolist() if hasattr(sims, 'tolist') else list(sims)
            
            scores_list = [float(s) for s in scores]
            
            for i, r in enumerate(results[:top_k]):
                r.score = r.score*0.4 + scores_list[i]*0.6
                r.features["neural"] = round(scores_list[i], 4)
            
            results.sort(key=lambda x: x.score, reverse=True)
            for i, r in enumerate(results): r.rank = i+1
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