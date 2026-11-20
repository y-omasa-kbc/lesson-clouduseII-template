"""コマ17の演習のテスト（教員が用意したもの）。Azure にも OpenRouter にもつながない

  python -m pytest lesson17/test_metrics.py -v
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evaluation"))
sys.path.insert(0, str(ROOT / "api"))

import metrics  # noqa: E402
import run_eval  # noqa: E402

CASE = {
    "id": "t1", "question": "経費精算の締め切りは", "user": "user-aoi", "answerable": True,
    "expected_source": "docs/keihi_shucchou.txt", "expected_text": "毎月25日", "must_include": ["25日"], "kind": "基本",
}
NO_ANSWER = {
    "id": "t2", "question": "社員食堂の今週のメニューは", "user": "user-aoi", "answerable": False,
    "expected_source": None, "expected_text": None, "must_include": [], "kind": "資料にない",
}
RIGHT = {"source": "docs/keihi_shucchou.txt", "content": "Q. 経費精算の締め切りは？\nA. 毎月25日までに申請する。", "score": 1.0}
SAME_FILE = {"source": "docs/keihi_shucchou.txt", "content": "Q. 出張の日当はいくらか？\nA. 1日2,000円である。", "score": 0.9}
OTHER = {"source": "docs/faq_general.txt", "content": "Q. 毎月25日は何の日？\nA. 給与の支給日である。", "score": 0.8}


def answer(text, sources, unknown=False):
    return {"answer": text, "citations": [{"n": i, "source": s, "title": ""} for i, s in enumerate(sources, 1)],
            "unknown": unknown, "llmCalls": 1}


# ---------- 手順5-1: find_rank ----------
def test_順位_1番目():
    assert metrics.find_rank(CASE, [RIGHT, SAME_FILE, OTHER]) == 1


def test_順位_3番目():
    assert metrics.find_rank(CASE, [SAME_FILE, OTHER, RIGHT]) == 3


def test_順位_同じ文書でも目印がなければ根拠ではない():
    assert metrics.find_rank(CASE, [SAME_FILE]) is None


def test_順位_目印があっても文書が違えば根拠ではない():
    assert metrics.find_rank(CASE, [OTHER]) is None


def test_順位_答えられない質問はいつもNone():
    assert metrics.find_rank(NO_ANSWER, [RIGHT, OTHER]) is None


# ---------- 手順5-2: check_answer ----------
def test_回答_語と出典がそろえば正しい():
    assert metrics.check_answer(CASE, answer("毎月25日までに申請する [1]", ["docs/keihi_shucchou.txt"])) is True


def test_回答_語がなければ誤り():
    assert metrics.check_answer(CASE, answer("経費システムから申請する [1]", ["docs/keihi_shucchou.txt"])) is False


def test_回答_出典が違えば誤り():
    assert metrics.check_answer(CASE, answer("毎月25日である [1]", ["docs/faq_general.txt"])) is False


def test_回答_答えられる質問に分からないは誤り():
    assert metrics.check_answer(CASE, answer("資料からは分かりません。", [], unknown=True)) is False


def test_回答_答えられない質問に分からないは正しい():
    assert metrics.check_answer(NO_ANSWER, answer("資料からは分かりません。", [], unknown=True)) is True


def test_回答_答えられない質問に答えたら誤り():
    assert metrics.check_answer(NO_ANSWER, answer("今週はカレーである [1]", ["docs/faq_general.txt"])) is False


# ---------- 手順5-3: diagnose ----------
def test_切り分け_成功():
    assert metrics.diagnose(CASE, 1, True) == metrics.SUCCESS


def test_切り分け_根拠がなければ検索の失敗():
    assert metrics.diagnose(CASE, None, False) == metrics.RETRIEVAL_FAILURE


def test_切り分け_根拠があれば生成の失敗():
    assert metrics.diagnose(CASE, 2, False) == metrics.GENERATION_FAILURE


def test_切り分け_答えられない質問に答えたら生成の失敗():
    assert metrics.diagnose(NO_ANSWER, None, False) == metrics.GENERATION_FAILURE


# ---------- 全体（教員が用意した部分と合わせて動くか） ----------
def test_全体_最初の設定の数字():
    settings = {"search_mode": "vector", "top_k": 3, "chunking": "qa"}
    _rows, summary = run_eval.evaluate(settings)
    assert summary["hit_rate"] == 0.75
    assert summary["answer_accuracy"] == 0.6
    assert summary["retrieval_failures"] == 2
    assert summary["generation_failures"] == 2
    assert summary["leaks"] == 0


def test_全体_評価ではLLMを呼ばずに検索だけを測れる():
    settings = {"search_mode": "vector", "top_k": 3, "chunking": "qa"}
    _rows, summary = run_eval.evaluate(settings, retrieval_only=True)
    assert summary["llm_calls"] == 0
    assert summary["hit_rate"] == 0.75
