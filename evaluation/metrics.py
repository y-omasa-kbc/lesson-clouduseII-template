"""評価の指標（コマ17。コマ18の開始状態では、コマ17の演習の解答を反映済み）

1件の質問ごとに、次の3つを決める。
  find_rank    : 検索の評価。期待する根拠（正解のチャンク）が、検索の結果の何番目にあったか（LLM を使わない）
  check_answer : 回答の評価。決めた規則で、回答が正しいかを判定する（LLM を使わない）
  diagnose     : 失敗の切り分け。失敗したのは検索か、生成（LLM の回答）か
全体の数字（ヒット率・MRR・正答率など）は、summarize がまとめる。

このファイルの関数は、Azure にも OpenRouter にもつながずにテストできる。
"""

SUCCESS = "成功"
RETRIEVAL_FAILURE = "検索の失敗"
GENERATION_FAILURE = "生成の失敗"


# =====================================================================
# 1. 検索の評価
# =====================================================================
def find_rank(case, hits):
    """期待する根拠が、検索の結果 hits の何番目（1から数える）にあったかを返す。なければ None

      期待する根拠 = source が case["expected_source"] と同じで、
                    content に case["expected_text"]（目印の文字列）を含むチャンク
      答えられない質問（case["answerable"] が False）は、期待する根拠がないので、いつも None
    """
    if not case["answerable"]:
        return None
    for i, h in enumerate(hits, 1):
        if h["source"] == case["expected_source"] and case["expected_text"] in h["content"]:
            return i
    return None


# =====================================================================
# 2. 回答の評価
# =====================================================================
def check_answer(case, result):
    """回答 result（rag.answer_question の戻り値）が正しいかを、規則で判定する

      答えられる質問   : 「分からない」ではなく、must_include の語をすべて含み、
                         出典（result["citations"]）に expected_source が入っていれば正しい
      答えられない質問 : 「分からない」と答えていれば（result["unknown"] が True なら）正しい
    """
    if not case["answerable"]:
        return result["unknown"]
    if result["unknown"]:
        return False
    cited = {c["source"] for c in result["citations"]}
    return all(w in result["answer"] for w in case["must_include"]) and case["expected_source"] in cited


# =====================================================================
# 3. 失敗の切り分け
# =====================================================================
def diagnose(case, rank, ok):
    """1件の結果を、SUCCESS・RETRIEVAL_FAILURE・GENERATION_FAILURE のどれかに分ける

      ok が True                              → SUCCESS
      答えられる質問で、rank が None            → RETRIEVAL_FAILURE（根拠が LLM に渡っていない。LLM には答えようがない）
      答えられる質問で、rank がある             → GENERATION_FAILURE（根拠は渡ったのに、正しく答えられなかった）
      答えられない質問で、ok が False           → GENERATION_FAILURE（資料にないことを答えた）
    """
    if ok:
        return SUCCESS
    if case["answerable"] and rank is None:
        return RETRIEVAL_FAILURE
    return GENERATION_FAILURE


# =====================================================================
# 4. 全体の数字（教員が用意したもの）
# =====================================================================
def summarize(rows):
    """1件ごとの結果のリストから、全体の数字を計算する

      rows の1件: {"case": …, "rank": …, "ok": …, "diagnosis": …, "leaks": …, "llmCalls": …}
    """
    answerable = [r for r in rows if r["case"]["answerable"]]
    unanswerable = [r for r in rows if not r["case"]["answerable"]]
    ranks = [r["rank"] for r in answerable]
    return {
        "questions": len(rows),
        "hit_rate": _ratio(sum(1 for x in ranks if x is not None), len(ranks)),
        "mrr": round(sum(1 / x for x in ranks if x) / len(ranks), 3) if ranks else 0.0,
        "answer_accuracy": _ratio(sum(1 for r in rows if r["ok"]), len(rows)),
        "unknown_accuracy": _ratio(sum(1 for r in unanswerable if r["ok"]), len(unanswerable)),
        "retrieval_failures": sum(1 for r in rows if r["diagnosis"] == RETRIEVAL_FAILURE),
        "generation_failures": sum(1 for r in rows if r["diagnosis"] == GENERATION_FAILURE),
        "leaks": sum(r["leaks"] for r in rows),
        "llm_calls": sum(r["llmCalls"] for r in rows),
    }


def _ratio(n, d):
    return round(n / d, 3) if d else 0.0
