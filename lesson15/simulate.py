"""取り込みパイプラインを、Codespace の中だけで動かす（コマ15。教員が用意したもの）

使い方: python lesson15/simulate.py
Azure にも OpenRouter にもつながない。api/ingest.py の process_event を、疑似の Blob・インデックス・キャッシュで動かし、
文書を置く・置き直す・差し替える・消すたびに、何が起きたか（埋め込んだ数・キャッシュから使った数）を表示する。
埋め込みはモック埋め込みだが、「本物ならここで費用がかかる」回数を数えている。
"""

from memory import HERE, CountingEmbedder, MemoryBlobs, MemoryCache, MemoryIndex, event

import ingest

DOCS = HERE / "docs"
DOCS_V2 = HERE / "docs-v2"


def main():
    blobs, index, cache, embedder = MemoryBlobs(), MemoryIndex(), MemoryCache(), CountingEmbedder()

    def step(title, kind, name, model="mock"):
        result = ingest.process_event(event(kind, name), blobs, index, cache, embedder, model)
        print(f"\n■ {title}")
        print(f"   結果: {result.get('action')}  チャンク {result.get('chunks', '-')}  投入 {result.get('uploaded', '-')}"
              f"  削除 {result.get('deleted', '-')}  埋め込み {result.get('embedded', '-')}"
              f"  キャッシュ {result.get('cacheHits', '-')}")
        print(f"   インデックス: {index.count_by_source()}  （合計 {len(index.docs)} 件）")
        print(f"   埋め込みモデルを呼んだ回数の累計: {embedder.calls} 回（チャンク {embedder.texts} 個分）")

    for path in sorted(DOCS.glob("*.txt")):
        blobs.files[path.name] = path.read_text(encoding="utf-8")
        step(f"1. {path.name} を置く", "created", path.name)

    step("2. faq_it.txt を、同じ内容で置き直す", "created", "faq_it.txt")

    blobs.files["faq_it.txt"] = (DOCS_V2 / "faq_it.txt").read_text(encoding="utf-8")
    step("3. faq_it.txt を、改訂版（1つ変更・1つ削除・1つ追加）に差し替える", "created", "faq_it.txt")

    del blobs.files["keihi_shucchou.txt"]
    step("4. keihi_shucchou.txt を消す", "deleted", "keihi_shucchou.txt")

    step("5. 同じ「消した」イベントが、もう一度届く", "deleted", "keihi_shucchou.txt")

    index.docs.clear()
    print("\n（インデックスを空にした。キャッシュは残っている）")
    for name in sorted(blobs.files):
        step(f"6. インデックスを作り直す: {name} を取り込み直す", "created", name)

    index.docs.clear()
    print("\n（インデックスを空にした。埋め込みの方法を other-model に変えた、とする）")
    step("7. 埋め込みの方法を変えて faq_general.txt を取り込み直す", "created", "faq_general.txt", model="other-model")


if __name__ == "__main__":
    main()
