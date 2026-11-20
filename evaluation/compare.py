"""2つの評価の結果を比べる（コマ17。教員が用意したもの）

使い方
  python evaluation/compare.py before after       evaluation/results/before.json と after.json を比べる

全体の数字の差と、質問ごとの変化（良くなった・悪くなった）を表示する。
全体の数字が良くなっても、悪くなった質問があれば、それは「回帰」である。必ず中身を見る。
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

LABELS = [
    ("hit_rate", "ヒット率", "%"),
    ("mrr", "MRR", "f"),
    ("answer_accuracy", "正答率", "%"),
    ("unknown_accuracy", "「分からない」の正答率", "%"),
    ("retrieval_failures", "検索の失敗（件）", "d"),
    ("generation_failures", "生成の失敗（件）", "d"),
    ("leaks", "漏洩（件）", "d"),
    ("llm_calls", "LLM を呼んだ回数", "d"),
]


def load(label):
    path = HERE / "results" / f"{label}.json"
    if not path.exists():
        sys.exit(f"{path.relative_to(HERE.parent)} がない。run_eval.py --label {label} で作る。")
    return json.loads(path.read_text(encoding="utf-8"))


def fmt(value, kind):
    if kind == "%":
        return f"{value:.0%}"
    if kind == "f":
        return f"{value:.3f}"
    return str(value)


def main():
    if len(sys.argv) != 3:
        sys.exit("使い方: python evaluation/compare.py <前のラベル> <後のラベル>")
    a, b = load(sys.argv[1]), load(sys.argv[2])
    print(f"前（{a['label']}）: {a['settings']}")
    print(f"後（{b['label']}）: {b['settings']}")
    if a["retrievalOnly"] or b["retrievalOnly"]:
        sys.exit("--retrieval-only の結果は、回答を測っていないので比べられない。")
    print()
    print(f"{'指標':24} {'前':>7} {'後':>7}")
    for key, name, kind in LABELS:
        print(f"{name:24} {fmt(a['summary'][key], kind):>7} {fmt(b['summary'][key], kind):>7}")

    print("\n質問ごとの変化")
    before = {r["case"]["id"]: r for r in a["rows"]}
    changed = 0
    for r in b["rows"]:
        old = before.get(r["case"]["id"])
        if old is None:
            continue
        rank_old = "-" if old["rank"] is None else old["rank"]
        rank_new = "-" if r["rank"] is None else r["rank"]
        if old["ok"] == r["ok"] and rank_old == rank_new:
            continue
        changed += 1
        if old["ok"] and not r["ok"]:
            mark = "悪くなった（回帰）"
        elif not old["ok"] and r["ok"]:
            mark = "良くなった"
        elif old["diagnosis"] != r["diagnosis"]:
            mark = "失敗の種類が変わった"
        else:
            mark = "順位だけ変わった"
        print(f"  {r['case']['id']} {r['case']['question']}: 順位 {rank_old} → {rank_new}、"
              f"{old['diagnosis']} → {r['diagnosis']}  … {mark}")
    if changed == 0:
        print("  変化なし")


if __name__ == "__main__":
    main()
