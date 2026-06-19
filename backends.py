import time
import json
import threading
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from models import SearchResult
from config import config
import logging

logger = logging.getLogger("SearchTool")

# Проверка доступности библиотек
# Поддерживаем и новый пакет `ddgs`, и старый `duckduckgo_search`.
try:
    from ddgs import DDGS
    DDGS_AVAILABLE = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        DDGS_AVAILABLE = True
    except ImportError:
        DDGS_AVAILABLE = False

class DuckDuckGoBackend:
    name = "ddgs"
    
    def search(self, query, max_results=10, lang_hint=None):
        if not DDGS_AVAILABLE: raise RuntimeError("DDGS not available")
        results = []

        # Если указана языковая подсказка — сначала пробуем её, потом fallback
        if lang_hint:
            methods = [
                {"region": lang_hint},
                {"region": "wt-wt"},
            ]
        else:
            methods = [
                {"region": "wt-wt"},
                {"region": "ru-ru"},
                {"region": "us-en"},
                {"news": True},
            ]

        for method in methods:
            try:
                with DDGS() as ddgs:
                    if method.get("news"):
                        search_results = ddgs.news(query, max_results=max_results)
                    else:
                        search_results = ddgs.text(query, max_results=max_results, region=method["region"])
                    
                    if search_results:
                        for i, r in enumerate(search_results):
                            if i >= max_results: break
                            # Унификация формата для news и text
                            url = r.get("href") or r.get("url")
                            body = r.get("body") or r.get("snippet") or ""
                            results.append(SearchResult(
                                title=r.get("title", "No title"),
                                url=url,
                                snippet=body[:300],
                                score=1.0 - i*0.05, rank=i+1, source_backend=self.name
                            ))
                        return results
            except Exception as e:
                continue
        return results
    
    def news(self, query, max_results=10, timelimit=None):
        if not DDGS_AVAILABLE: raise RuntimeError("DDGS not available")
        results = []
        try:
            kw = {"query": query, "max_results": max_results}
            if timelimit: kw["timelimit"] = timelimit
            with DDGS() as ddgs:
                # ИСПРАВЛЕНО: убран time.sleep(0.5)
                news_results = ddgs.news(**kw)
                for i, r in enumerate(news_results):
                    if i >= max_results: break
                    results.append(SearchResult(
                        title=r.get("title", "") or "No title",
                        url=r.get("url", ""),
                        snippet=f"{r.get('source','')} | {r.get('date','')}",
                        score=1.0-i*0.05, rank=i+1, source_backend=self.name
                    ))
        except Exception as e:
            logger.warning(f"DDGS news error: {e}")
            raise
        return results
    
    def images(self, query, max_results=5):
        if not DDGS_AVAILABLE: raise RuntimeError("DDGS not available")
        results = []
        try:
            with DDGS() as ddgs:
                # ИСПРАВЛЕНО: убран time.sleep(0.5)
                image_results = ddgs.images(query, max_results=max_results)
                for i, r in enumerate(image_results):
                    if i >= max_results: break
                    image_url = r.get("image") or r.get("url") or r.get("thumbnail", "")
                    results.append(SearchResult(
                        title=r.get("title", "") or "No title",
                        url=image_url, snippet=r.get("thumbnail", ""),
                        score=1.0-i*0.05, rank=i+1, source_backend=self.name
                    ))
        except Exception as e:
            logger.warning(f"DDGS images error: {e}")
            raise
        return results
    
    @property
    def is_available(self): return DDGS_AVAILABLE

class SearXNGBackend:
    name = "searxng"
    # Обновленный список инстансов (май 2026)
    INSTANCES = [
        "http://localhost:8081",
        "https://searx.be",
        "https://searx.xyz",
        "https://search.disroot.org",
        "https://searx.sethforprivacy.com",
        "https://priv.au",
        "https://searx.work",
    ]

    def __init__(self, url=None):
        self.url = url
        self.session = requests.Session()
        retry = Retry(total=2, backoff_factor=0.3, status_forcelist=[429,500,502,503])
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers.update({"User-Agent": "SearchTool/3.4"})
        self._available_instance = None
        self._availability_checked = False
        # ИСПРАВЛЕНО: добавлен lock для синхронизации доступа к _available_instance
        self._instance_lock = threading.Lock()
        self._availability_lock = threading.Lock()

    def _get_working_instance(self):
        # ИСПРАВЛЕНО: вся проверка и обновление атомарны
        with self._instance_lock:
            # Сначала проверяем закэшированный инстанс
            if self._available_instance:
                try:
                    resp = self.session.get(self._available_instance, timeout=5)
                    if resp.status_code == 200:
                        return self._available_instance
                except:
                    self._available_instance = None

            # Если не работает, перебираем список
            for instance in self.INSTANCES:
                try:
                    resp = self.session.get(instance, timeout=5)
                    if resp.status_code == 200:
                        self._available_instance = instance
                        return instance
                except Exception:
                    continue
            return None

    def search(self, query, max_results=10, lang_hint=None):
        instance = self._get_working_instance()
        if not instance: raise RuntimeError("No SearXNG instances available")

        params = {"q": query, "format": "json", "categories": "general"}
        if lang_hint:
            params["language"] = lang_hint

        try:
            resp = self.session.get(
                f"{instance}/search",
                params=params,
                timeout=15,
                headers={"Accept": "application/json"}
            )
            resp.raise_for_status()

            # ИСПРАВЛЕНО: проверка что ответ действительно JSON
            ct = resp.headers.get("content-type", "")
            if "application/json" not in ct and "text/json" not in ct:
                logger.warning(f"SearXNG returned non-JSON content type: {ct}")
                with self._instance_lock:
                    self._available_instance = None
                raise RuntimeError(f"SearXNG returned non-JSON (type: {ct})")

            data = resp.json()
            results = []
            for i, r in enumerate(data.get("results", [])[:max_results]):
                results.append(SearchResult(
                    title=r.get("title", "") or "No title",
                    url=r.get("url", ""),
                    snippet=r.get("content", "")[:300],
                    score=r.get("score", 1), rank=i+1, source_backend=self.name
                ))
            return results
        except (json.JSONDecodeError, ValueError) as e:
            with self._instance_lock:
                self._available_instance = None
            logger.warning(f"SearXNG JSON parse error: {e}")
            raise RuntimeError(f"SearXNG returned invalid JSON: {e}")
        except Exception as e:
            with self._instance_lock:
                self._available_instance = None
            logger.warning(f"SearXNG failed: {e}")
            raise

    @property
    def is_available(self):
        # ИСПРАВЛЕНО: ленивая проверка доступности с кэшированием
        if self._availability_checked:
            return self._available_instance is not None

        with getattr(self, '_availability_lock', threading.Lock()):
            if self._availability_checked:
                return self._available_instance is not None

            # ИСПРАВЛЕНО: пробуем только первый инстанс, не все
            result = self._get_working_instance() is not None
            self._availability_checked = True
            return result
