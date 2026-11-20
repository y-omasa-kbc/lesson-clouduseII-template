"""PDF から、単純抽出でテキストを取り出す（コマ18。教員が用意したもの）

使い方
  python lesson18/extract_simple.py lesson18/pdf/kamome_shukuhaku.pdf
  python lesson18/extract_simple.py lesson18/pdf/kamome_shukuhaku_scan.pdf

取り出したテキストを表示し、lesson18/out/<名前>.simple.txt に保存する。Azure にも OpenRouter にもつながない。
見た目は普通の漢字でも、別の文字（部首の文字など）として取り出された文字があれば、一覧にする。
"""

import sys
import unicodedata
from pathlib import Path

from common import OUT, simple_text

import doc_parse


def odd_characters(text):
    """部首の文字・互換用の漢字など、検索で一致しなくなる文字を集める"""
    found = {}
    for ch in text:
        code = ord(ch)
        if 0x2E80 <= code <= 0x2FDF or 0xF900 <= code <= 0xFAFF:
            found[ch] = unicodedata.name(ch, "?")
    return found


def main():
    if len(sys.argv) != 2:
        sys.exit("使い方: python lesson18/extract_simple.py <PDF のパス>")
    path = Path(sys.argv[1])
    text, pages = simple_text(path)
    OUT.mkdir(exist_ok=True)
    out = OUT / f"{path.stem}.simple.txt"
    out.write_text(text, encoding="utf-8", newline="\n")

    print(f"■ {path.name}: {pages} ページ、取り出せた文字 {len(text.strip())} 文字（{out.relative_to(OUT.parent.parent)} に保存した）")
    print("-" * 60)
    print(text if text.strip() else "（文字が1つも取り出せなかった）")
    print("-" * 60)
    odd = odd_characters(text)
    if odd:
        print("見た目は普通の漢字だが、別の文字として取り出された文字:")
        for ch, name in odd.items():
            fixed = doc_parse.normalize(ch)
            result = f"NFKC 正規化で「{fixed}」になる" if fixed != ch else "NFKC 正規化でも直らない"
            print(f"  「{ch}」 U+{ord(ch):04X} {name} … {result}")
    normalized = doc_parse.normalize(text)
    for word in ("課長", "部長", "一般社員"):
        print(f"「{word}」で探すと: 正規化の前 {word in text} ／ 正規化の後 {word in normalized}")


if __name__ == "__main__":
    main()
