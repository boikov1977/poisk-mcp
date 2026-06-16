from dataclasses import dataclass, field
from typing import List, Dict, Any

@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    score: float = 0.0
    rank: int = 0
    source_backend: str = ""
    features: Dict[str, Any] = field(default_factory=dict)

@dataclass 
class ProcessedQuery:
    original: str
    normalized: str
    tokens: List[str]
    entities: List[str]
    intent: str
    expanded_terms: List[str] = field(default_factory=list)
    language: str = "auto"
    
    def get_search_query(self) -> str:
        query = " ".join([self.normalized] + self.expanded_terms)
        return query if query.strip() else self.original