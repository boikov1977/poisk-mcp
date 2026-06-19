import sys
import os

# ============================================================================
# ЗАЩИТА ОТ МУСОРА В STDOUT НА WINDOWS (FIX FOR LM STUDIO)
# При запуске без терминала (через LM Studio) библиотеки типа tqdm и 
# huggingface_hub могут слать прогресс-бары в stdout, ломая JSON-протокол.
# Временно перенаправляем stdout в черную дыру.
# ============================================================================
_original_stdout = sys.stdout
sys.stdout = open(os.devnull, 'w')

# Важно: config должен быть импортирован первым для настройки окружения
from config import config as cfg, initialize_hf_mirror
import config

import json
import logging
import io
import html
import re
import requests  # <--- ДОБАВЛЕН ИМПОРТ
from datetime import datetime
from typing import Optional

# Инициализируем настройки зеркал (выполняется один раз при старте)
initialize_hf_mirror()

# Настройка логирования
try:
    from mcp.server.fastmcp import FastMCP
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

# Настройка логгера
def setup_logging():
    stream = sys.stderr if MCP_AVAILABLE else sys.stdout
    formatter = logging.Formatter("🔍 %(message)s", datefmt="%H:%M:%S")
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    for lib in ["httpx", "httpcore", "huggingface_hub", "urllib3", "filelock", "tokenizers", "flashrank"]:
        logging.getLogger(lib).setLevel(logging.ERROR)
    return logging.getLogger("SearchTool")

logger = setup_logging()

# Импорт наших модулей
from engine import SearchEngine
from network import net, valid_url, safe_req, path_ok
from cache import search_cache

from tools import wmo, jina, extract, is_binary

# MCP Server
if MCP_AVAILABLE:
    mcp = FastMCP("SearchTool-v3.4")
else:
    class FakeMCP:
        def tool(self, f=None, **k): return f if f else (lambda x: x)
    mcp = FakeMCP()

# ИНИЦИАЛИЗАЦИЯ МОДЕЛИ 
engine = SearchEngine()

# ============================================================================
# КОНЕЦ ЗОНЫ ИМПОРТОВ. Возвращаем stdout на место!
# Теперь FastMCP сможет спокойно писать JSON-ответы в LM Studio.
# ============================================================================
sys.stdout.close()
sys.stdout = _original_stdout

# ============================================================================
# TOOLS
# ============================================================================

@mcp.tool()
def search_web(query: str, max_results: int = 8, backend: str = "auto", rerank: bool = True, diversity: bool = True, languages: str = "") -> str:
    """Поиск с нейро-ранжированием. languages — список языков через запятую, напр. "ru,en,zh" для мультиязычного поиска"""
    if len(query.strip()) < 2:
        return "❌ Слишком короткий запрос"

    try:
        langs = [l.strip() for l in languages.split(",") if l.strip()] if languages else None
        res, m = engine.search(query, min(max_results, 20), backend, rerank, diversity, languages=langs)
        if not res:
            return f"❌ {m.get('error', 'Ничего не найдено')}"

        o = [
            f"🔍 \"{m['original']}\"",
            f"📊 {m['count']} результатов | ⏱️ {m['ms']:.0f}ms | {'💾 Cached' if m['cached'] else '🆕 Fresh'} | {'🤖 Reranked' if m['reranked'] else ''}{' 🎯 Diversified' if m['diversified'] else ''}{' 🌐 Multilingual' if m.get('languages') else ''}",
            "─" * 60
        ]
        
        for i, r in enumerate(res, 1):
            o.extend([
                f"\n{i}. {r.title}",
                f"   🔗 {r.url}",
                f"   📝 {r.snippet[:250]}",
                f"   📈 Релевантность: {r.score:.3f}"
            ])
        
        return "\n".join(o)
    except Exception as e: 
        return f"❌ Ошибка поиска: {e}"

# @mcp.tool()
# def search_news(query: str, mr: int = 10, tl: Optional[str] = None) -> str:
#     """Поиск новостей"""
#     try:
#         res, m = engine.news(query, mr, tl)
#         if not res: 
#             return f"📰 Новости не найдены: {m.get('error', '')}"
        
#         result_lines = [f"📰 Новости по запросу \"{query}\" | {'все время' if not tl else tl}\n"]
#         for i, r in enumerate(res, 1):
#             result_lines.append(f"{i}. {r.title}\n   🔗 {r.url}\n   📝 {r.snippet}\n")
        
#         return "\n".join(result_lines)
#     except Exception as e: 
#         return f"❌ Ошибка поиска новостей: {e}"

# @mcp.tool()
# def search_images(query: str, mr: int = 5) -> str:
#     """Поиск изображений"""
#     try:
#         res, m = engine.images(query, mr)
#         if not res: 
#             return "🖼️ Изображения не найдены"
        
