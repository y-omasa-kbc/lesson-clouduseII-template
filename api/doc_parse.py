"""文書の解析結果からチャンクを作る（コマ18）

PDF からテキストを取り出す方法は2つある。
  単純抽出 : PDF の中の文字の並びを、そのまま取り出す（pypdf など）。表の見出しと値の対応、
             ページのヘッダー・フッターの区別は失われる。スキャン画像の PDF からは、文字が取り出せない
  文書解析 : Document Intelligence のレイアウト モデル（prebuilt-layout）で、段落・見出し・表を構造として取り出す。
             画像の文字も読む（OCR）

このファイルの関数は、取り出した結果からチャンクを作る。Azure にも OpenRouter にもつながずにテストできる
（文書解析の結果は、呼ぶ側が辞書で渡す。Document Intelligence の SDK の as_dict() の形）。
取り込みのパイプライン（コマ15の ingest.py）に組み込むときは、split_chunks の代わりに、ここの関数を使う。
"""

import re
import unicodedata

SIMPLE_CHUNK_SIZE = 200  # 単純抽出のテキストを分けるときの、1チャンクの文字数の目安
NOISE_ROLES = {"pageHeader", "pageFooter", "pageNumber"}  # 本文ではない段落の役割


# =====================================================================
# 1. 単純抽出のテキスト
# =====================================================================
def normalize(text):
    """互換文字を、ふつうの文字にそろえる（NFKC 正規化）

    PDF から取り出した文字には、見た目は同じでも別の文字（例: 部首の文字「⽉」U+2F49）が混ざることがある。
    そのままでは「月」で検索しても一致しない。NFKC でそろえられるものは、そろえる（そろえられない文字も残る）。
    """
    return unicodedata.normalize("NFKC", text)


def simple_chunks(text, size=SIMPLE_CHUNK_SIZE):
    """単純抽出のテキストを、行をまとめて size 文字ほどのチャンクに分ける（文書の構造は分からないので、長さで分ける）"""
    chunks, current = [], ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if current and len(current) + len(line) > size:
            chunks.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


# =====================================================================
# 2. 文書解析の結果
# =====================================================================
_COMMENT = re.compile(r"<!--.*?-->", re.S)  # ページのヘッダー・フッター・番号・改ページは、Markdown ではコメントになる
_TABLE = re.compile(r"<table>.*?</table>", re.S)  # Markdown の中の表は、HTML の表になる


def markdown_sections(markdown):
    """文書解析の Markdown を、見出し（# で始まる行）ごとのチャンクに分ける。表は、その節のチャンクに入ったまま"""
    body = _COMMENT.sub("", markdown)
    sections, current = [], []
    for line in body.splitlines():
        if line.startswith("#") and current:
            sections.append("\n".join(current).strip())
            current = []
        current.append(line)
    if current:
        sections.append("\n".join(current).strip())
    return [s for s in sections if s]


def table_title(result, index):
    """表の題名に使う文字列。表の直前にある節の見出し（role が sectionHeading の段落）を使う。なければ「表n」"""
    table = result["tables"][index]
    offset = table["spans"][0]["offset"] if table.get("spans") else None
    headings = [
        p["content"] for p in result.get("paragraphs", [])
        if p.get("role") == "sectionHeading" and offset is not None and p["spans"][0]["offset"] < offset
    ]
    return headings[-1] if headings else f"表{index + 1}"


def table_to_sentences(table, title):
    """表（文書解析の結果の tables の1つ）を、セル1つにつき1つの文にする

      見出しの行のセル（kind が "columnHeader"）から、列ごとの見出しを決める
      各行の列0のセルを、その行の名前にする
      見出しの行・列0以外のセルごとに、「<題名>: <行の名前>・<列の見出し>は <値>」という文を作る

    例: 題名「第2条（宿泊費の上限）」、行「課長」、列「政令指定都市」、値「10,400円」
        → 「第2条（宿泊費の上限）: 課長・政令指定都市は 10,400円」
    値が空のセルは、文にしない。戻り値は、行の順・列の順に並べた文のリスト。
    """
    # TODO(コマ18 演習手順6-1): 上のとおりに、表のセルを文にする。
    #   セルは table["cells"] にあり、各セルは {"kind", "rowIndex", "columnIndex", "content"} を持つ
    #   （kind がないセルは、ふつうの値のセル）。
    #   いまは、表をまるごと1つの文字列にしている（見出しと値の対応が分からない）。
    return [" ".join(c["content"] for c in table["cells"])]


def layout_chunks(result):
    """文書解析の結果から、チャンクを作る: 見出しごとの節（表を除く）＋ 表のセルごとの文"""
    chunks = [_TABLE.sub("", s).strip() for s in markdown_sections(result["content"])]
    chunks = [c for c in chunks if c and not re.fullmatch(r"#+.*", c)]  # 見出しだけになった節は捨てる
    for i, table in enumerate(result.get("tables", [])):
        chunks.extend(table_to_sentences(table, table_title(result, i)))
    return chunks
