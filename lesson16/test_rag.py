"""api/rag.py を確かめるテスト（コマ16）

実行のしかた（Codespace のターミナルで。Azure にも OpenRouter にもつながない）
  pip install pytest azure-search-documents azure-identity
  python -m pytest lesson16/test_rag.py -v
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "lesson16"))

import auth  # noqa: E402
import doc_search  # noqa: E402
import embedding  # noqa: E402
import llm  # noqa: E402
import rag  # noqa: E402
from common import load_records, load_users  # noqa: E402
from memory_search import MemorySearchClient  # noqa: E402

HITS = [
    {"source": "docs/a.txt", "content": "Q. 質問A\nA. 答えA", "score": 0.03},
    {"source": "docs/b.txt", "content": "Q. 質問B\nA. 答えB", "score": 0.02},
]


def principal(user_id):
    return auth.Principal(user_id=user_id, name="", roles=())


def search_as(user_id, question):
    groups = rag.groups_for(principal(user_id), load_users())
    return rag.retrieve(MemorySearchClient(load_records()), question, embedding.mock_embed(question), groups)


def mock_complete(messages):
    return llm.MockProvider().complete_text("mock", messages, 400)


# ---------------------------------------------------------------------
# 教員が用意した部分（最初から通る）
# ---------------------------------------------------------------------
def test_データ_すべてのチャンクに権限がある():
    assert all(r["groups"] for r in load_records())


def test_定義_権限のフィールドは絞り込みに使える():
    field = {f.name: f for f in doc_search.build_index("test", 256).fields}["allowedGroups"]
    assert field.filterable is True


def test_グループ_サーバー側の一覧から決める():
    assert rag.groups_for(principal("user-kai"), load_users()) == ["all", "hr"]
    assert rag.groups_for(principal("no-such-user"), load_users()) == []


def test_グループがない利用者には何も見せない():
    assert search_as("user-toyama", "経費精算の締め切りは") == []


def test_資料に番号と出典を付けて渡す():
    content = rag.build_messages("質問", HITS, "システム")[1]["content"]
    assert "[1] 出典: docs/a.txt" in content and "[2] 出典: docs/b.txt" in content


# ---------------------------------------------------------------------
# セキュリティフィルター（演習手順5）
# ---------------------------------------------------------------------
def test_フィルター_グループから式を作る():
    assert rag.security_filter(["all", "hr"]) == "allowedGroups/any(g: search.in(g, 'all,hr', ','))"


def test_フィルター_空のグループでは作らない():
    with pytest.raises(ValueError):
        rag.security_filter([])


@pytest.mark.parametrize("bad", ["hr' or true or 'x", "all,hr", "人事"])
def test_フィルター_正しくない名前を式に入れない(bad):
    with pytest.raises(ValueError):
        rag.security_filter(["all", bad])


def test_検索_一般の利用者には人事課の文書が候補にならない():
    sources = [h["source"] for h in search_as("user-aoi", "昇給の基準を教えて")]
    assert sources and "docs/jinji_kyuyo.txt" not in sources


def test_検索_人事課の利用者には人事課の文書が候補になる():
    sources = [h["source"] for h in search_as("user-kai", "昇給の基準を教えて")]
    assert sources[0] == "docs/jinji_kyuyo.txt"


def test_検索_情報システム課の文書は人事課の利用者に見えない():
    sources = [h["source"] for h in search_as("user-kai", "サーバー室に入るときの決まりは")]
    assert "docs/it_kanri.txt" not in sources


# ---------------------------------------------------------------------
# 引用と「分からない」（演習手順6）
# ---------------------------------------------------------------------
def test_引用_回答が引用した資料だけを出典にする():
    citations = rag.extract_citations("答えBである [2]", HITS)
    assert [(c["n"], c["source"]) for c in citations] == [(2, "docs/b.txt")]
    assert citations[0]["title"] == "Q. 質問B"


def test_引用_存在しない番号は出典にしない():
    assert rag.extract_citations("答えである [0] [3] [9]", HITS) == []


def test_引用_同じ番号は1回で番号の順():
    citations = rag.extract_citations("B [2]。A [1]。B [2]", HITS)
    assert [c["n"] for c in citations] == [1, 2]


def test_回答_候補がなければLLMを呼ばない():
    def must_not_call(messages):
        raise AssertionError("LLM を呼んではいけない")

    result = rag.answer_question("質問", [], must_not_call)
    assert result == {"answer": rag.UNKNOWN, "citations": [], "unknown": True, "llmCalls": 0}


def test_回答_正しい引用がなければ分からないにする():
    result = rag.answer_question("質問", HITS, lambda m: "それは毎年4月に決まる [7]")
    assert result["unknown"] is True and result["answer"] == rag.UNKNOWN and result["llmCalls"] == 1


def test_回答_通しで一般の利用者には人事課の内容を答えない():
    question = "昇給の基準を教えて"
    result = rag.answer_question(question, search_as("user-aoi", question), mock_complete)
    assert result["unknown"] is True and "4%" not in result["answer"]


def test_回答_通しで人事課の利用者には引用つきで答える():
    question = "昇給の基準を教えて"
    result = rag.answer_question(question, search_as("user-kai", question), mock_complete)
    assert result["unknown"] is False
    assert [c["source"] for c in result["citations"]] == ["docs/jinji_kyuyo.txt"]
