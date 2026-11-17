"""AI Search の代わりに使う、疑似の検索（コマ16。教員が用意したもの）

test_rag.py と、ask_cli.py --local が使う。lesson16/vector_store.json を読み、
ベクトルのコサイン類似度で並べる（ハイブリッド検索ではない）。
絞り込みの式は、security_filter が作る形（allowedGroups/any(g: search.in(g, '…', ','))）だけを理解する。
"""

import math
import re

_FILTER = re.compile(r"allowedGroups/any\(g: search\.in\(g, '([^']*)', ','\)\)")


class MemorySearchClient:
    def __init__(self, records):
        self.records = records
        self.last_filter = "（まだ検索していない）"

    def search(self, select=None, search_text=None, vector_queries=None, top=3, filter=None):
        self.last_filter = filter
        allowed = None
        if filter is not None:
            m = _FILTER.fullmatch(filter)
            if m is None:
                raise ValueError(f"疑似の検索では理解できない絞り込みの式: {filter}")
            allowed = set(m.group(1).split(","))
        vector = vector_queries[0].vector if vector_queries else None

        def cosine(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
            return dot / norm if norm else 0.0

        candidates = [r for r in self.records if allowed is None or allowed & set(r["groups"])]
        scored = sorted(candidates, key=lambda r: cosine(vector, r["embedding"]) if vector else 0.0, reverse=True)
        return [
            {"source": r["source"], "content": r["text"], "@search.score": cosine(vector, r["embedding"]) if vector else 0.0}
            for r in scored[:top]
        ]
