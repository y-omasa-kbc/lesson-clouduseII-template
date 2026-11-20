"""PDF の取り出し方ごとに、チャンクと RAG の回答を比べる（コマ18。教員が用意したもの）

使い方
  python lesson18/compare_chunks.py                         3つの方法でチャンクを作り、questions.json の質問で比べる
  python lesson18/compare_chunks.py --show layout           方法 layout のチャンクを全部表示する
  python lesson18/compare_chunks.py --name kamome_shukuhaku_scan    スキャン画像の PDF で比べる（解析の結果が必要）
  python lesson18/compare_chunks.py --ask "質問" --method markdown   1つの方法・1つの質問だけで答える

方法
  simple   : 単純抽出（pypdf）のテキストを NFKC 正規化し、200文字ほどで分ける
  markdown : 文書解析の Markdown を、見出しごとに分ける（表は HTML の表のまま、節のチャンクに入る）
  layout   : 文書解析の結果から、節（表を除く）＋表のセルごとの文を作る（api/doc_parse.py の layout_chunks）

検索は Codespace の中のハイブリッド検索（evaluation/local_index.py）、回答は api/rag.py とモックLLM。
LLM は環境変数 LLM_PROVIDER で決める（既定はモックLLM）。本物で答えるのは、--ask で1回だけ（教員の合図があったとき）。
"""

import argparse
import json
import re
import sys

from common import HERE, load_layout, simple_text

import doc_parse
import embedding
import llm
import local_index
import rag

METHODS = ("simple", "markdown", "layout")
_VALUE = re.compile(r"\d{1,3}(?:,\d{3})+円")


def build_chunks(method, name):
    if method == "simple":
        text, _pages = simple_text(HERE / "pdf" / f"{name}.pdf")
        return doc_parse.simple_chunks(doc_parse.normalize(text))
    result = load_layout(name)
    if method == "markdown":
        return doc_parse.markdown_sections(result["content"])
    return doc_parse.layout_chunks(result)


def table_values(name):
    """表のすべての値（「…円」）。答えの値以外が回答に入っていたら、答えが1つに定まっていない"""
    result = load_layout(name)
    return {c["content"] for t in result.get("tables", []) for c in t["cells"] if _VALUE.fullmatch(c["content"])}


def answer(chunks, question, config):
    records = [{"source": "docs/kamome_shukuhaku.pdf", "text": c, "groups": ["all"], "embedding": embedding.mock_embed(c)}
               for c in chunks]
    client = local_index.LocalSearchClient(records)
    hits = rag.retrieve(client, question, embedding.mock_embed(question), ["all"], 3, "hybrid")
    result = rag.answer_question(
        question, hits, lambda m: llm.complete_text(m, config["rag_max_output_tokens"]), config["rag_system_prompt"]
    )
    return hits, result


def judge(q, result, values):
    if result["unknown"]:
        return False
    if not all(w in result["answer"] for w in q["must_include"]):
        return False
    if q["table_value"]:
        others = set(_VALUE.findall(result["answer"])) - set(q["must_include"])
        return not (others & values)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="kamome_shukuhaku")
    parser.add_argument("--show", choices=METHODS)
    parser.add_argument("--ask")
    parser.add_argument("--method", choices=METHODS, default="markdown")
    args = parser.parse_args()
    config = llm.load_config()

    if args.show:
        chunks = build_chunks(args.show, args.name)
        print(f"■ 方法 {args.show}: チャンク {len(chunks)} 個")
        for i, c in enumerate(chunks, 1):
            print(f"--- [{i}] {len(c)} 文字\n{c}")
        return

    if args.ask:
        chunks = build_chunks(args.method, args.name)
        print(f"方法 {args.method}・チャンク {len(chunks)} 個・LLM: {llm.provider_name()}（LLM を1回呼ぶ）")
        try:
            hits, result = answer(chunks, args.ask, config)
        except llm.LLMError as e:
            sys.exit(f"LLM を呼べなかった: {e}（送り直さない）")
        print(f"回答{'（分からない）' if result['unknown'] else ''}: {result['answer']}")
        for c in result["citations"]:
            print(f"  出典 [{c['n']}]「{c['title']}」")
        return

    if llm.provider_name() != "mock":
        sys.exit("比べるのはモックLLMで行う。本物で答えるのは --ask で1回だけ。")
    questions = json.loads((HERE / "questions.json").read_text(encoding="utf-8"))["questions"]
    values = table_values(args.name) if any(q["table_value"] for q in questions) else set()
    rows = {}
    for method in METHODS:
        chunks = build_chunks(method, args.name)
        marks = []
        for q in questions:
            if not chunks:
                marks.append("×")
                continue
            _hits, result = answer(chunks, q["question"], config)
            marks.append("○" if judge(q, result, values) else "×")
        rows[method] = (len(chunks), marks)

    print(f"■ {args.name}.pdf")
    print(f"{'方法':10} {'チャンク':>6}  " + "  ".join(q["id"] for q in questions))
    for method, (count, marks) in rows.items():
        print(f"{method:10} {count:>6}  " + "  ".join(f"{m:2}" for m in marks))
    print()
    for q in questions:
        print(f"  {q['id']}: {q['question']}（答え: {'・'.join(q['must_include'])}）")
    print("  ○: 答えの値を含み、表のほかの値を含まない回答 ／ ×: 答えられない・答えが1つに定まらない")


if __name__ == "__main__":
    main()
