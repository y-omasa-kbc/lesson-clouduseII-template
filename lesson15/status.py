"""取り込みの状態を見る（コマ15。教員が用意したもの）

使い方: python lesson15/status.py
  - インデックスの文書の数（文書ごと）
  - 埋め込みキャッシュの件数
  - 取り込みの記録（新しいものから10件）
どれも、自分のサインイン（az login）で読む。
"""

from common import cosmos_container, search_clients

_, client, index_name = search_clients()
counts = {}
for r in client.search(search_text="*", select=["source"], top=1000):
    counts[r["source"]] = counts.get(r["source"], 0) + 1
print(f"■ インデックス {index_name}: 合計 {sum(counts.values())} 件")
for source, n in sorted(counts.items()):
    print(f"   {source}: {n} 件")

cache = cosmos_container("embcache")
total = list(cache.query_items("SELECT VALUE COUNT(1) FROM c", enable_cross_partition_query=True))[0]
print(f"■ 埋め込みキャッシュ: {total} 件")

log = cosmos_container("ingestlog")
print("■ 取り込みの記録（新しい順に10件）")
for e in log.query_items("SELECT TOP 10 * FROM c ORDER BY c.at DESC", enable_cross_partition_query=True):
    detail = e.get("error") or (
        f"チャンク {e.get('chunks', '-')} 投入 {e.get('uploaded', '-')} 削除 {e.get('deleted', '-')}"
        f" 埋め込み {e.get('embedded', '-')} キャッシュ {e.get('cacheHits', '-')}"
    )
    print(f"   {e['at'][11:19]}  {e.get('event', '-'):11} {e['source']:26} {e['action']:8} {detail}（{e['attempt']} 回目）")