#         result_lines = [f"🖼️ Изображения по запросу \"{query}\"\n"]
#         for i, r in enumerate(res, 1):
#             result_lines.append(f"{i}. {r.title}\n   🖼️ URL: {r.url}\n   👁️ Превью: {r.snippet}\n")
        
#         return "\n".join(result_lines)
#     except Exception as e: 
#         return f"❌ Ошибка поиска изображений: {e}"

@mcp.tool()
def read_webpage(url: str, markdown: bool = True) -> str:
    """Чтение веб-страницы"""
    if not valid_url(url):
        return "❌ URL заблокирован (проверка SSRF)"

    try:
        if markdown:
            # Пробуем получить через Jina API
            jina_result = jina(url, net)
            if jina_result and len(jina_result) > 100:
                return jina_result[:cfg.MAX_CONTENT_LENGTH]

        r = safe_req(url)
        try:
            r.raise_for_status()

            ct = (r.headers.get("content-type") or "").lower()
            is_text = ct.startswith("text/")
            is_html = ("text/html" in ct) or ("application/xhtml+xml" in ct)
            is_plain = "text/plain" in ct

            if not (is_text or is_html):
                return f"❌ Неподдерживаемый тип контента: {ct}"

            buf = bytearray()
            size = 0
            for chunk in r.iter_content(8192):
                if not chunk:
                    continue
                size += len(chunk)
                if size > cfg.MAX_CONTENT_SIZE:
                    break
                buf.extend(chunk)

            if not buf:
                return "⚠️ Пустой ответ"

            encoding = r.encoding or getattr(r, "apparent_encoding", None) or "utf-8"
            raw_text = bytes(buf).decode(encoding, errors="replace")

            if is_plain and not is_html:
                text = raw_text.strip()[:cfg.MAX_CONTENT_LENGTH]
            else:
                text = extract(raw_text)[:cfg.MAX_CONTENT_LENGTH]

            if len(text.strip()) < 20:
                if not markdown:
                    return f"⚠️ Страница содержит мало текста ({len(text)} символов). Попробуйте markdown=True"
                return f"⚠️ Страница содержит мало текста ({len(text)} символов). Возможно, контент грузится через JS"

            return text
        finally:
            try:
                r.close()
            except Exception:
                pass
    except Exception as e:
        return f"❌ Ошибка чтения страницы: {e}"

@mcp.tool()
def browse_summarize(url: str, instr: str = "Извлеките основные пункты.") -> str:
    """Чтение и создание резюме страницы"""
    content = read_webpage(url)
    if content.startswith("❌"): 
        return content
    
    try:
        # Разбиваем на параграфы
        paragraphs = [p.strip() for p in content.split("\n\n") if len(p.strip()) > 80]
        # Сортируем по длине (чем длиннее, тем вероятнее важный контент)
        scored = [(len(p) * (1 - i * 0.05), p) for i, p in enumerate(paragraphs)]
        scored.sort(reverse=True)
        
        return f"📋 {instr}\n{'='*60}\n\n" + "\n\n".join([
            p for _, p in scored[:10]
        ])[:3000]
    except Exception as e:
        return f"❌ Ошибка обработки: {e}"

