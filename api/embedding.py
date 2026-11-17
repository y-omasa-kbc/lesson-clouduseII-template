"""埋め込み（コマ14。教員が用意したもの。コマ15で、文書をまとめて埋め込む embed_documents を追加した）

文書の埋め込みは、教員が事前に作って配っている（lesson14/vector_store.json）。学生が作るのは、検索の質問の埋め込みだけである。
文書と質問は、必ず同じ方法で埋め込む。違う方法で作ったベクトルどうしは、比べても意味がない。

  mock       : モック埋め込み（既定）。OpenRouter を呼ばず、費用がかからない
  openrouter : 本物の埋め込みモデル。配布データも本物で作ってあるときだけ使える

モック埋め込みは、本物の埋め込みモデルの性質を、演習に必要な範囲でまねたものである。
  - 同じ意味の言葉のまとまり（CONCEPTS）に、同じ次元を割り当てる。言い換えた質問でも近くなる
  - 英数字と記号は無視する。本物のモデルも、型番や番号の完全な一致は苦手である
実際の意味を理解しているわけではない。CONCEPTS にない言い換えには対応できない。
"""

import hashlib
import json
import math
import os
import re
from pathlib import Path

DIMENSIONS = 256
CONCEPT_DIMENSIONS = 64  # 先頭の次元を言葉のまとまりに、残りを文字の並びに使う
CONCEPT_WEIGHT = 3.0
BIGRAM_WEIGHT = 0.3

CONCEPTS = [
    ("休暇", "休み", "休む", "休暇", "有給", "年休"),
    ("PC", "パソコン", "ノートPC", "端末", "PC"),
    ("持ち出し", "持ち出", "持ち帰", "社外に"),
    ("経費", "経費", "精算", "領収書", "立替", "立て替え"),
    ("出張", "出張", "旅費", "宿泊"),
    ("パスワード", "パスワード", "ログインできない", "サインインできない"),
    ("リモート", "VPN", "社外から", "在宅勤務", "テレワーク", "自宅から"),
    ("迷惑メール", "怪しいメール", "不審なメール", "迷惑メール", "フィッシング"),
    ("紛失", "なくした", "紛失", "失くした", "落とした"),
    ("社員証", "社員証", "入館証", "IDカード"),
    ("印刷", "複合機", "プリンター", "印刷", "コピー機"),
    ("来客", "来客", "お客様", "訪問者"),
    ("勤務時間", "始業", "終業", "勤務時間", "何時から"),
    ("締め切り", "締め切り", "期限", "いつまで"),
]

_JAPANESE = re.compile(r"[ぁ-んァ-ヶ一-龠ー]+")


class EmbeddingError(Exception):
    pass


def _bucket(text):
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return CONCEPT_DIMENSIONS + int.from_bytes(digest[:4], "big") % (DIMENSIONS - CONCEPT_DIMENSIONS)


def mock_embed(text):
    """文字列からベクトルを作る（同じ文字列からは、いつも同じベクトルができる）"""
    vector = [0.0] * DIMENSIONS
    for i, (_, *words) in enumerate(CONCEPTS):
        if any(w in text for w in words):
            vector[i] += CONCEPT_WEIGHT
    for run in _JAPANESE.findall(text):
        for a, b in zip(run, run[1:]):
            vector[_bucket(a + b)] += BIGRAM_WEIGHT
    vector[-1] += 0.05  # すべて 0 のベクトルにしない（コサイン類似度を計算できなくなる）
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [round(v / norm, 6) for v in vector]


def openrouter_embed(texts, model):
    """本物の埋め込みモデルを呼ぶ（教員の費用になる。呼ぶ回数を最小にする）"""
    from openai import OpenAI, OpenAIError

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key.startswith("sk-or-"):
        raise EmbeddingError("OpenRouter のキーが届いていない")
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key, timeout=30, max_retries=0)
    try:
        response = client.embeddings.create(model=model, input=texts)
    except OpenAIError as e:
        raise EmbeddingError(f"埋め込みを作れなかった（{model}）") from e
    return [d.embedding for d in response.data]


def _configured_model():
    config = json.loads(Path(__file__).with_name("llm_config.json").read_text(encoding="utf-8"))
    model = config["embedding_model"]
    if model.startswith("set-by-teacher/"):
        raise EmbeddingError("埋め込みモデルが指定されていない（教員の指示を待つ）")
    return model


def document_model_name():
    """文書の埋め込みに使う方法の名前（コマ15）。埋め込みキャッシュのキーに入れる"""
    provider = os.environ.get("EMBEDDING_PROVIDER", "mock")
    if provider == "mock":
        return "mock"
    if provider != "openrouter":
        raise EmbeddingError(f"埋め込みの呼び先 {provider} は使えない")
    return _configured_model()


def embed_documents(texts):
    """文書のチャンクをまとめて埋め込む（コマ15）。本物のときは、1回の要求にまとめて送る"""
    if not texts:
        return []
    model = document_model_name()
    if model == "mock":
        return [mock_embed(t) for t in texts]
    return openrouter_embed(list(texts), model)


def embed_query(text, info=None):
    """検索の質問を埋め込む。呼び先は環境変数 EMBEDDING_PROVIDER で決める"""
    provider = os.environ.get("EMBEDDING_PROVIDER", "mock")
    if provider == "mock":
        return mock_embed(text)
    if provider != "openrouter":
        raise EmbeddingError(f"埋め込みの呼び先 {provider} は使えない")
    config = json.loads(Path(__file__).with_name("llm_config.json").read_text(encoding="utf-8"))
    model = config["embedding_model"]
    if model.startswith("set-by-teacher/"):
        raise EmbeddingError("埋め込みモデルが指定されていない（教員の指示を待つ）")
    if info is not None and info.get("model") != model:
        raise EmbeddingError("文書の埋め込みと違う方法では、質問を埋め込めない（配布データを確かめる）")
    return openrouter_embed([text], model)[0]
