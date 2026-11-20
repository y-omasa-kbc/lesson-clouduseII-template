"""LLM による自動評価（LLM-as-a-judge。コマ17。教員が用意したもの）

run_eval.py が保存した結果（evaluation/results/<label>.json）の回答を、「審査員」役の LLM に採点させる。
  観点: 根拠性（回答が資料だけに基づいているか）・関連性（回答が質問に直接答えているか）。1〜5 点
  「分からない」と答えた質問は、採点しない（LLM を呼ばない）
採点の後で、規則による判定（run_eval.py の ○×）と、審査員の判定（すべての観点が3点以上なら合格）を比べる。

使い方
  python evaluation/judge.py before --estimate          LLM を何回呼ぶかを見積もるだけ（LLM を呼ばない）
  python evaluation/judge.py before                     モックの審査員で採点する
  MOCK_FAULT=judge_format python evaluation/judge.py before    審査員が決めた形で答えない場合
  --limit N で、採点する質問の数を減らす。--criteria 根拠性 で、観点を1つにする。

LLM は環境変数 LLM_PROVIDER で決める（既定はモックLLM）。本物で採点するのは、教員の合図があったときの、決められた回数だけ。
"""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "api"))

import llm  # noqa: E402

CRITERIA = ("根拠性", "関連性")
PASS_SCORE = 3


def build_judge_messages(criterion, question, hits, answer, system_prompt):
    sources = "\n\n".join(f"[{i}] {h['content']}" for i, h in enumerate(hits, 1))
    content = f"{llm.JUDGE_MARK}\n【観点】\n{criterion}\n【質問】\n{question}\n【資料】\n{sources}\n【回答】\n{answer}"
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": content}]


def parse_score(text):
    """審査員の応答から点数（1〜5 の整数）を取り出す。取り出せなければ None（審査員も形式を守らないことがある）"""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None, "JSON がなかった"
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None, "JSON として読めなかった"
    score = data.get("score")
    if not isinstance(score, int) or not 1 <= score <= 5:
        return None, f"点数が 1〜5 の整数ではなかった: {score!r}"
    return score, str(data.get("reason", ""))[:60]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    parser.add_argument("--estimate", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--criteria", nargs="+", choices=CRITERIA, default=list(CRITERIA))
    args = parser.parse_args()

    path = HERE / "results" / f"{args.label}.json"
    if not path.exists():
        sys.exit(f"{path.relative_to(HERE.parent)} がない。run_eval.py --label {args.label} で作る。")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data["retrievalOnly"]:
        sys.exit("--retrieval-only の結果には回答がないので、採点できない。")
    targets = [r for r in data["rows"] if r["llmCalls"] and r["answer"] and "分かりません" not in r["answer"]]
    if args.limit is not None:
        targets = targets[: args.limit]
    calls = len(targets) * len(args.criteria)
    print(f"採点する回答 {len(targets)} 件 × 観点 {len(args.criteria)} 個 = LLM を {calls} 回呼ぶ（{llm.provider_name()}）")
    if args.estimate:
        return

    config = llm.load_config()
    agree = 0
    print(f"\n{'ID':4} {'規則':4} {'審査員':6} " + " ".join(f"{c:6}" for c in args.criteria) + " 回答")
    for r in targets:
        scores = []
        for criterion in args.criteria:
            messages = build_judge_messages(criterion, r["case"]["question"], r["hits"], r["answer"],
                                            config["judge_system_prompt"])
            try:
                text = llm.complete_text(messages, config["judge_max_output_tokens"])
            except llm.LLMError as e:
                sys.exit(f"審査員を呼べなかった: {e}（送り直さない）")
            score, _reason = parse_score(text)
            scores.append(score)
        judged = "判定不能" if None in scores else ("合格" if min(scores) >= PASS_SCORE else "不合格")
        rule = "○" if r["ok"] else "×"
        if (judged == "合格") == bool(r["ok"]) and judged != "判定不能":
            agree += 1
        cells = " ".join(f"{'?' if s is None else s:>6}" for s in scores)
        answer = r["answer"].replace("（モックLLMの応答）", "")[:24]
        print(f"{r['case']['id']:4} {rule:4} {judged:6} {cells} {answer}")
    if targets:
        print(f"\n規則による判定と、審査員の判定の一致: {agree} / {len(targets)} 件")


if __name__ == "__main__":
    main()
