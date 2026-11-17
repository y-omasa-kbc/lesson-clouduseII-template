"""埋め込みの費用を見積もる（コマ15。教員が用意したもの）

使い方: python lesson15/estimate.py [--price 単価] [--tokens-per-char 1文字あたりのトークン数] [--updates 回数] [--changed 割合]

lesson15/docs/ の文書をチャンクに分け、文字数からトークン数を概算して、埋め込みの費用を見積もる。
単価と「1文字あたりのトークン数」は、モデルによって違う。既定の値は、計算の練習のための仮の値である。
実際の単価は、授業で示すもの（OpenRouter のモデルのページ）を使う。このスクリプトは OpenRouter を呼ばない。
"""

import argparse

from memory import HERE

import ingest

parser = argparse.ArgumentParser()
parser.add_argument("--price", type=float, default=0.02, help="100万トークンあたりの単価（ドル。仮の値）")
parser.add_argument("--tokens-per-char", type=float, default=1.0, help="1文字あたりのトークン数（仮の値）")
parser.add_argument("--updates", type=int, default=20, help="1か月に差し替える文書の数")
parser.add_argument("--changed", type=float, default=0.2, help="1回の差し替えで内容が変わるチャンクの割合")
parser.add_argument("--scale", type=int, default=1000, help="文書が何倍になったときの金額も示すか")
args = parser.parse_args()

docs = sorted((HERE / "docs").glob("*.txt"))
chunks = [c for p in docs for c in ingest.split_chunks(p.read_text(encoding="utf-8"))]
chars = sum(len(c) for c in chunks)
tokens = chars * args.tokens_per_char
per_doc = tokens / len(docs)


def dollars(t):
    return t / 1_000_000 * args.price


rows = [
    ("A. 全部の文書を1回埋め込む", tokens),
    (f"B. 1か月に {args.updates} 回の差し替えで、毎回文書全体を埋め込み直す（キャッシュなし）", per_doc * args.updates),
    (f"C. 同じ差し替えで、変わったチャンク（{args.changed:.0%}）だけを埋め込む（キャッシュあり）",
     per_doc * args.changed * args.updates),
    ("D. インデックスを作り直す（キャッシュなし。キャッシュがあれば 0）", tokens),
]
print(f"文書 {len(docs)} 個、チャンク {len(chunks)} 個、{chars} 文字 → 約 {tokens:,.0f} トークン")
print(f"単価 {args.price} ドル／100万トークン、1文字 {args.tokens_per_char} トークン（どちらも仮の値）として:")
for label, t in rows:
    print(f"  {label}")
    print(f"      約 {t:,.0f} トークン → 約 {dollars(t):.6f} ドル（文書が {args.scale} 倍なら 約 {dollars(t) * args.scale:.4f} ドル）")
