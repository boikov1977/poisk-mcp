# 🔍 MCP SearchTool v3.4

MCP-сервер для Claude Code с нейро-ранжированием результатов поиска.

Предоставляет инструменты: `search_web`, `read_webpage`, `browse_summarize`, `web_research`.

## Архитектура

```
┌──────────────────────────────────────────────┐
│         Claude Code (MCP-клиент)              │
│  search_web  read_webpage  browse_summarize   │
└──────────┬───────────────────────────────────┘
           │ stdio (MCP-протокол)
┌──────────▼───────────────────────────────────┐
│         server.py (MCP SearchTool v3.4)       │
│  ┌─────────┐  ┌──────────┐  ┌──────────────┐ │
│  │ Duck     │  │ SearXNG  │  │ Neural       │ │
│  │ DuckGo   │  │ (Docker) │  │ Reranker     │ │
│  │ Backend  │  │ Backend  │  │ (all-MiniLM) │ │
│  └─────────┘  └──────────┘  └──────────────┘ │
└──────────────────────────────────────────────┘
```

## Быстрая установка

```bash
# 1. Клонировать
git clone git@github.com:boikov1977/poisk-mcp.git
cd poisk-mcp

# 2. Запустить деплой (SearXNG + venv + зависимости + модель)
./deploy.sh

# 3. Подключить к Claude Code
claude mcp add poisk-mcp -- $(pwd)/venv/bin/python $(pwd)/server.py
```

> 💡 **Совет:** Если устанавливаешь вручную — используй `make install-cpu` вместо `make install`,
> чтобы PyTorch скачался без CUDA-пакетов (экономит ~2.5 ГБ трафика и часа ожидания).

## Ручная установка (по шагам)

### 1. SearXNG (Docker)

```bash
docker compose up -d
# Проверка: curl http://localhost:8081/
```

### 2. Python-окружение

```bash
python3 -m venv venv

# ВАЖНО: сначала устанавливаем CPU-only PyTorch (без гигабайтов CUDA)
venv/bin/pip install torch torchvision torchaudio \
  --index-url https://download.pytorch.org/whl/cpu

# затем остальные зависимости
venv/bin/pip install -r requirements.txt
```

Или одной командой:
```bash
make install-cpu
```

### 3. Модель нейро-ранжирования

Для `local_files_only=True` требуется локально скачанная модель.
Если модель не кэширована — выполни:

```bash
bash scripts/download_model.sh
```

Или просто запусти сервер — модель загрузится автоматически (при первом запуске).

### 4. Запуск

```bash
venv/bin/python server.py
```

Сервер работает по протоколу MCP (stdio). Подключается к Claude Code как MCP-сервер.

## Состав

| Файл | Назначение |
|------|-----------|
| `server.py` | Главный MCP-сервер (точка входа) |
| `config.py` | Конфигурация, настройка HF mirror, UTF-8 |
| `engine.py` | Поисковый движок (агрегация, дедупликация, реранк) |
| `backends.py` | Бэкенды поиска: DuckDuckGo + SearXNG |
| `reranker.py` | Нейро-ранжирование (Sentence Transformers) |
| `tools.py` | Вспомогательные утилиты (BeautifulSoup, Jina) |
| `network.py` | Сетевой слой (SSRF-защита, rate limit) |
| `cache.py` | Кэш результатов + rate limiter |
| `models.py` | Data-классы (SearchResult, ProcessedQuery) |
| `Makefile` | Цели: dev, install, check, clean |
| `docker-compose.yml` | SearXNG в Docker |
| `deploy.sh` | Полная установка одной командой |
| `scripts/download_model.sh` | Скачать модель для реранкера |
| `scripts/sca_check.sh` | Проверка безопасности зависимостей |
| `requirements.txt` | Python-зависимости |

## Инструменты MCP

| Инструмент | Описание |
|-----------|----------|
| `search_web(query, max_results, backend, rerank, diversity)` | Поиск с нейро-ранжированием |
| `read_webpage(url, markdown)` | Чтение веб-страницы (HTML → текст) |
| `browse_summarize(url, instr)` | Чтение + суммаризация |
| `web_research(query, depth)` | Глубокое исследование темы |

## Требования

- Python 3.10+
- Docker (для SearXNG)
- 2GB свободного места (модель ~175MB с кэшем)

## Для Claude Code CLI

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
