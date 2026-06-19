# config.py
import os
import sys
import warnings
from pathlib import Path
from dataclasses import dataclass
import urllib.request
import time

# ═══════════════════════════════════════════════════════════════
#  Принудительная настройка UTF-8 для всех платформ
#  Это решает проблему крокозябр в Windows консоли (cp866/cp1251)
#  и при перенаправлении вывода в файлы/пайпы
# ═══════════════════════════════════════════════════════════════

# Устанавливаем переменные окружения ДО импорта других модулей
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# Для Python 3.7+ — перенастраиваем стандартные потоки
def _setup_utf8():
    """Настройка UTF-8 для stdout/stderr"""

    # На Windows переключаем кодовую страницу консоли на UTF-8 (65001)
    if sys.platform == "win32":
        try:
            import ctypes
            # CP_UTF8 = 65001
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except (AttributeError, OSError, Exception):
            pass  # Если нет доступа — продолжаем без смены кодовой страницы

    # Python 3.7+ поддерживает sys.stdout.reconfigure()
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        if hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(encoding='utf-8', errors='replace')
                continue
            except (AttributeError, ValueError, OSError):
                pass

        # Fallback для старых версий или если reconfigure недоступен
        # Пересоздаём поток с UTF-8 кодировкой
        if sys.platform == "win32":
            import io
            try:
                if stream is sys.stdout and hasattr(sys.stdout, 'buffer'):
                    sys.stdout = io.TextIOWrapper(
                        sys.stdout.buffer, encoding='utf-8', errors='replace',
                        line_buffering=True
                    )
                elif stream is sys.stderr and hasattr(sys.stderr, 'buffer'):
                    sys.stderr = io.TextIOWrapper(
                        sys.stderr.buffer, encoding='utf-8', errors='replace',
                        line_buffering=True
                    )
            except (AttributeError, OSError):
                pass  # Игнорируем ошибки — используем то, что есть

_setup_utf8()

warnings.filterwarnings("ignore")
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["PYTHONWARNINGS"] = "ignore"
os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "30"

# Очищаем SOCKS-прокси — они мешают загрузке моделей huggingface_hub,
# а для рабочего HTTP-трафика (поиск, чтение страниц) достаточно HTTP_PROXY
for _var in ["ALL_PROXY", "all_proxy"]:
    os.environ.pop(_var, None)

# Путь к моделям в корне проекта
MODEL_CACHE_DIR = Path(__file__).parent / "models"
MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

os.environ["HF_HOME"] = str(MODEL_CACHE_DIR)
os.environ["TRANSFORMERS_CACHE"] = str(MODEL_CACHE_DIR / "transformers")
os.environ["HUGGINGFACE_HUB_CACHE"] = str(MODEL_CACHE_DIR / "hub")

SELECTED_MIRROR = None

def initialize_hf_mirror():
    """Явная инициализация зеркала. Вызывается из server.py"""
    global SELECTED_MIRROR
    
    # Если уже настроено, пропускаем
    if SELECTED_MIRROR:
        return

    sys.stderr.write("=" * 60 + "\n")
    sys.stderr.write("🔧 Configuring HuggingFace mirrors...\n")
    sys.stderr.write("=" * 60 + "\n")
    
    HF_MIRRORS = ["https://hf-mirror.com", "https://huggingface.co"]
    
    try:
        for mirror in HF_MIRRORS:
            try:
                sys.stderr.write(f"⏳ Testing: {mirror} ... ")
                sys.stderr.flush()
                start = time.time()
                req = urllib.request.Request(mirror, headers={"User-Agent": "MCP-SearchTool/3.4"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    elapsed = time.time() - start
                    if resp.status == 200:
                        SELECTED_MIRROR = mirror
                        os.environ["HF_ENDPOINT"] = mirror
                        sys.stderr.write(f"✅ OK ({elapsed:.2f}s)\n")
                        break
            except Exception as e:
                sys.stderr.write(f"❌ Failed\n")
        
        if not SELECTED_MIRROR:
            SELECTED_MIRROR = "https://huggingface.co"
            os.environ["HF_ENDPOINT"] = SELECTED_MIRROR
            
    except Exception as e:
        sys.stderr.write(f"⚠️ Mirror detection failed: {e}\n")
        SELECTED_MIRROR = "https://huggingface.co"
        os.environ["HF_ENDPOINT"] = SELECTED_MIRROR

    sys.stderr.write(f"🎯 Selected endpoint: {SELECTED_MIRROR}\n")

@dataclass
class SearchConfig:
    REQUEST_TIMEOUT: int = 15
    MAX_REDIRECTS: int = 5
    MAX_CONTENT_LENGTH: int = 15000
    MAX_CONTENT_SIZE: int = 3 * 1024 * 1024
    MAX_CONCURRENT: int = 8
    MAX_SEARCH_RESULTS: int = 12
    DEFAULT_MAX_RESULTS: int = 8
    CACHE_TTL_SECONDS: int = 3600
    CACHE_MAX_SIZE: int = 1000
    ENABLE_NEURAL_RERANK: bool = True
    NEURAL_MODEL_NAME: str = "ms-marco-MultiBERT-L-12"
    TOP_K_RERANK: int = 20
    ENABLE_DIVERSITY: bool = True
    DIVERSITY_LAMBDA: float = 0.7
    RATE_LIMIT_REQUESTS: int = 30

config = SearchConfig()