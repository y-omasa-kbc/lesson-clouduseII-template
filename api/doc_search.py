"""文書の検索（コマ14）: ローカルのベクトルDBから、Azure AI Search へ

コマ16で、文書の権限のフィールド allowedGroups と、検索の絞り込み（filter）を追加した。

  1. インデックスの定義（build_index）       : どのフィールドを、どう検索できるようにするか
  2. ローカルのデータの変換（to_search_documents）: ローカルの形から、インデックスの文書の形へ
  3. 検索（build_search_args・run_search）     : 全文検索・ベクトル検索・ハイブリッド検索

このファイルの関数は、Azure にもつながずにテストできる（接続する部分は、呼ぶ側から client を受け取る）。
"""

import hashlib

from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    HnswParameters,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery

MODES = ("keyword", "vector", "hybrid")
VECTOR_PROFILE = "vector-profile"


# =====================================================================
# 1. インデックスの定義
# =====================================================================
def build_index(name, dimensions):
    """インデックスの定義を作る（dimensions は埋め込みの次元数）"""
    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SimpleField(name="source", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="content", type=SearchFieldDataType.String, analyzer_name="ja.microsoft"),
        SearchField(
            name="contentVector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            hidden=True,
            vector_search_dimensions=dimensions,
            vector_search_profile_name=VECTOR_PROFILE,
        ),
        # 文書の権限（コマ16）: この文書を見てよいグループの一覧。絞り込み（filter）だけに使う
        #   全文検索の対象にはせず、検索の結果にも返さない（hidden=True。誰が見られるかを、利用者に知らせない）
        SimpleField(
            name="allowedGroups",
            type=SearchFieldDataType.Collection(SearchFieldDataType.String),
            filterable=True,
            hidden=True,
        ),
    ]
    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="hnsw", parameters=HnswParameters(metric="cosine"))],
        profiles=[VectorSearchProfile(name=VECTOR_PROFILE, algorithm_configuration_name="hnsw")],
    )
    return SearchIndex(name=name, fields=fields, vector_search=vector_search)


# =====================================================================
# 2. ローカルのデータの変換
# =====================================================================
def document_id(source, index):
    """文書の ID を決める。キーに使える文字は英数字と _ - = だけなので、ハッシュにする

    同じ文書の同じ位置からは、いつも同じ ID ができる。だから、投入をやり直しても文書が増えない（コマ10の冪等性）。
    """
    return hashlib.sha256(f"{source}#{index}".encode("utf-8")).hexdigest()


def to_search_documents(records):
    """ローカルの vector_store.json の形のリストを、インデックスの文書のリストに変える

      ローカルの形: {"source": 文書のパス, "text": 本文, "embedding": ベクトル}
      文書の形    : {"id": document_id の値, "source": 同じ, "content": 本文, "contentVector": ベクトル}
    """
    return [
        {
            "id": document_id(r["source"], i),
            "source": r["source"],
            "content": r["text"],
            "contentVector": r["embedding"],
        }
        for i, r in enumerate(records)
    ]


# =====================================================================
# 3. 検索
# =====================================================================
def build_search_args(mode, query, vector, top, filter=None):
    """SearchClient.search に渡す引数を作る

      keyword : 全文検索だけ（search_text）
      vector  : ベクトル検索だけ（vector_queries）
      hybrid  : 両方を1回の要求で送る。AI Search が RRF で順位をまとめる
      filter  : 絞り込みの式（コマ16）。全文検索にもベクトル検索にも効く（ベクトル検索では、近いものを探す前に絞り込む）
    """
    args = _mode_args(mode, query, vector, top)
    if filter is not None:
        args["filter"] = filter
    return args


def _mode_args(mode, query, vector, top):
    vector_query = None
    if mode != "keyword":
        vector_query = VectorizedQuery(vector=vector, k_nearest_neighbors=top * 5, fields="contentVector")
    if mode == "keyword":
        return {"search_text": query, "top": top}
    if mode == "vector":
        return {"search_text": None, "vector_queries": [vector_query], "top": top}
    return {"search_text": query, "vector_queries": [vector_query], "top": top}


def run_search(client, mode, query, vector, top=3, filter=None):
    """検索して、結果（source・content・score）のリストを返す"""
    args = build_search_args(mode, query, vector, top, filter)
    return [
        {"source": r["source"], "content": r["content"], "score": round(r["@search.score"], 4)}
        for r in client.search(select=["source", "content"], **args)
    ]
