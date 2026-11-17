"""インデックスを作る（コマ15。教員が用意したもの）

使い方: python lesson15/create_index.py
api/doc_search.py の build_index の定義（コマ14と同じ）で、インデックスを空の状態で作り直す。
文書は、このあと Blob に置いて、取り込みのパイプラインに入れてもらう（このコマでは、手で投入しない）。
"""

from common import search_clients

import doc_search
import embedding

index_client, _, index_name = search_clients()
if index_name in list(index_client.list_index_names()):
    index_client.delete_index(index_name)
index_client.create_index(doc_search.build_index(index_name, embedding.DIMENSIONS))
print(f"インデックス {index_name} を空の状態で作った（ベクトルは {embedding.DIMENSIONS} 次元。モック埋め込みと同じ）")
