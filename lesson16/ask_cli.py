"""社内FAQに、テスト利用者として質問する（コマ16。教員が用意したもの）

使い方
  python lesson16/ask_cli.py --user user-aoi "昇給の基準を教えて"   1人の利用者として、1つの質問をする
  python lesson16/ask_cli.py --all                                questions.json の質問を、3人の利用者でそれぞれ試す
  --local を付けると、AI Search の代わりに疑似の検索（memory_search.py）を使う（Azure につながない）

api/rag.py の関数を、この Codespace の中で動かす（検索は、自分のサインインで AI Search に接続する）。
LLM は、環境変数 LLM_PROVIDER で決める（既定はモックLLM）。わざと失敗させるときは MOCK_FAULT を付ける。
  例: MOCK_FAULT=bad_citation python lesson16/ask_cli.py --user user-aoi "経費精算の締め切りは"
"""

import argparse
import json
import sys

from common import HERE, load_records, load_users, search_clients
from memory_search import MemorySearchClient

import auth
import embedding
import llm
import rag


def ask(client, user, question, config):
    principal = auth.Principal(user_id=user["id"], name=user["name"], roles=tuple(user["roles"]))
    groups = rag.groups_for(principal, load_users())
    try:
        vector = embedding.embed_query(question)
        hits = rag.retrieve(client, question, vector, groups)
        result = rag.answer_question(
            question,
            hits,
            lambda messages: llm.complete_text(messages, config["rag_max_output_tokens"]),
            config["rag_system_prompt"],
        )
    except (embedding.EmbeddingError, llm.LLMError, ValueError) as e:
        print(f"  [{user['name']}] エラー: {e}")
        return
    print(f"  [{user['name']}] グループ {groups}")
    print(f"     検索の候補: {', '.join(h['source'].removeprefix('docs/') for h in hits) or '（なし）'}")
    mark = "（分からない）" if result["unknown"] else ""
    print(f"     回答{mark}: {result['answer']}")
    for c in result["citations"]:
        print(f"     出典 [{c['n']}] {c['source']}「{c['title']}」")
    print(f"     LLM を呼んだ回数: {result['llmCalls']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="?")
    parser.add_argument("--user", default="user-aoi")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--local", action="store_true")
    args = parser.parse_args()

    users = {u["id"]: u for u in load_users()}
    config = llm.load_config()
    client = MemorySearchClient(load_records()) if args.local else search_clients()[1]
    if args.all:
        for q in json.loads((HERE / "questions.json").read_text(encoding="utf-8")):
            print(f"\n■ {q['question']}（{q['note']}）")
            for user in users.values():
                ask(client, user, q["question"], config)
        return
    if not args.question or args.user not in users:
        sys.exit(f"使い方: python lesson16/ask_cli.py --user <{' | '.join(users)}> \"質問\"")
    print(f"\n■ {args.question}")
    ask(client, users[args.user], args.question, config)


if __name__ == "__main__":
    main()
