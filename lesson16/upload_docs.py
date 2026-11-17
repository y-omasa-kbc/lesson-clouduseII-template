"""配布データ（権限つき）をインデックスに投入する（コマ16。教員が用意したもの）

使い方: python lesson16/upload_docs.py
lesson16/vector_store.json の各レコードは、コマ14の形に、文書の権限 "groups" が加わっている。
これを、インデックスの allowedGroups に入れる。埋め込みは配布データのものを使う（OpenRouter は呼ばない）。
"""

import sys

from common import load_records, search_clients

import doc_search

_, client, index_name = search_clients()
records = load_records()
documents = doc_search.to_search_documents(records)
for doc, record in zip(documents, records):
    doc["allowedGroups"] = record["groups"]
results = client.merge_or_upload_documents(documents)
failed = [r for r in results if not r.succeeded]
print(f"{len(results) - len(failed)} 件を {index_name} に投入した。失敗 {len(failed)} 件")
groups = {}
for r in records:
    for g in r["groups"]:
        groups[g] = groups.get(g, 0) + 1
print("グループごとの文書（チャンク）の数: " + "、".join(f"{g} {n} 件" for g, n in sorted(groups.items())))
if failed:
    sys.exit(f"失敗の例: {failed[0].error_message}")
