"""文書の取り込みパイプライン（コマ15）: Blob に置いた文書を、自動でインデックスに反映する

流れ（イベント1つを、1回処理する）
  Blob のコンテナー docs に、文書を置く・差し替える・消す
    → Event Grid が、イベント（BlobCreated・BlobDeleted）をキュー ingest-events に入れる
    → キュー トリガーの関数 ingest_worker が、process_event を呼ぶ
        1. イベントから、どの文書か（Blob の名前）を読む                    parse_event
        2. その文書の「いまの」内容を Blob から読む。なければ、消された文書として扱う
        3. チャンクに分け、チャンクの内容のハッシュから ID を決める          make_chunks
        4. インデックスにある、その文書の ID と比べて、足すもの・消すものを決める  plan_update
        5. 足すチャンクだけを、埋め込みキャッシュを使って埋め込む            embed_with_cache
        6. 足すものを投入してから、要らなくなったものを消す

イベントの種類（作られた・消された）ではなく、処理する時点の Blob の状態に合わせる。
だから、同じイベントを何度処理しても、イベントの順番が入れ替わっても、最後は Blob と同じ状態になる（冪等。コマ10）。

外部のもの（Blob・インデックス・キャッシュ・埋め込みモデル）は、引数で受け取る。
だから、このファイルの関数は Azure にも OpenRouter にもつながずにテストできる（lesson15/memory.py の疑似の部品を使う）。
"""

import hashlib
import json

CONTAINER = "docs"  # 文書を置く Blob のコンテナー
QUEUE_NAME = "ingest-events"  # Event Grid がイベントを入れるキュー
POISON_QUEUE_NAME = QUEUE_NAME + "-poison"
CREATED = "Microsoft.Storage.BlobCreated"
DELETED = "Microsoft.Storage.BlobDeleted"
MAX_BYTES = 200_000  # 1文書の大きさの上限。大きすぎる文書で、埋め込みの費用が膨らまないようにする
MAX_CHUNKS = 100  # 1文書のチャンクの数の上限


class IngestError(Exception):
    """何度やり直しても直らない失敗（文書の形が正しくない・大きすぎる）。再実行せずに「失敗」として記録する"""


# =====================================================================
# 1. イベントを読む
# =====================================================================
def parse_event(body):
    """Event Grid のイベント（JSON の文字列）から、(イベントの種類, Blob の名前) を返す。対象外なら None

    subject の例: /blobServices/default/containers/docs/blobs/faq_it.txt
    """
    event = json.loads(body)
    kind = event.get("eventType")
    subject = str(event.get("subject", ""))
    prefix = f"/blobServices/default/containers/{CONTAINER}/blobs/"
    if kind not in (CREATED, DELETED) or not subject.startswith(prefix):
        return None
    name = subject[len(prefix):]
    if not name.endswith(".txt") or "/" in name:
        return None
    return kind, name


def source_of(name):
    """インデックスの source の値（コマ14の配布データと同じ形）"""
    return f"docs/{name}"


