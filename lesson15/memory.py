"""取り込みパイプラインを、Azure なしで動かすための疑似の部品（コマ15。教員が用意したもの）

api/ingest.py の process_event は、Blob・インデックス・キャッシュ・埋め込みを引数で受け取る。
ここにある疑似の部品を渡せば、同じコードを Codespace の中だけで動かせる（simulate.py と test_ingest.py が使う）。
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "api"))

import embedding  # noqa: E402
import ingest  # noqa: E402


class MemoryBlobs:
    """Blob のコンテナー docs の代わり（名前 → 文字列）"""

    def __init__(self):
        self.files = {}

    def read_text(self, name):
        return self.files.get(name)


class MemoryIndex:
    """AI Search のインデックスの代わり（ID → 文書）"""

    def __init__(self):
        self.docs = {}

    def ids_for_source(self, source):
        return [i for i, d in self.docs.items() if d["source"] == source]

    def upload(self, documents):
        for d in documents:
            self.docs[d["id"]] = d

    def delete(self, ids):
        for i in ids:
            self.docs.pop(i, None)

    def count_by_source(self):
        counts = {}
        for d in self.docs.values():
            counts[d["source"]] = counts.get(d["source"], 0) + 1
        return dict(sorted(counts.items()))


class MemoryCache:
    """埋め込みキャッシュの代わり（キー → ベクトル）"""

    def __init__(self):
        self.items = {}

    def get(self, key):
        return self.items.get(key)

    def put(self, key, vector):
        self.items[key] = vector


class CountingEmbedder:
    """埋め込みモデルの代わり（モック埋め込み）。呼ばれた回数と、埋め込んだチャンクの数を数える"""

    def __init__(self):
        self.calls = 0
        self.texts = 0

    def __call__(self, texts):
        self.calls += 1
        self.texts += len(texts)
        return [embedding.mock_embed(t) for t in texts]


def event(kind, name):
    """Event Grid のイベントと同じ形の JSON の文字列を作る（必要な項目だけ）"""
    event_type = ingest.CREATED if kind == "created" else ingest.DELETED
    return json.dumps(
        {
            "eventType": event_type,
            "subject": f"/blobServices/default/containers/{ingest.CONTAINER}/blobs/{name}",
            "data": {"url": f"https://example.blob.core.windows.net/{ingest.CONTAINER}/{name}"},
        }
    )
