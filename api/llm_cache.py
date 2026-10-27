"""LLM の応答のキャッシュ（コマ9。コマ10の開始状態では演習の解答を反映済み）

同じ入力に対する分類を、2回目からは LLM を呼ばずに、保存しておいた結果で返す。
保存先は Cosmos DB のコンテナー llmcache（項目は一定の時間が過ぎると自動で消える。TTL）。

  キャッシュのキー = 「答えを変えうるもの」をすべてまとめた文字列のハッシュ（SHA-256）
  - 問い合わせの本文そのものをキーにしない（長い・個人の情報を含みうる）。ハッシュなら元に戻せない
  - モデル・プロンプト・スキーマのどれかが変わったら、別のキーになる（古い答えを使わない）
"""

import hashlib
import json


def cache_key(purpose, model, messages, schema, mode):
    """キャッシュのキー（64文字の16進数）を返す

      purpose  : 何のための呼び出しか（例: "classify"）
      model    : 主のモデルの ID
      messages : モデルに送る messages（system のプロンプトと、問い合わせの件名・本文）
      schema   : 出力の形（JSON Schema）
      mode     : 構造化出力の方式（json_schema / prompt）
    """
    material = json.dumps(
        {"purpose": purpose, "model": model, "messages": messages, "schema": schema, "mode": mode},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class CosmosCache:
    """Cosmos DB のコンテナーを使ったキャッシュ（教員が用意したもの）"""

    def __init__(self, container):
        self.container = container

    def get(self, key):
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            return self.container.read_item(item=key, partition_key=key)["value"]
        except CosmosResourceNotFoundError:
            return None

    def set(self, key, value, ttl_seconds):
        # ttl: この項目だけの保存期間（秒）。過ぎると Cosmos DB が自動で消す
        self.container.upsert_item({"id": key, "value": value, "ttl": ttl_seconds})
