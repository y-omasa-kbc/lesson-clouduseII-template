"""コマ18の演習のテスト（教員が用意したもの）。Azure にも OpenRouter にもつながない

  python -m pytest lesson18/test_doc_parse.py -v
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "api"))

import doc_parse  # noqa: E402

SAMPLE = json.loads((HERE / "sample" / "kamome_shukuhaku.layout.json").read_text(encoding="utf-8"))
TABLE = SAMPLE["tables"][0]


def small_table(kind_of_first_column=None):
    cells = [
        {"kind": "columnHeader", "rowIndex": 0, "columnIndex": 0, "content": "品目"},
        {"kind": "columnHeader", "rowIndex": 0, "columnIndex": 1, "content": "4月"},
        {"kind": "columnHeader", "rowIndex": 0, "columnIndex": 2, "content": "5月"},
        {"rowIndex": 1, "columnIndex": 0, "content": "用紙"},
        {"rowIndex": 1, "columnIndex": 1, "content": "30箱"},
        {"rowIndex": 1, "columnIndex": 2, "content": ""},
        {"rowIndex": 2, "columnIndex": 0, "content": "トナー"},
        {"rowIndex": 2, "columnIndex": 1, "content": "2本"},
        {"rowIndex": 2, "columnIndex": 2, "content": "3本"},
    ]
    if kind_of_first_column:
        for c in cells:
            if c["columnIndex"] == 0 and c["rowIndex"] > 0:
                c["kind"] = kind_of_first_column
    return {"rowCount": 3, "columnCount": 3, "cells": cells}


# ---------- 手順6-1: table_to_sentences ----------
def test_表_セルごとに1つの文():
    sentences = doc_parse.table_to_sentences(TABLE, "第2条（宿泊費の上限）")
    assert len(sentences) == 12  # 4行 × 3列（見出しの行と、列0を除く）


def test_表_行の名前と列の見出しと値が1つの文に入る():
    sentences = doc_parse.table_to_sentences(TABLE, "第2条（宿泊費の上限）")
    assert "第2条（宿泊費の上限）: 課長・政令指定都市は 10,400円" in sentences


def test_表_行の順と列の順に並ぶ():
    sentences = doc_parse.table_to_sentences(TABLE, "T")
    assert sentences[0] == "T: 一般社員・東京23区・大阪市は 10,000円"
    assert sentences[-1] == "T: 部長・その他の地域は 9,800円"


def test_表_空のセルは文にしない():
    sentences = doc_parse.table_to_sentences(small_table(), "備品の注文")
    assert sentences == ["備品の注文: 用紙・4月は 30箱", "備品の注文: トナー・4月は 2本", "備品の注文: トナー・5月は 3本"]


def test_表_列0が行の見出しとして解析されていても同じ():
    sentences = doc_parse.table_to_sentences(small_table("rowHeader"), "備品の注文")
    assert sentences[0] == "備品の注文: 用紙・4月は 30箱"


# ---------- 教員が用意した部分 ----------
def test_正規化_部首の文字をふつうの漢字にそろえる():
    assert doc_parse.normalize("2027年4⽉1⽇") == "2027年4月1日"


def test_Markdown_ページのヘッダーとフッターは入らない():
    text = "\n".join(doc_parse.markdown_sections(SAMPLE["content"]))
    assert "SM-204" not in text
    assert "1 / 1" not in text


def test_表の題名は直前の節の見出し():
    assert doc_parse.table_title(SAMPLE, 0) == "第2条（宿泊費の上限）"


def test_全体_節と表の文からチャンクを作る():
    chunks = doc_parse.layout_chunks(SAMPLE)
    assert len(chunks) == 17  # 節 5 ＋ 表の文 12
    assert not any("<table>" in c for c in chunks)
