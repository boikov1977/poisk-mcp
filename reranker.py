import os
import time
import threading
import logging
from config import config, MODEL_CACHE_DIR

logger = logging.getLogger("SearchTool")

# Флаги для обратной совместимости (больше не нужны, но engine.py импортирует)
TORCH_AVAILABLE = False
NUMPY_AVAILABLE = False

# FlashRank — лёгкий cross-encoder через ONNX Runtime
FLASHRANK_AVAILABLE = False
try:
    from flashrank import Ranker, RerankRequest
    FLASHRANK_AVAILABLE = True
    logger.info("✅ FlashRank imported successfully")
except Exception as e:
    logger.warning(f"❌ FlashRank not available: {e}")

TRANSFORMERS_AVAILABLE = FLASHRANK_AVAILABLE

# Заглушка для cos_sim — не используется с cross-encoder, но engine.py импортирует util
class DummyUtil:
    def cos_sim(self, a, b):
        return []
util = DummyUtil()

# Обновляем конфиг
config.ENABLE_NEURAL_RERANK = FLASHRANK_AVAILABLE


class _NeuralRerankerSingleton:
    _instance = None
    _model = None
    _model_name = None
    _initialized = False
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_instance(cls, model_name: str = None):
        model_name = model_name or config.NEURAL_MODEL_NAME

        if cls._initialized and cls._model_name == model_name:
            return cls._instance or cls()

        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
            if cls._initialized and cls._model_name == model_name:
                return cls._instance
            cls._load_model(model_name)
            return cls._instance

    @classmethod
    def _load_model(cls, model_name: str):
        if cls._initialized and cls._model_name == model_name:
            return

        if not FLASHRANK_AVAILABLE:
            logger.warning("⚠️ FlashRank not available, neural reranking disabled")
            cls._initialized = False
            return

        try:
            logger.info(f"🧠 Loading FlashRank model: {model_name}")
            start_time = time.time()

            # FlashRank сам скачивает ONNX-модель при первом запуске
            cls._model = Ranker(model_name=model_name)

            load_time = time.time() - start_time
            cls._model_name = model_name
            cls._initialized = True
            logger.info(f"✅ FlashRank model loaded in {load_time:.1f}s")
        except Exception as e:
            logger.error(f"❌ Failed to load FlashRank model: {e}")
            cls._initialized = False
            cls._model = None

    @property
    def is_available(self) -> bool:
        return self._initialized and self._model is not None

    @property
    def supports_embeddings(self) -> bool:
        """FlashRank — cross-encoder, не умеет делать эмбеддинги."""
        return False

    def encode(self, texts, **kwargs):
        """
        Не поддерживается в FlashRank.
        DiversityEngine падает на domain-based diversity.
        """
        raise NotImplementedError(
            "FlashRank cross-encoder не поддерживает encode(). "
            "Используйте rerank() для скоринга пар запрос-документ."
        )

    def rerank(self, query, texts):
        """
        Оценивает релевантность каждого текста относительно запроса.
        Возвращает список оценок (float) в том же порядке, что и texts.
        """
        if not self.is_available:
            raise RuntimeError("Model not loaded")

        passages = [{"id": i, "text": t} for i, t in enumerate(texts)]
        req = RerankRequest(query=query, passages=passages)
        result = self._model.rerank(req)

        # result — список {"id": ..., "text": ..., "score": ..., ...} от лучшего к худшему
        scores = [0.0] * len(texts)
        for item in result:
            scores[item["id"]] = item["score"]

        return scores

    def similarity(self, a, b):
        raise NotImplementedError(
            "FlashRank cross-encoder не поддерживает similarity(). "
            "Используйте rerank()."
        )


NeuralReranker = _NeuralRerankerSingleton
