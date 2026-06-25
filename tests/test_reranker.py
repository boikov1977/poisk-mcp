"""Tests for reranker.py — NeuralReranker singleton"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock
import reranker as rr


def test_singleton_identity():
    """Singleton: два вызова get_instance дают один и тот же объект"""
    r1 = rr.NeuralReranker.get_instance()
    r2 = rr.NeuralReranker.get_instance()
    assert r1 is r2


def test_not_available_when_flashrank_false():
    """Когда FLASHRANK_AVAILABLE=False — reranker не доступен"""
    with patch.object(rr, "FLASHRANK_AVAILABLE", False):
        # Сбросим singleton, чтобы перезагрузить модель
        rr.NeuralReranker._initialized = False
        rr.NeuralReranker._model = None
        r = rr.NeuralReranker.get_instance("ms-marco-MultiBERT-L-12")
        assert r.is_available is False
        # Восстановим для других тестов
        rr.NeuralReranker._initialized = False
        rr.NeuralReranker._model = None


def test_supports_embeddings_false():
    """FlashRank cross-encoder не умеет эмбеддинги"""
    r = rr.NeuralReranker.get_instance()
    assert r.supports_embeddings is False


def test_encode_raises():
    """Вызов encode() должен бросать NotImplementedError"""
    r = rr.NeuralReranker.get_instance()
    with pytest.raises(NotImplementedError):
        r.encode(["hello"])


def test_similarity_raises():
    """Вызов similarity() должен бросать NotImplementedError"""
    r = rr.NeuralReranker.get_instance()
    with pytest.raises(NotImplementedError):
        r.similarity("a", "b")


def test_rerank_not_available_raises():
    """Если модель не загружена — rerank должен бросать RuntimeError"""
    with patch.object(rr, "FLASHRANK_AVAILABLE", False):
        rr.NeuralReranker._initialized = False
        rr.NeuralReranker._model = None
        r = rr.NeuralReranker.get_instance()
        with pytest.raises(RuntimeError, match="Model not loaded"):
            r.rerank("query", ["text1", "text2"])
        rr.NeuralReranker._initialized = False
        rr.NeuralReranker._model = None


def test_rerank_maps_scores():
    """Если FlashRank работает — rerank возвращает скоры в том же порядке"""
    fake_model = MagicMock()
    # FlashRank возвращает отсортированный список
    fake_model.rerank.return_value = [
        {"id": 1, "score": 0.9},
        {"id": 0, "score": 0.5},
    ]

    # Создаём фейковый Ranker класс на модуле
    fake_model = MagicMock()
    fake_model.rerank.return_value = [
        {"id": 1, "score": 0.9},
        {"id": 0, "score": 0.5},
    ]

    FakeRanker = MagicMock(return_value=fake_model)
    FakeRerankRequest = MagicMock()

    with patch.object(rr, "FLASHRANK_AVAILABLE", True):
        rr.NeuralReranker._initialized = True
        rr.NeuralReranker._model = fake_model
        rr.NeuralReranker._model_name = "test-model"
        # Временно вешаем фейковые классы
        orig_ranker = getattr(rr, "Ranker", None)
        orig_request = getattr(rr, "RerankRequest", None)
        rr.Ranker = FakeRanker
        rr.RerankRequest = FakeRerankRequest
        try:
            r = rr.NeuralReranker.get_instance()
            scores = r.rerank("hello", ["world", "python"])
            assert scores == [0.5, 0.9]
        finally:
            if orig_ranker is not None:
                rr.Ranker = orig_ranker
            else:
                delattr(rr, "Ranker")
            if orig_request is not None:
                rr.RerankRequest = orig_request
            else:
                if hasattr(rr, "RerankRequest"):
                    delattr(rr, "RerankRequest")

    rr.NeuralReranker._initialized = False
    rr.NeuralReranker._model = None
    rr.NeuralReranker._model_name = None


def test_load_model_exception():
    """Ошибка загрузки модели — _initialized=False"""
    FakeRanker = MagicMock(side_effect=Exception("Disk full"))
    with patch.object(rr, "FLASHRANK_AVAILABLE", True):
        orig_ranker = getattr(rr, "Ranker", None)
        rr.Ranker = FakeRanker
        try:
            rr.NeuralReranker._initialized = False
            rr.NeuralReranker._model = None
            rr.NeuralReranker._model_name = None
            r = rr.NeuralReranker.get_instance("fail-model")
            assert r.is_available is False
        finally:
            if orig_ranker is not None:
                rr.Ranker = orig_ranker
            else:
                if hasattr(rr, "Ranker"):
                    delattr(rr, "Ranker")
    rr.NeuralReranker._initialized = False
    rr.NeuralReranker._model = None
    rr.NeuralReranker._model_name = None


import pytest