# =====================================================================
# 2. チャンクに分け、ID を決める
# =====================================================================
def content_hash(text):
    """チャンクの内容のハッシュ。内容が1文字でも変われば、別の値になる"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_id(source, digest):
    """チャンクの ID。文書の名前と内容のハッシュから決める

    同じ文書の同じ内容のチャンクは、いつも同じ ID になる。内容が変われば、ID も変わる。
    """
    return hashlib.sha256(f"{source}#{digest}".encode("utf-8")).hexdigest()


def split_chunks(text):
    """空行で区切って、Q と A の組ごとのチャンクにする（コマ14の配布データと同じ分け方）"""
    text = text.replace("\r\n", "\n")
    return [block.strip() for block in text.split("\n\n") if block.strip()]


def make_chunks(source, text):
    """文書をチャンクのリストにする。チャンクは {"id", "source", "content", "hash"}"""
    blocks = split_chunks(text)
    if len(blocks) > MAX_CHUNKS:
        raise IngestError(f"チャンクが多すぎる（{len(blocks)} 個。上限は {MAX_CHUNKS} 個）")
    chunks, seen = [], set()
    for block in blocks:
        digest = content_hash(block)
        cid = chunk_id(source, digest)
        if cid in seen:  # 同じ文書の中に、まったく同じチャンクが2つあれば、1つにする
            continue
        seen.add(cid)
        chunks.append({"id": cid, "source": source, "content": block, "hash": digest})
    return chunks


# =====================================================================
# 3. 埋め込みキャッシュ
# =====================================================================
def cache_key(model, digest):
    """埋め込みキャッシュのキー"""
    # TODO(コマ15 演習手順5-1): キーに、埋め込みの方法（model）も入れる。
    #   f"{model}#{digest}" のハッシュ（content_hash を使う）を返す。
    #   いまは内容のハッシュだけなので、埋め込みモデルを変えても、前のモデルのベクトルが使われてしまう。
    return digest


def embed_with_cache(chunks, model, cache, embed_many):
    """chunks の埋め込みを返す。キャッシュにあるものは使い、ないものだけを、まとめて1回で埋め込む

      cache      : get(key) → ベクトル（なければ None）、put(key, ベクトル)
      embed_many : 文字列のリスト → ベクトルのリスト（埋め込みモデルを呼ぶ。本物なら費用がかかる）
      戻り値     : (ベクトルのリスト（chunks と同じ順）, 新たに埋め込んだ数)
    """
    # TODO(コマ15 演習手順5-2): キャッシュを使う。
    #   1. 各チャンクのキー cache_key(model, c["hash"]) で cache.get し、なかったものの位置を集める
    #   2. なかったものがあれば、その本文だけを embed_many に1回で渡し、結果を cache.put で保存する
    #   3. (chunks と同じ順のベクトルのリスト, なかったものの数) を返す
    #   いまは、毎回すべてのチャンクを埋め込んでいる。
    return embed_many([c["content"] for c in chunks]), len(chunks)


# =====================================================================
# 4. 差分更新
# =====================================================================
def plan_update(existing_ids, chunks):
    """インデックスにある ID（existing_ids）と、新しいチャンクを比べて、(投入するチャンク, 消す ID) を返す

      投入する: 新しいチャンクのうち、ID がインデックスにないもの（内容が変わった・増えたチャンク）
      消す    : インデックスにある ID のうち、新しいチャンクにないもの（内容が変わる前・なくなったチャンク）
      ID が両方にあるチャンクは、内容が同じなので、何もしない
    """
    # TODO(コマ15 演習手順6-1): 上のとおりに、投入するチャンクと、消す ID を決める。
    #   消す ID は sorted で並べて返す（テストで比べやすくするため）。
    #   いまは、すべてのチャンクを投入し、何も消していない。差し替えた文書の古いチャンクが、検索に残ってしまう。
    return list(chunks), []


# =====================================================================
# 5. イベント1つを処理する
# =====================================================================
def process_event(body, blobs, index, cache, embed_many, model):
    """イベント1つを処理して、結果（記録用の辞書）を返す

      blobs : read_text(name) → 文書の文字列（Blob がなければ None）
      index : ids_for_source(source) → ID のリスト、upload(文書のリスト)、delete(ID のリスト)
    """
    parsed = parse_event(body)
    if parsed is None:
        return {"action": "ignored"}
    kind, name = parsed
    source = source_of(name)
    result = {"event": kind.rsplit(".", 1)[-1], "source": source}

    # イベントの種類ではなく、いまの Blob の状態に合わせる
    text = blobs.read_text(name)
    if text is None:
        # TODO(コマ15 演習手順6-2): Blob がない（消された）ときは、その文書のチャンクを、インデックスからすべて消す。
        #   index.ids_for_source(source) で ID を集めて index.delete に渡し、
        #   result に "action": "deleted" と "deleted": 消した数 を入れて返す。
        #   いまは何もしていない。消した文書が、検索に残ってしまう。
        return {**result, "action": "missing", "deleted": 0}

    chunks = make_chunks(source, text)
    to_upload, to_delete = plan_update(index.ids_for_source(source), chunks)
    vectors, embedded = embed_with_cache(to_upload, model, cache, embed_many)
    index.upload(
        [
            {"id": c["id"], "source": c["source"], "content": c["content"], "contentVector": v}
            for c, v in zip(to_upload, vectors)
        ]
    )
    index.delete(to_delete)  # 先に新しいものを入れてから、古いものを消す（途中で検索しても、文書が空にならない）
    return {
        **result,
        "action": "updated",
        "chunks": len(chunks),
        "uploaded": len(to_upload),
        "deleted": len(to_delete),
        "embedded": embedded,
        "cacheHits": len(to_upload) - embedded,
    }


# =====================================================================
# Azure の部品（function_app.py と lesson15/ の道具から使う。教員が用意したもの）
# =====================================================================
def odata_string(value):
    """OData の絞り込みの式に入れる文字列。' を '' にする（そうしないと、式を書き換えられてしまう）"""
    return "'" + str(value).replace("'", "''") + "'"


class SearchIndexStore:
    """AI Search のインデックス（Search Index Data Contributor のロールで接続する）"""

    def __init__(self, client):
        self.client = client

    def ids_for_source(self, source):
        results = self.client.search(
            search_text="*", filter=f"source eq {odata_string(source)}", select=["id"], top=1000
        )
        return [r["id"] for r in results]

    def upload(self, documents):
        if documents:
            self._check(self.client.merge_or_upload_documents(documents))

    def delete(self, ids):
        if ids:
            self._check(self.client.delete_documents([{"id": i} for i in ids]))

    @staticmethod
    def _check(results):
        failed = [r for r in results if not r.succeeded]
        if failed:  # 一時的な失敗のこともあるので、例外を出して再実行に任せる
            raise RuntimeError(f"インデックスの更新に {len(failed)} 件失敗した: {failed[0].error_message}")


class CosmosEmbeddingCache:
    """埋め込みキャッシュ（Cosmos DB のコンテナー embcache。パーティションキーは /id）"""

    def __init__(self, container):
        self.container = container

    def get(self, key):
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            return self.container.read_item(item=key, partition_key=key)["vector"]
        except CosmosResourceNotFoundError:
            return None

    def put(self, key, vector):
        self.container.upsert_item({"id": key, "vector": vector})


class BlobDocuments:
    """Blob のコンテナー docs（マネージドIDで読む）"""

    def __init__(self, container_client):
        self.container = container_client

    def read_text(self, name):
        from azure.core.exceptions import ResourceNotFoundError

        blob = self.container.get_blob_client(name)
        try:
            size = blob.get_blob_properties().size
            if size > MAX_BYTES:
                raise IngestError(f"文書が大きすぎる（{size} バイト。上限は {MAX_BYTES} バイト）")
            data = blob.download_blob().readall()
        except ResourceNotFoundError:
            return None
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise IngestError("文書が UTF-8 のテキストではない") from e
