"""アクセス制御付きの RAG と引用（コマ16）

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
    # TODO(コマ16 演習手順5-1): 上の例の形の式を返す。
    #   ・groups が空なら ValueError を出す（空の式では絞り込めない。絞り込まずに検索すると、全部が見えてしまう）
    #   ・GROUP_NAME.fullmatch に合わない名前があれば ValueError を出す
    #     （' や , を含む名前をそのまま式に入れると、式を書き換えられてしまう）
    #   いまは None を返している（絞り込まない）。
    return None


def retrieve(client, query, vector, groups, top=3):
    """利用者が見てよい文書だけを、ハイブリッド検索で探す"""
    if not groups:
        return []  # どのグループにも属さない利用者には、何も見せない（検索もしない）
    # TODO(コマ16 演習手順5-2): 検索に、絞り込みの式 security_filter(groups) を渡す（run_search の filter）。
    #   いまは絞り込まずに検索している。見てはいけない文書も、検索の候補になってしまう。
    return doc_search.run_search(client, "hybrid", query, vector, top)


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
    # TODO(コマ16 演習手順6-1): 上のとおりに、回答が実際に引用した資料だけを返す。
    #   回答から [数字] を探すには re.findall(r"\[(\d+)\]", answer) を使う。
    #   いまは、回答に関係なく、検索の結果を全部「出典」にしている。
    return [{"n": i, "source": h["source"], "title": title_of(h["content"])} for i, h in enumerate(hits, 1)]


def answer_question(question, hits, complete, system_prompt=""):
    """検索の結果をもとに答える。complete(messages) は LLM を呼んで回答の文字列を返す関数

    戻り値: {"answer": 回答, "citations": 出典の一覧, "unknown": 分からないと答えたか, "llmCalls": LLM を呼んだ回数}
    """
    # TODO(コマ16 演習手順6-2): 「分からない」と答える場面を2つ加える。
    #   ・hits が空なら、LLM を呼ばずに UNKNOWN を返す（llmCalls は 0）
    #   ・回答に正しい引用が1つもなければ（extract_citations が空なら）、回答を UNKNOWN に置き換える
    #     （根拠を示せない回答は、資料にないことを作った可能性がある）
    #   いまは、どちらの場面でも、LLM の回答をそのまま返している。
    answer = complete(build_messages(question, hits, system_prompt))
    citations = extract_citations(answer, hits)
    return {"answer": answer, "citations": citations, "unknown": False, "llmCalls": 1}
