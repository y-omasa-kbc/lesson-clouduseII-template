"""インデックスを作る（コマ16。教員が用意したもの）

使い方: python lesson16/create_index.py
api/doc_search.py の build_index の定義（コマ14の定義に、文書の権限のフィールド allowedGroups を加えたもの）で、
インデックスを作り直す（いったん消してから作るので、投入した文書も消える）。
"""

from common import load_info, search_clients

import doc_search

index_client, _, index_name = search_clients()
if index_name in list(index_client.list_index_names()):
    index_client.delete_index(index_name)
index = doc_search.build_index(index_name, load_info()["dimensions"])
index_client.create_index(index)
print(f"インデックス {index_name} を作った。フィールド:")
for field in index.fields:
    print(f"  {field.name}  type={field.type}  filterable={bool(field.filterable)}")
