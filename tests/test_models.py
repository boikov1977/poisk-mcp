"""Smoke tests for models"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import SearchResult, ProcessedQuery
from engine import QueryProcessor


def test_search_result_defaults():
    r = SearchResult(title="Test", url="http://example.com", snippet="Snippet")
    assert r.score == 0.0
    assert r.rank == 0
    assert r.source_backend == ""
    assert r.features == {}


def test_search_result_full():
    r = SearchResult(
        title="T", url="http://x.com", snippet="S",
        score=0.95, rank=1, source_backend="ddgs",
        features={"neural": 0.8}
    )
    assert r.score == 0.95
    assert r.features["neural"] == 0.8


def test_processed_query():
    pq = ProcessedQuery(original="test query", normalized="test query")
    assert pq.get_search_query() == "test query"


def test_processed_query_with_expanded():
    pq = ProcessedQuery(
        original="test", normalized="test",
        expanded_terms=["+extra"]
    )
    assert "extra" in pq.get_search_query()


def test_query_processor():
    pq = QueryProcessor.process("hello world")
    assert pq.original == "hello world"
    assert pq.intent == "informational"
    assert "hello" in pq.get_search_query()


def test_query_processor_too_short():
    import pytest
    with pytest.raises(ValueError):
        QueryProcessor.process("x")


def test_query_processor_strips():
    pq = QueryProcessor.process("  spaced  query  ")
    assert pq.original == "spaced  query"
