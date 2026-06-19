# 🔍 MCP SearchTool v3.5

MCP-сервер для Claude Code с нейро-ранжированием, мультиязычным поиском и диверсификацией результатов.

Инструменты: `search_web`, `read_webpage`, `browse_summarize`, `web_research`.

## Фишки

- **🌐 Мультиязычный поиск** — ищет по одному запросу в нескольких языковых регионах (ru, en, zh, de, fr, es, ja, ko), дедуплицирует и ранжирует общий результат
- **🧠 FlashRank cross-encoder** — лёгкая ONNX-модель (~98MB) без PyTorch, работает на любом CPU
- **🎯 MMR-диверсификация** — Jaccard-based Maximal Marginal Relevance, убирает дубли по смыслу без эмбеддингов
- **⚡ Быстрый старт** — не нужно качать гигабайты torch/CUDA

## Архитектура

```
┌──────────────────────────────────────────────┐
│         Claude Code (MCP-клиент)              │
│  search_web(..., languages="ru,en,zh")        │
└──────────┬───────────────────────────────────┘
           │ stdio (MCP-протокол)
┌──────────▼───────────────────────────────────┐
│         server.py (MCP SearchTool v3.5)       │
│  ┌─────────┐  ┌──────────┐  ┌──────────────┐ │
│  │ Duck     │  │ SearXNG  │  │ FlashRank    │ │
│  │ DuckGo   │  │ (Docker) │  │ Cross-encoder│ │
│  │ Backend  │  │ Backend  │  │ (MultiBERT)  │ │
│  └─────────┘  └──────────┘  └──────────────┘ │
│  ┌─────────────────────────────────────────┐  │
│  │ DiversityEngine (Jaccard MMR / Domain)  │  │
│  └─────────────────────────────────────────┘  │
└──────────────────────────────────────────────┘
```

## Быстрая установка

```bash
# 1. Клонировать
git clone git@github.com:boikov1977/poisk-mcp.git
cd poisk-mcp

# 2. Создать venv и установить зависимости
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# 3. (Опционально) Запустить SearXNG — для fallback-бэкенда
docker compose up -d

# 4. Подключить к Claude Code
claude mcp add poisk-mcp -- $(pwd)/venv/bin/python $(pwd)/server.py
```

> 💡 **Никакого PyTorch!** FlashRank использует ONNX Runtime — лёгкий, быстрый, без CUDA.

## Мультиязычный поиск

Параметр `languages="ru,en,zh"` в `search_web` запускает поиск по всем указанным регионам:

```python
# Искать на русском, английском и китайском
search_web("последние новости ИИ", languages="ru,en,zh")

# Только английский
search_web("latest AI news", languages="en")

# Обычный поиск (без мультиязычности)
search_web("новости")
```

**Как работает:**
1. Запрос отправляется в DuckDuckGo для каждого языка со своим регионом (`ru-ru`, `us-en`, `cn-zh`)
2. Результаты дедуплицируются по URL (один и тот же сайт не дублируется)
3. FlashRank переранжирует общий пул по релевантности
4. DiversityEngine выбирает максимально разные по смыслу результаты

**Поддерживаемые языки:** ru, en, zh, de, fr, es, ja, ko

## Ручная установка (по шагам)

### 1. Python-окружение

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### 2. SearXNG (Docker) — опционально

```bash
docker compose up -d
# Проверка: curl http://localhost:8081/
```

SearXNG работает как fallback, если DuckDuckGo недоступен.

### 3. Запуск

```bash
venv/bin/python server.py
```

Сервер работает по протоколу MCP (stdio). Первый запуск скачает модель FlashRank (~98MB) автоматически.

### 4. Подключение к Claude Code

Через глобальный MCP-конфиг (`~/.claude.json`):
```json
{
  "mcpServers": {
    "poisk-mcp": {
      "type": "stdio",
      "command": "/path/to/poisk-mcp/venv/bin/python",
      "args": ["/path/to/poisk-mcp/server.py"]
    }
  }
}
```

Или через CLI:
```bash
claude mcp add poisk-mcp -- $(pwd)/venv/bin/python $(pwd)/server.py
```

## Состав

| Файл | Назначение |
|------|-----------|
| `server.py` | Главный MCP-сервер (точка входа) |
| `config.py` | Конфигурация, настройка HF mirror, UTF-8 |
| `engine.py` | Поисковый движок + мультиязычный поиск + DiversityEngine |
| `backends.py` | Бэкенды поиска: DuckDuckGo + SearXNG |
| `reranker.py` | Нейро-ранжирование (FlashRank cross-encoder) |
| `tools.py` | Вспомогательные утилиты (BeautifulSoup, Jina) |
| `network.py` | Сетевой слой (SSRF-защита, rate limit) |
| `cache.py` | Кэш результатов + rate limiter |
| `models.py` | Data-классы (SearchResult, ProcessedQuery) |
| `Makefile` | Цели: dev, install, check, clean |
| `docker-compose.yml` | SearXNG в Docker |
| `deploy.sh` | Полная установка одной командой |
| `scripts/download_model.sh` | (устарел — модель качается автоматически) |
| `requirements.txt` | Python-зависимости |

## Инструменты MCP

| Инструмент | Параметры | Описание |
|-----------|-----------|----------|
| `search_web` | `query, max_results=8, backend="auto", rerank=True, diversity=True, languages=""` | Поиск с нейро-ранжированием. `languages="ru,en"` — мультиязычный режим |
| `read_webpage` | `url, markdown=True` | Чтение веб-страницы (HTML → текст) |
| `browse_summarize` | `url, instr="Извлеките основные пункты."` | Чтение + суммаризация |
| `web_research` | `query, depth=3` | Глубокое исследование темы |

## Требования

- Python 3.10+
- Docker (опционально, для SearXNG)
- ~300MB свободного места (модель FlashRank ~98MB + зависимости)

## Что изменилось в v3.5

По сравнению с v3.4:

| Было | Стало |
|------|-------|
| Sentence Transformers + PyTorch (~2.5GB) | FlashRank ONNX (~98MB, без torch) |
| Bi-encoder (эмбеддинги) | Cross-encoder (прямая оценка релевантности) |
| Одноязычный поиск | Мультиязычный поиск (ru, en, zh, de, fr, es, ja, ko) |
| Diversity падал без эмбеддингов | Jaccard MMR + Domain MMR (всегда работает) |
| Torch занимал 90% установки | `pip install -r requirements.txt` за 30 секунд |
