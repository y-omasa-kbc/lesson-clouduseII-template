"""評価セットで、社内FAQボットの RAG を測る（コマ17。教員が用意したもの）

使い方（リポジトリの一番上のフォルダで実行する）
  python evaluation/run_eval.py --retrieval-only          検索だけを測る（LLM を呼ばない）
  python evaluation/run_eval.py --label before            検索と回答を測り、evaluation/results/before.json に保存する
  python evaluation/run_eval.py --estimate                本物の LLM で測ると、LLM を何回呼ぶかを見積もる（LLM を呼ばない）

設定（検索の方式・件数・チャンクの分け方）は evaluation/settings.json で決める。
回答の生成は api/rag.py の answer_question を使う。LLM は環境変数 LLM_PROVIDER で決める（既定はモックLLM）。
本物の LLM で評価セット全体を測るのは、呼ぶ回数を見積もり、教員が認めたときだけ（--real を付ける）。
"""

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "api"))

import local_index  # noqa: E402
import metrics  # noqa: E402

import auth  # noqa: E402
import embedding  # noqa: E402
import llm  # noqa: E402
import rag  # noqa: E402


def load_settings():
    settings = json.loads((HERE / "settings.json").read_text(encoding="utf-8"))
    return {k: v for k, v in settings.items() if not k.startswith("_")}


def load_cases():
    return json.loads((HERE / "eval_set.json").read_text(encoding="utf-8"))["cases"]


def load_users():
    return json.loads((HERE.parent / "api" / "dev_users.json").read_text(encoding="utf-8"))["users"]


def evaluate(settings, retrieval_only=False, complete=None):
    """評価セットの全部の質問を試し、(1件ごとの結果のリスト, 全体の数字) を返す"""
    if os.environ.get("EMBEDDING_PROVIDER", "mock") != "mock":
        sys.exit("評価の索引はモック埋め込みで作る。EMBEDDING_PROVIDER を付けずに実行する。")
    config = llm.load_config()
    if complete is None:
        def complete(messages):
            return llm.complete_text(messages, config["rag_max_output_tokens"])

    records = local_index.build_records(settings["chunking"])
    client = local_index.LocalSearchClient(records)
    groups_of_chunk = local_index.groups_of(records)
    users = load_users()

    rows = []
    for case in load_cases():
        principal = auth.Principal(user_id=case["user"], name=case["user"], roles=())
        groups = rag.groups_for(principal, users)
        hits = rag.retrieve(
            client, case["question"], embedding.mock_embed(case["question"]), groups,
            settings["top_k"], settings["search_mode"],
        )
        # 漏洩: 利用者のグループと重ならない権限のチャンクが、検索の結果に混ざった数
        leaks = sum(1 for h in hits if not groups_of_chunk[(h["source"], h["content"])] & set(groups))
        rank = metrics.find_rank(case, hits)
        row = {"case": case, "rank": rank, "leaks": leaks, "hits": hits, "ok": None, "diagnosis": None,
               "answer": None, "citations": [], "llmCalls": 0}
        if not retrieval_only:
            result = rag.answer_question(case["question"], hits, complete, config["rag_system_prompt"])
            ok = metrics.check_answer(case, result)
            row.update(ok=ok, diagnosis=metrics.diagnose(case, rank, ok), answer=result["answer"],
                       citations=result["citations"], llmCalls=result["llmCalls"])
        rows.append(row)
    return rows, metrics.summarize(rows)


def print_rows(rows, retrieval_only):
    print(f"{'ID':4} {'種類':6} {'順位':>4}  {'判定':4} {'切り分け':8} 質問 → 回答")
    for r in rows:
        c = r["case"]
        rank = "-" if r["rank"] is None else str(r["rank"])
        if retrieval_only:
            print(f"{c['id']:4} {c['kind']:6} {rank:>4}  {c['question']}")
            continue
        mark = "○" if r["ok"] else "×"
        answer = r["answer"].replace("（モックLLMの応答）", "")[:28]
        print(f"{c['id']:4} {c['kind']:6} {rank:>4}  {mark:4} {r['diagnosis']:8} {c['question']} → {answer}")


def print_summary(summary, retrieval_only):
    print()
    print(f"  ヒット率（期待する根拠が上位に入った割合）: {summary['hit_rate']:.0%}")
    print(f"  MRR（期待する根拠の順位の逆数の平均）     : {summary['mrr']:.3f}")
    print(f"  漏洩（見てはいけないチャンクが検索に出た数）: {summary['leaks']}")
    if retrieval_only:
        print("  LLM を呼んだ回数: 0（検索だけを測った）")
        return
    print(f"  正答率（規則で判定）                       : {summary['answer_accuracy']:.0%}")
    print(f"  「分からない」の正答率（答えられない質問）   : {summary['unknown_accuracy']:.0%}")
    print(f"  失敗の内訳: 検索の失敗 {summary['retrieval_failures']} 件、生成の失敗 {summary['generation_failures']} 件")
    print(f"  LLM を呼んだ回数: {summary['llm_calls']}（{llm.provider_name()}）")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", help="結果を evaluation/results/<label>.json に保存する")
    parser.add_argument("--retrieval-only", action="store_true", help="検索だけを測る（LLM を呼ばない）")
    parser.add_argument("--estimate", action="store_true", help="LLM を呼ぶ回数を見積もるだけ")
    parser.add_argument("--real", action="store_true", help="本物の LLM で測ることを認める（教員の指示があるときだけ）")
    args = parser.parse_args()

    settings = load_settings()
    print(f"設定: {settings}")
    if args.estimate:
        rows, summary = evaluate(settings, retrieval_only=True)
        calls = sum(1 for r in rows if r["hits"])  # 検索の候補がない質問では、LLM を呼ばない
        print(f"評価セット {len(rows)} 問。本物の LLM で回答まで測ると、LLM を {calls} 回呼ぶ（1回の評価につき）。")
        print("設定を変えて比べるたびに、この回数がかかる。審査員（judge.py）を使うと、さらに増える。")
        return
    if llm.provider_name() != "mock" and not args.retrieval_only and not args.real:
        sys.exit("本物の LLM で評価セット全体を測ろうとしている。--estimate で回数を見積もり、教員の指示があるときだけ --real を付ける。")

    rows, summary = evaluate(settings, retrieval_only=args.retrieval_only)
    print_rows(rows, args.retrieval_only)
    print_summary(summary, args.retrieval_only)
    if args.label:
        out = HERE / "results" / f"{args.label}.json"
        out.parent.mkdir(exist_ok=True)
        data = {"label": args.label, "settings": settings, "provider": llm.provider_name(),
                "retrievalOnly": args.retrieval_only, "summary": summary, "rows": rows}
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        print(f"\n結果を {out.relative_to(HERE.parent)} に保存した。")


if __name__ == "__main__":
    main()
