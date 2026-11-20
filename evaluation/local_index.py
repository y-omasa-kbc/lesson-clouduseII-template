"""評価用の、ローカルの索引と検索（コマ17。教員が用意したもの）

AI Search の代わりに、この Codespace の中だけで検索する。Azure にも OpenRouter にもつながない。

  1. evaluation/docs/*.txt を、settings.json の chunking の方法でチャンクに分ける
  2. 各チャンクをモック埋め込みでベクトルにする（api/embedding.py の mock_embed。費用はかからない）
  3. 権限（evaluation/acl.json）を、チャンクのすべてに付ける
  4. SearchClient と同じ呼び方（search(search_text=…, vector_queries=…, top=…, filter=…)）で検索できる
       search_text だけ        → 全文検索（2文字ずつの並びで、BM25 に似た点数を付ける）
       vector_queries だけ     → ベクトル検索（コサイン類似度）
       両方                    → ハイブリッド検索（2つの順位を RRF でまとめる）

AI Search の全文検索（ja.microsoft アナライザー）やハイブリッド検索とは、点数の計算が違う。
順位の傾向は似せてあるが、同じにはならない。評価の仕組みを学ぶための、小さな代用品である。
本物の埋め込みを使う構成でチャンクの分け方を変えると、全部のチャンクを埋め込み直す費用がかかる（コマ15）。
"""

import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "api"))

import embedding  # noqa: E402
import ingest  # noqa: E402

CHUNKINGS = ("qa", "file", "fixed")
FIXED_SIZE = 80  # chunking が fixed のときの、1チャンクの文字数
RRF_K = 60  # RRF の定数（AI Search と同じ値）

_FILTER = re.compile(r"allowedGroups/any\(g: search\.in\(g, '([^']*)', ','\)\)")
_TERM = re.compile(r"[ぁ-んァ-ヶ一-龠ー]+|[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")


# =====================================================================
# 1. チャンクに分ける
# =====================================================================
def split(text, chunking):
    if chunking == "qa":
        return ingest.split_chunks(text)  # 空行で分ける（Q と A の組ごと。コマ15の取り込みと同じ）
    if chunking == "file":
        return [text.strip()]  # 文書をまるごと1チャンクにする
    if chunking == "fixed":
        flat = text.strip()
        return [flat[i : i + FIXED_SIZE] for i in range(0, len(flat), FIXED_SIZE)]  # 文字数で機械的に切る
    raise ValueError(f"chunking は {CHUNKINGS} のどれか: {chunking}")


def build_records(chunking):
    acl = json.loads((HERE / "acl.json").read_text(encoding="utf-8"))
    records = []
    for path in sorted((HERE / "docs").glob("*.txt")):
        source = f"docs/{path.name}"
        groups = acl[source]  # 権限が書かれていない文書があれば、ここで止まる（権限なしで索引に入れない）
        for chunk in split(path.read_text(encoding="utf-8"), chunking):
            records.append({"source": source, "text": chunk, "groups": groups, "embedding": embedding.mock_embed(chunk)})
    return records


# =====================================================================
# 2. 検索
# =====================================================================
def terms(text):
    """全文検索の語: 日本語は2文字ずつの並び、英数字（E-4021 など）はひとまとまり"""
    result = []
    for run in _TERM.findall(text):
        if run[0].isascii():
            result.append(run.lower())
        elif len(run) == 1:
            result.append(run)
        else:
            result.extend(run[i : i + 2] for i in range(len(run) - 1))
    return result


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


class LocalSearchClient:
    def __init__(self, records):
        self.records = records
        self.calls = 0
        self._docs = [Counter(terms(r["text"])) for r in records]
        self._avg = sum(sum(d.values()) for d in self._docs) / max(len(self._docs), 1)
        n = len(records)
        df = Counter(t for d in self._docs for t in d)
        self._idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def _bm25(self, i, query_terms, k1=1.2, b=0.75):
        doc = self._docs[i]
        length = sum(doc.values())
        score = 0.0
        for t in query_terms:
            if t in doc:
                tf = doc[t]
                score += self._idf[t] * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / self._avg))
        return score

    def search(self, select=None, search_text=None, vector_queries=None, top=3, filter=None):
        self.calls += 1
        allowed = None
        if filter is not None:
            m = _FILTER.fullmatch(filter)
            if m is None:
                raise ValueError(f"ローカルの検索では理解できない絞り込みの式: {filter}")
            allowed = set(m.group(1).split(","))
        candidates = [i for i, r in enumerate(self.records) if allowed is None or allowed & set(r["groups"])]

        ranked_lists = []
        if search_text:
            q = set(terms(search_text))
            scored = [(self._bm25(i, q), i) for i in candidates]
            ranked_lists.append([i for s, i in sorted(scored, key=lambda x: -x[0]) if s > 0])
        if vector_queries:
            v = vector_queries[0].vector
            scored = [(cosine(v, self.records[i]["embedding"]), i) for i in candidates]
            ranked_lists.append([i for s, i in sorted(scored, key=lambda x: -x[0])])

        if len(ranked_lists) == 1:
            order = ranked_lists[0]
            scores = {i: 1.0 / (rank + 1) for rank, i in enumerate(order)}
        else:  # RRF: 各方式の順位 r から 1/(K + r) を足し合わせる
            scores = Counter()
            for order in ranked_lists:
                for rank, i in enumerate(order, 1):
                    scores[i] += 1.0 / (RRF_K + rank)
            order = sorted(scores, key=lambda i: -scores[i])
        return [
            {"source": self.records[i]["source"], "content": self.records[i]["text"], "@search.score": scores[i]}
            for i in order[:top]
        ]


def groups_of(records):
    """チャンクの本文 → 権限（漏洩の確認に使う）"""
    return {(r["source"], r["text"]): set(r["groups"]) for r in records}
