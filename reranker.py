import os
import time
import threading
import logging
from config import config, MODEL_CACHE_DIR

logger = logging.getLogger("SearchTool")

# Проверка библиотек
try:
    import torch
    import torch.nn.functional as F
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

# --- Исправление для окружений без FFmpeg ---
def _mock_torchcodec():
    import sys
    from types import ModuleType
    import importlib.util
    
    for name in ["torchcodec", "torchcodec.decoders"]:
        if name not in sys.modules:
            mock_mod = ModuleType(name)
            mock_mod.__path__ = []
            mock_mod.__spec__ = importlib.util.spec_from_loader(name, loader=None)
            if name == "torchcodec.decoders":
                class Dummy: pass
                mock_mod.AudioDecoder = Dummy
                mock_mod.VideoDecoder = Dummy
            sys.modules[name] = mock_mod

try:
    from sentence_transformers import SentenceTransformer, util
    TRANSFORMERS_AVAILABLE = True
    logger.info("✅ sentence-transformers imported successfully")
except Exception as e:
    if "libtorchcodec" in str(e) or "FFmpeg" in str(e):
        try:
            _mock_torchcodec()
            from sentence_transformers import SentenceTransformer, util
            TRANSFORMERS_AVAILABLE = True
            logger.info("✅ sentence-transformers imported successfully (with FFmpeg/torchcodec mock)")
        except Exception as e2:
            TRANSFORMERS_AVAILABLE = False
            logger.info(f"❌ sentence-transformers NOT available even after mock: {e2}")
    else:
        TRANSFORMERS_AVAILABLE = False
        logger.info(f"❌ sentence-transformers NOT available: {e}")
    
    if not TRANSFORMERS_AVAILABLE:
        # Fallback util for engine.py imports
        class DummyUtil:
            def cos_sim(self, a, b):
                return []
        util = DummyUtil()

# Обновляем конфиг
config.ENABLE_NEURAL_RERANK = TRANSFORMERS_AVAILABLE

class _NeuralRerankerSingleton:
    _instance = None
    _model = None
    _model_name = None
    _initialized = False
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                # ИСПРАВЛЕНО: двойная проверка внутри лока
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_instance(cls, model_name: str = None):
        model_name = model_name or config.NEURAL_MODEL_NAME

        # Быстрая проверка без лока
        if cls._initialized and cls._model_name == model_name:
            return cls._instance or cls()

        # ИСПРАВЛЕНО: полная проверка внутри лока
        with cls._lock:
            # Создаём экземпляр если нужно
            if cls._instance is None:
                cls._instance = super().__new__(cls)

            if cls._initialized and cls._model_name == model_name:
                return cls._instance

            cls._load_model(model_name)
            return cls._instance
    
    @classmethod
    def _load_model(cls, model_name: str):
        # ИСПРАВЛЕНО: вызывается только внутри get_instance с уже захваченным локом
        # или напрямую при инициализации — поэтому не берём лок повторно
        if cls._initialized and cls._model_name == model_name:
            return

        if not config.ENABLE_NEURAL_RERANK or not TRANSFORMERS_AVAILABLE:
            logger.warning("⚠️ Neural reranking disabled")
            cls._initialized = False
            return

        try:
            logger.info(f"🧠 Loading model: {model_name}")
            start_time = time.time()
            device = 'cpu' # if TORCH_AVAILABLE and torch.cuda.is_available() else 'cpu'
            logger.info(f"   Device: {device}")

            cls._model = SentenceTransformer(model_name, cache_folder=str(MODEL_CACHE_DIR), device=device, local_files_only=True)
            load_time = time.time() - start_time
            cls._model_name = model_name
            cls._initialized = True
            logger.info(f"✅ Model loaded in {load_time:.1f}s")
        except Exception as e:
            logger.error(f"❌ Failed to load model: {e}")
            cls._initialized = False
            cls._model = None
    
    @property
    def is_available(self) -> bool:
        return self._initialized and self._model is not None
    
    def encode(self, texts, **kwargs):
        if not self.is_available: raise RuntimeError("Model not loaded")
        return self._model.encode(texts, **kwargs)
    
    def similarity(self, a, b):
        if not self.is_available: raise RuntimeError("Model not loaded")
        return self._model.similarity(a, b)

NeuralReranker = _NeuralRerankerSingleton