@mcp.tool()
def web_research(query: str, depth: int = 3) -> str:
    """Глубокое исследование темы"""
    try:
        try:
            depth_i = int(depth)
        except Exception:
            depth_i = 3

        if depth_i < 1:
            depth_i = 1
        if depth_i > 5:
            depth_i = 5

        max_results = min(depth_i + 5, 20)
        res, _ = engine.search(query, max_results, "auto", False, True)
        if not res:
            return f"❌ Нет результатов по запросу '{query}'"
        
        report = []
        max_report_chars = 9000

        def add_block(text: str) -> None:
            if not text:
                return
            current_len = sum(len(x) + 1 for x in report)
            remaining = max_report_chars - current_len
            if remaining <= 0:
                return
            if len(text) > remaining:
                report.append(text[: max(0, remaining - 1)])
                return
            report.append(text)

        add_block(f"# 🔬 Исследование: {query}")
        add_block(f"📅 Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        add_block("\n## Источники\n")
        
        for i, r in enumerate(res, 1):
            add_block(f"### {i}. {r.title}\n- 🔗 {r.url}\n- 📝 {r.snippet[:200]}...\n")
        
        add_block("\n## Детальный анализ\n")
        
        from urllib.parse import urlparse

        q_terms = [t for t in re.split(r"\s+", query.lower().strip()) if len(t) >= 3]

        def score_paragraph(p: str) -> int:
            p_l = p.lower()
            hits = sum(1 for t in q_terms if t in p_l)
            return hits * 200 + min(len(p), 800)

        seen_domains = set()
        analyzed = 0
        for r in res:
            if analyzed >= depth_i:
                break
            if not getattr(r, "url", None):
                continue
            if not valid_url(r.url):
                continue

            domain = urlparse(r.url).netloc.lower()
            if domain in seen_domains:
                continue
            seen_domains.add(domain)

            analyzed += 1
            add_block(f"### Источник {analyzed}: {r.title}\n- 🔗 {r.url}\n")
            try:
                content = read_webpage(r.url, markdown=False)
                if content.startswith("❌"):
                    add_block(f"*{content}*\n")
                    continue

                paragraphs = [p.strip() for p in content.split("\n\n") if len(p.strip()) >= 80]
                if not paragraphs:
                    lines = [ln.strip() for ln in content.split("\n") if len(ln.strip()) >= 60]
                    paragraphs = lines[:20]

                paragraphs.sort(key=score_paragraph, reverse=True)
                top = paragraphs[:6]
                if top:
                    add_block("\n".join(top) + "\n")
                else:
                    add_block("*Недостаточно извлекаемого текста*\n")
            except Exception as e:
                add_block(f"*Ошибка: {e}*\n")
        
        add_block("\n---\n*MCP SearchTool v3.4 Production*")
        return "\n".join(report)
    except Exception as e: 
        return f"❌ Ошибка исследования: {e}"

# @mcp.tool()
# def stats() -> str:
#     """Статистика системы"""
#     s = engine.get_stats()
#     c = s.pop("cache")
#     rl = s.pop("rl")
#     n = net._stats

#     return "\n".join([
#         "📈 Статистика системы",
#         "─" * 40,
#         f"🔍 Запросов: {s['queries']} | ⏱️ Среднее время: {s['avg_ms']:.1f}мс",
#         f"💾 Кэш: {c['hits']}/{c['hits']+c['misses']} ({c['hit_rate_pct']}% попаданий)",
#         f"⚙️ Бэкенды: {', '.join(s['backends'])}",
#         f"🧠 Нейросеть: {'✅ Загружена' if s['model_loaded'] else '❌ Не загружена'} | Зеркало HF: {s['mirror']}",
#         f"🌐 Сеть: {n['req']} запросов | {n['err']} ошибок | {n['bytes']/1024/1024:.1f}MB трафика"
#     ])

# @mcp.tool()
# def clear_cache() -> str:
#     """Очистка кэша"""
#     search_cache.invalidate(None)
#     return "✅ Кэш успешно очищен"

# @mcp.tool()
# def read_file(path: str) -> str:
#     """Чтение локального файла"""
#     # Проверка на бинарный файл
#     if is_binary(path):
#         return "❌ Бинарные файлы не поддерживаются"

#     # Проверка размера файла
#     try:
#         file_size = os.path.getsize(path)
#         if file_size > 10 * 1024 * 1024:  # 10 MB лимит
#             return f"❌ Файл слишком большой ({file_size / 1024 / 1024:.1f} MB, лимит 10 MB)"
#     except OSError as e:
#         return f"❌ Ошибка доступа к файлу: {e}"

#     if not path_ok(path):
#         return "❌ Доступ запрещен (путь вне разрешенной директории)"

#     # Пробуем разные кодировки
#     encodings = ['utf-8', 'utf-8-sig', 'cp1251', 'latin-1']

#     for enc in encodings:
#         try:
#             with open(path, "r", encoding=enc) as f:
#                 content = f.read(8000)
#                 return content
#         except UnicodeDecodeError:
#             continue
#         except Exception as e:
#             return f"❌ Ошибка чтения: {e}"

#     return "❌ Не удалось прочитать файл (неизвестная кодировка)"

# @mcp.tool()
# def get_time() -> str:
#     """Текущее время"""
#     return datetime.now().isoformat()


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    sys.stderr.write("\n" + "=" * 60 + "\n")
    sys.stderr.write("🚀 MCP SearchTool v3.4 - Production Edition\n")
    sys.stderr.write("=" * 60 + "\n")
    sys.stderr.write(f"  • HF-Mirror: {config.SELECTED_MIRROR}\n")
    sys.stderr.write(f"  • Backends: {list(engine.backends.keys())}\n")
    sys.stderr.write(f"  • Neural Reranking: {'✅ Enabled' if engine.reranker.is_available else '❌ Disabled'}\n")
    
    logger.info("Starting Modular Server...")
    
    if MCP_AVAILABLE:
        try:
            mcp.run(transport="stdio")
        finally:
            net.close()
    else:
        sys.stderr.write("⚠️ FastMCP not available - running in test mode\n")
        net.close()