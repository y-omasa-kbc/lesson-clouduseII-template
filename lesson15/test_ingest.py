"""api/ingest.py を確かめるテスト（コマ15）

実行のしかた（Codespace のターミナルで。Azure にも OpenRouter にもつながない）
  pip install pytest azure-search-documents azure-identity azure-storage-blob
  python -m pytest lesson15/test_ingest.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from memory import CountingEmbedder, MemoryBlobs, MemoryCache, MemoryIndex, event  # noqa: E402

import ingest  # noqa: E402

DOC = "Q. 質問1\nA. 答え1\n\nQ. 質問2\nA. 答え2\n\nQ. 質問3\nA. 答え3\n"
DOC_V2 = "Q. 質問1\nA. 答え1\n\nQ. 質問2\nA. 答え2（改訂）\n\nQ. 質問4\nA. 答え4\n"


def setup():
    return MemoryBlobs(), MemoryIndex(), MemoryCache(), CountingEmbedder()


def run(parts, kind, name, model="mock"):
    blobs, index, cache, embedder = parts
    return ingest.process_event(event(kind, name), blobs, index, cache, embedder, model)


# ---------------------------------------------------------------------
# 教員が用意した部分（最初から通る）
# ---------------------------------------------------------------------
def test_イベント_対象のコンテナーのtxtだけを読む():
    assert ingest.parse_event(event("created", "a.txt")) == (ingest.CREATED, "a.txt")
    assert ingest.parse_event(event("created", "a.pdf")) is None
    other = event("created", "a.txt").replace("/containers/docs/", "/containers/other/")
    assert ingest.parse_event(other) is None


def test_チャンク_内容が同じならIDも同じ():
    a = ingest.make_chunks("docs/a.txt", DOC)
    b = ingest.make_chunks("docs/a.txt", DOC.replace("\n", "\r\n"))
    assert [c["id"] for c in a] == [c["id"] for c in b]
    assert len(a) == 3


def test_チャンク_内容が変わればIDも変わる():
    a = {c["content"]: c["id"] for c in ingest.make_chunks("docs/a.txt", DOC)}
    b = {c["content"]: c["id"] for c in ingest.make_chunks("docs/a.txt", DOC_V2)}
    assert a["Q. 質問1\nA. 答え1"] == b["Q. 質問1\nA. 答え1"]
    assert "Q. 質問2\nA. 答え2" not in b


def test_絞り込みの文字列_引用符を二重にする():
    assert ingest.odata_string("docs/it's.txt") == "'docs/it''s.txt'"


def test_対象外のイベントは無視する():
    parts = setup()
    assert run(parts, "created", "image.png")["action"] == "ignored"


# ---------------------------------------------------------------------
# 埋め込みキャッシュ（演習手順5）
# ---------------------------------------------------------------------
def test_キャッシュ_キーに埋め込みの方法が入る():
    assert ingest.cache_key("model-a", "abc") != ingest.cache_key("model-b", "abc")


def test_キャッシュ_キーは決まった値でキーに使える文字だけ():
    key = ingest.cache_key("model-a", "abc")
    assert key == ingest.cache_key("model-a", "abc")
    assert key.isalnum()


def test_キャッシュ_2回目は埋め込まない():
    cache, embedder = MemoryCache(), CountingEmbedder()
    chunks = ingest.make_chunks("docs/a.txt", DOC)
    first, n1 = ingest.embed_with_cache(chunks, "mock", cache, embedder)
    second, n2 = ingest.embed_with_cache(chunks, "mock", cache, embedder)
    assert (n1, n2) == (3, 0)
    assert first == second
    assert embedder.calls == 1


def test_キャッシュ_ないものだけを1回でまとめて埋め込む():
    cache, embedder = MemoryCache(), CountingEmbedder()
    ingest.embed_with_cache(ingest.make_chunks("docs/a.txt", DOC), "mock", cache, embedder)
    chunks = ingest.make_chunks("docs/b.txt", DOC_V2)  # 3つのうち2つ（改訂した質問2と、質問4）がキャッシュにない。キーは内容だけで決まり、文書の名前によらない
    vectors, n = ingest.embed_with_cache(chunks, "mock", cache, embedder)
    assert n == 2
    assert embedder.calls == 2 and embedder.texts == 5
    assert len(vectors) == 3 and all(v is not None for v in vectors)


def test_キャッシュ_方法を変えたら埋め込み直す():
    cache, embedder = MemoryCache(), CountingEmbedder()
    chunks = ingest.make_chunks("docs/a.txt", DOC)
    ingest.embed_with_cache(chunks, "model-a", cache, embedder)
    _, n = ingest.embed_with_cache(chunks, "model-b", cache, embedder)
    assert n == 3


# ---------------------------------------------------------------------
# 差分更新と削除（演習手順6）
# ---------------------------------------------------------------------
def test_差分_変わったものだけを投入し古いものを消す():
    old = ingest.make_chunks("docs/a.txt", DOC)
    new = ingest.make_chunks("docs/a.txt", DOC_V2)
    to_upload, to_delete = ingest.plan_update([c["id"] for c in old], new)
    assert sorted(c["content"] for c in to_upload) == ["Q. 質問2\nA. 答え2（改訂）", "Q. 質問4\nA. 答え4"]
    old_ids = {c["content"]: c["id"] for c in old}
    assert to_delete == sorted([old_ids["Q. 質問2\nA. 答え2"], old_ids["Q. 質問3\nA. 答え3"]])


def test_差分_同じ内容で置き直しても何もしない():
    chunks = ingest.make_chunks("docs/a.txt", DOC)
    assert ingest.plan_update([c["id"] for c in chunks], chunks) == ([], [])


def test_処理_差し替えるとインデックスが新しい内容と同じになる():
    parts = setup()
    blobs, index, _, _ = parts
    blobs.files["a.txt"] = DOC
    run(parts, "created", "a.txt")
    blobs.files["a.txt"] = DOC_V2
    result = run(parts, "created", "a.txt")
    assert (result["uploaded"], result["deleted"], result["embedded"]) == (2, 2, 2)
    assert sorted(d["content"] for d in index.docs.values()) == sorted(ingest.split_chunks(DOC_V2))


def test_処理_消すとその文書のチャンクだけが消える():
    parts = setup()
    blobs, index, _, _ = parts
    blobs.files["a.txt"] = DOC
    blobs.files["b.txt"] = DOC_V2
    run(parts, "created", "a.txt")
    run(parts, "created", "b.txt")
    del blobs.files["a.txt"]
    result = run(parts, "deleted", "a.txt")
    assert result["action"] == "deleted" and result["deleted"] == 3
    assert index.count_by_source() == {"docs/b.txt": 3}


def test_処理_同じイベントを2回処理しても結果は同じ():
    parts = setup()
    blobs, index, _, embedder = parts
    blobs.files["a.txt"] = DOC
    run(parts, "created", "a.txt")
    snapshot = dict(index.docs)
    again = run(parts, "created", "a.txt")
    assert index.docs == snapshot and again["uploaded"] == 0 and embedder.calls == 1
    del blobs.files["a.txt"]
    run(parts, "deleted", "a.txt")
    assert run(parts, "deleted", "a.txt")["deleted"] == 0


def test_処理_作られたイベントでもBlobがなければ消す():
    parts = setup()
    blobs, index, _, _ = parts
    blobs.files["a.txt"] = DOC
    run(parts, "created", "a.txt")
    del blobs.files["a.txt"]  # イベントが処理される前に、文書が消された
    assert run(parts, "created", "a.txt")["action"] == "deleted"
    assert index.docs == {}
