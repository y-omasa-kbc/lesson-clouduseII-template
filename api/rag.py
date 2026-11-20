"""アクセス制御付きの RAG と引用（コマ16。コマ17の開始状態では、コマ16の演習の解答を反映済み）

流れ（POST /api/ask）
  1. 利用者のグループを、サーバー側で決める                  groups_for
       トークンで確かめた利用者から決める。要求の本文や画面から送られた値は使わない
  2. グループから、検索の絞り込み（セキュリティフィルター）を作る   security_filter
  3. 絞り込んで検索する                                      retrieve
       見てよい文書だけが、検索の候補になる。LLM には、見てはいけない文書が最初から渡らない
  4. 候補がなければ、LLM を呼ばずに「分からない」と答える         answer_question
  5. 番号を付けた資料と質問を LLM に渡し、番号の引用を付けて答えさせる  build_messages
  6. 回答の引用 [n] を確かめる。正しい引用が1つもなければ「分からない」にする  extract_citations

このファイルの関数は、Azure にも OpenRouter にもつながずにテストできる
（検索の client と、LLM を呼ぶ関数 complete を、呼ぶ側から受け取る）。
"""

import re

import doc_search

UNKNOWN = "社内の資料からは分かりません。担当の部署に確認してください。"
GROUP_NAME = re.compile(r"[a-z][a-z0-9-]{0,31}")  # グループの名前として認める形


# =====================================================================
# 1. 利用者のグループ
# =====================================================================
def groups_for(principal, users):
    """利用者が属するグループの一覧を返す。分からない利用者は、どのグループにも属さない（何も見えない）

      users : テスト利用者の一覧（api/dev_users.json）。本番では、ID プロバイダーのグループの情報や、
              利用者のデータベースから決める
    """
    for user in users:
        if user["id"] == principal.user_id:
            return list(user.get("groups", []))
    return []


# =====================================================================
# 2. セキュリティフィルター
# =====================================================================
def security_filter(groups):
    """グループの一覧から、AI Search の絞り込みの式を作る

    例: ["all", "hr"] → allowedGroups/any(g: search.in(g, 'all,hr', ','))
        「allowedGroups のどれかが、all か hr のどちらかと一致する文書」だけが検索の対象になる
    """
    if not groups:
        raise ValueError("グループがない")  # 空の式では絞り込めない。絞り込まずに検索すると、全部が見えてしまう
    for g in groups:
        if not GROUP_NAME.fullmatch(g):
            raise ValueError(f"グループの名前が正しくない: {g!r}")  # ' や , を含む名前は、式を書き換えられる
    return f"allowedGroups/any(g: search.in(g, '{','.join(groups)}', ','))"


def retrieve(client, query, vector, groups, top=3, mode="hybrid"):
    """利用者が見てよい文書だけを検索する（既定はハイブリッド検索）

    top・mode は、コマ17で引数にした（評価で、件数と検索の方式を変えて比べるため。アプリの値は llm_config.json の
    rag_top_k・rag_search_mode）。
    """
    if not groups:
        return []  # どのグループにも属さない利用者には、何も見せない（検索もしない）
    return doc_search.run_search(client, mode, query, vector, top, filter=security_filter(groups))


# =====================================================================
# 3. 回答と引用
# =====================================================================
def title_of(content):
    """資料の見出し（先頭の行）"""
    return content.strip().splitlines()[0][:60] if content.strip() else ""


def build_messages(question, hits, system_prompt):
    """LLM に渡すメッセージ。資料には [1] から番号を付け、出典を添える"""
    sources = "\n\n".join(f"[{i}] 出典: {h['source']}\n{h['content']}" for i, h in enumerate(hits, 1))
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"【資料】\n{sources}\n\n【質問】\n{question}"},
    ]


def extract_citations(answer, hits):
    """回答の中の引用 [n] を集め、出典の一覧 [{"n", "source", "title"}] を、番号の順に返す

    hits に存在しない番号（[0] や、資料の数より大きい番号）は、出典にしない。同じ番号は1回だけにする。
    """
    numbers = sorted({int(n) for n in re.findall(r"\[(\d+)\]", answer)})
    return [
        {"n": n, "source": hits[n - 1]["source"], "title": title_of(hits[n - 1]["content"])}
        for n in numbers
        if 1 <= n <= len(hits)
    ]


def answer_question(question, hits, complete, system_prompt=""):
    """検索の結果をもとに答える。complete(messages) は LLM を呼んで回答の文字列を返す関数

    戻り値: {"answer": 回答, "citations": 出典の一覧, "unknown": 分からないと答えたか, "llmCalls": LLM を呼んだ回数}
    """
    if not hits:
        return {"answer": UNKNOWN, "citations": [], "unknown": True, "llmCalls": 0}  # LLM を呼ばない
    answer = complete(build_messages(question, hits, system_prompt))
    citations = extract_citations(answer, hits)
    if not citations:
        # 正しい引用が1つもない回答は、資料にないことを作った可能性がある
        return {"answer": UNKNOWN, "citations": [], "unknown": True, "llmCalls": 1}
    return {"answer": answer, "citations": citations, "unknown": False, "llmCalls": 1}
