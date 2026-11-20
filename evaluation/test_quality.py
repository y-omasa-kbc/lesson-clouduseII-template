"""品質の回帰テスト（コマ17。教員が用意したもの）

評価セットを、モックLLMとローカルの索引で測り、quality_gate.json の基準を下回っていないかを確かめる。
LLM も Azure も呼ばないので、何度実行しても費用はかからない。コマ20では、これを CI（GitHub Actions）で動かす。

  python -m pytest evaluation/test_quality.py -v

基準を下回ったら、その変更は品質を下げている（回帰）。直すか、変更をやめる。
改善を採用したら、基準を新しい値まで上げる（品質は下がる方向にだけ動かないようにする）。
"""

import json
from pathlib import Path

import pytest

import run_eval

GATE = json.loads((Path(__file__).resolve().parent / "quality_gate.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def summary():
    _rows, summary = run_eval.evaluate(run_eval.load_settings())
    return summary


def test_漏洩がない(summary):
    assert summary["leaks"] <= GATE["max_leaks"]


def test_答えられない質問には分からないと答える(summary):
    assert summary["unknown_accuracy"] >= GATE["min_unknown_accuracy"]


def test_ヒット率が基準以上(summary):
    assert summary["hit_rate"] >= GATE["min_hit_rate"]


def test_正答率が基準以上(summary):
    assert summary["answer_accuracy"] >= GATE["min_answer_accuracy"]
