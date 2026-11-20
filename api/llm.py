"""言語モデル（LLM）を呼ぶ部分（コマ7〜9・12・16。教員が用意したもの）

コマ16で、応答をまとめて返す complete_text（RAG の回答に使う）を追加した。
コマ17で、モックLLMに、LLM による自動評価（審査員）の応答を追加した。

コマ9で「プロバイダ」の形に整理した。
  プロバイダ = LLM の呼び先。同じ形の関数（complete_json・stream_chat・complete_tools）を持つ。
  complete_tools（コマ12）は、ツールの定義を渡して1回呼び、答えの文か、ツールの要求（tool_calls）を返す。
    MockProvider        : モックLLM（既定）。OpenRouter を呼ばない。共有の利用上限を消費しない
    OpenRouterProvider  : OpenRouter の本物のモデル。教員の合図があったときだけ使う
  呼ぶ側（function_app.py・resilience.py）は、どちらのプロバイダかを気にしなくてよい。
  呼び先は、アプリ設定 LLM_PROVIDER（mock / openrouter）で切り替える。

失敗は、次の2種類の例外に分けて知らせる（コマ9）。
  TransientLLMError : 待てば直るかもしれない失敗（429・502・503・408・タイムアウト・接続の失敗）
                      → 待ってから再試行する／別のモデルに切り替える価値がある
  LLMError          : 再試行しても直らない失敗（400・401・402・403、設定の誤り）
                      → すぐに諦める。同じ要求を送り直しても、同じ失敗になるだけ
モックも、本物と同じ例外を出せる（アプリ設定 MOCK_FAULT）。だから、本物を呼ばずに
「429 が返ってきたときの動き」を作って確かめられる。
"""

import hashlib
import itertools
import json
import os
import re
import time
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("llm_config.json")
PLACEHOLDER_PREFIX = "set-by-teacher/"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class LLMError(Exception):
    """LLM を呼べなかった（再試行しても直らない）。利用者に見せてよい文だけを入れる"""


class TransientLLMError(LLMError):
    """待てば直るかもしれない失敗。status は HTTP の状態コード（タイムアウトなどは None）

    retry_after は、提供元が「何秒待ってから送り直せ」と知らせてきた秒数（Retry-After ヘッダー）。
    知らせてこなかったときは None。
    """

    def __init__(self, message, status=None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def load_config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def provider_name():
    return os.environ.get("LLM_PROVIDER", "mock")


def model_candidates(config=None):
    """呼んでよいモデルを、使う順に返す（1つ目が主のモデル、2つ目以降がフォールバック）

    許可するのは、設定ファイルに書いた無料モデル（:free で終わるもの）だけ。
    フォールバックでも有料モデルは呼ばない（教員の費用になるため）。
    """
    config = config or load_config()
    names = [config["chat_model"]] + list(config.get("fallback_models", []))
    if provider_name() == "mock":
        return names  # モックは、教員がモデルを指定する前でも動くようにする
    return [m for m in names if m.endswith(":free") and not m.startswith(PLACEHOLDER_PREFIX)]


def tool_model_candidates(config=None):
    """ツール呼び出しに使ってよいモデル（コマ12）

    ツール呼び出しに対応したモデルは限られるので、チャットとは別に設定する（tool_model）。
    許可するのは、ここでも無料モデル（:free で終わるもの）だけ。
    """
    config = config or load_config()
    names = [config["tool_model"]] + list(config.get("tool_fallback_models", []))
    if provider_name() == "mock":
        return names
    return [m for m in names if m.endswith(":free") and not m.startswith(PLACEHOLDER_PREFIX)]


def get_provider():
    if provider_name() == "openrouter":
        return OpenRouterProvider()
    return MockProvider()


# =====================================================================
# アシスタント（コマ7）: 応答の文字列を少しずつ返す
#   ストリーミングは、最初の文字を画面に送った後では「やり直し」ができないので、
#   コマ9の再試行・フォールバックの対象にしていない（主のモデルを1回だけ呼ぶ）。
# =====================================================================
def stream_chat(messages, max_tokens):
    provider = get_provider()
    candidates = model_candidates()
    if not candidates:
        raise LLMError("呼んでよいモデルが設定されていない（教員の指示を待つ）")
    yield from provider.stream_chat(candidates[0], messages, max_tokens)


# =====================================================================
# RAG の回答（コマ16）: 応答をまとめて返す
#   モデルの候補を順に試す（主のモデルが一時的に失敗したら、フォールバックのモデル）。再試行はしない
# =====================================================================
def complete_text(messages, max_tokens):
    provider = get_provider()
    candidates = model_candidates()
    if not candidates:
        raise LLMError("呼んでよいモデルが設定されていない（教員の指示を待つ）")
    last_error = None
    for model in candidates:
        try:
            return provider.complete_text(model, messages, max_tokens)
        except TransientLLMError as e:
            last_error = e
    raise last_error


# =====================================================================
# モックLLM
# =====================================================================
# 分類のモック: 問い合わせの文字列に含まれる言葉で、決まった結果を返す
_MOCK_RULES = [
    (("パスワード", "ロック", "アカウント", "ログイン"), "アカウント・パスワード"),
    (("VPN", "Wi-Fi", "無線", "ネットワーク", "つながらない"), "ネットワーク・VPN"),
    (("PC", "パソコン", "プリンター", "モニター", "キーボード"), "PC・機器"),
    (("給与", "有給", "休暇", "年末調整", "勤怠"), "人事・給与"),
    (("会議室", "備品", "鍵", "駐車", "郵便", "空調"), "総務・施設"),
]

# rate_limit_once で「この要求は1回目か」を覚えておく（このインスタンスの中だけ）
_seen_requests = set()

# ツールの要求の ID を作る（モック。本物のモデルも、要求ごとに ID を付けて返す）
_call_ids = itertools.count(1)
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class MockProvider:
    """モックLLM。アプリ設定 MOCK_FAULT で、わざと失敗させられる

    形式の失敗（コマ8）
      none            : 正しい JSON を返す（既定）
      invalid_once    : 1回目だけ、決まりに合わない値を返す
      always_invalid  : 毎回、JSON ではない文を返す
    通信の失敗（コマ9）
      rate_limit_once : 同じ要求の1回目だけ、429（Retry-After: 2秒）で失敗する
      primary_down    : 主のモデルだけ、毎回 503 で失敗する（フォールバックのモデルは動く）
      all_rate_limited: どのモデルも、毎回 429（Retry-After: 2秒）で失敗する
    ツール呼び出しの失敗（コマ12）
      bad_tool_args   : ステータスの変更で、決まりにない値（「解決済み」）を引数に入れる
      unknown_tool    : 定義にないツール（delete_inquiry）を要求する
      tool_loop       : ツールの結果を受け取っても、同じ検索を要求し続ける（止まらない）
    エージェントの失敗（コマ13）
      error_loop      : 毎回違う、存在しない問い合わせの ID で get_inquiry を要求し続ける（エラーが続く）
      ignore_scope    : 渡されていない更新のツールも要求する（モデルが範囲を守らない）
    RAG の回答の失敗（コマ16）
      bad_citation    : 存在しない資料の番号（[9]）を引用する
      no_citation     : 引用を付けずに、資料にない内容をもっともらしく答える
    LLM による自動評価の失敗（コマ17）
      judge_format    : 審査員が、決めた JSON の形で答えない（点数を文で答える）
    """

    def __init__(self):
        self.fault = os.environ.get("MOCK_FAULT", "none")

    def _maybe_fail(self, model, messages):
        if self.fault == "all_rate_limited":
            raise TransientLLMError(f"利用上限に達した（429・モック・{model}）", 429, 2.0)
        if self.fault == "primary_down" and model == load_config()["chat_model"]:
            raise TransientLLMError(f"提供元が混雑している（503・モック・{model}）", 503, None)
        if self.fault == "rate_limit_once":
            key = hashlib.sha256((model + json.dumps(messages, ensure_ascii=False)).encode("utf-8")).hexdigest()
            if key not in _seen_requests:
                _seen_requests.add(key)
                raise TransientLLMError(f"利用上限に達した（429・モック・{model}）", 429, 2.0)

    def stream_chat(self, model, messages, max_tokens):
        """受け取った messages の様子を、そのまま文章にして少しずつ返す"""
        self._maybe_fail(model, messages)
        last = messages[-1]["content"]
        roles = [m["role"] for m in messages]
        chars = sum(len(m["content"]) for m in messages)
        text = (
            "（モックLLMの応答）"
            f"受け取ったメッセージは {len(messages)} 件"
            f"（system {roles.count('system')}・user {roles.count('user')}・"
            f"assistant {roles.count('assistant')}）、合計 {chars} 文字だった。"
            f"最後の質問の先頭は「{last[:20]}」である。"
        )
        for i in range(0, len(text), 4):
            time.sleep(0.08)  # 本物のように少しずつ届くことを再現する
            yield text[i : i + 4]

    def complete_text(self, model, messages, max_tokens):
        """RAG の回答のモック（コマ16）。資料と質問の言葉の重なりで、答えるか「分からない」かを決める

        質問の言葉（2文字ずつの並び）が、いちばん多く含まれる資料の「A.」の文を、その番号を付けて返す。
        重なりが少なければ、「資料からは分かりません」と返す。意味を理解しているわけではない。
        """
        self._maybe_fail(model, messages)
        if messages[-1]["content"].startswith(JUDGE_MARK):
            return self._judge(messages[-1]["content"])
        question, sources = _parse_rag_prompt(messages[-1]["content"])
        if self.fault == "no_citation":
            return "（モックLLMの応答）その件は、毎年4月に全社員へ一律で案内されることになっている。"
        best_n, best_score = None, 0.0
        grams = _bigrams(question)
        for n, text in sources.items():
            score = len(grams & _bigrams(text)) / len(grams) if grams else 0.0
            if score > best_score:
                best_n, best_score = n, score
        if best_n is None or best_score < _MOCK_RAG_THRESHOLD:
            return "（モックLLMの応答）資料からは分かりません。"
        answer_lines = [line for line in sources[best_n].splitlines() if line.startswith("A.")]
        answer = (answer_lines[0][2:] if answer_lines else sources[best_n]).strip()
        cite = 9 if self.fault == "bad_citation" else best_n
        return f"（モックLLMの応答）{answer} [{cite}]"

    def _judge(self, content):
        """LLM による自動評価（審査員）のモック（コマ17）。言葉の重なりで点数を付ける。意味は理解していない

          根拠性: 回答の言葉（2文字ずつの並び）のうち、資料にも含まれるものの割合
          関連性: 質問の言葉のうち、回答と、回答のもとになった資料（Q. の行を含む）にも含まれるものの割合
                  （言葉が重なれば関連があるとみなす。言い換えには弱く、期待する答えも知らない）
        """
        criterion = _section(content, "観点")
        question, sources, answer = _section(content, "質問"), _section(content, "資料"), _section(content, "回答")
        if self.fault == "judge_format":
            return "（モックの審査員）5点です。資料に沿ってよく書けていると思います。"
        answer_grams = _bigrams(re.sub(r"（モックLLMの応答）|\[\d+\]", "", answer))
        if criterion == "根拠性":
            base = _bigrams(sources)
            ratio = len(answer_grams & base) / len(answer_grams) if answer_grams else 0.0
        else:
            blocks = [_bigrams(b) for b in re.split(r"^\[\d+\] ", sources, flags=re.M) if b.strip()]
            origin = max(blocks, key=lambda b: len(b & answer_grams), default=set())
            grams = _bigrams(question)
            ratio = len(grams & (answer_grams | origin)) / len(grams) if grams else 0.0
        score = 1 + round(4 * ratio)
        return json.dumps({"score": score, "reason": f"（モックの審査員）言葉の重なりは {ratio:.0%} だった。"}, ensure_ascii=False)

    def complete_json(self, model, messages, schema, mode, max_tokens):
        """分類用のモック。決まった規則で分類し、JSON の文字列を返す"""
        self._maybe_fail(model, messages)
        retried = any(m["role"] == "assistant" for m in messages)  # 形式の再試行のときは、前の出力が入っている
        if self.fault == "always_invalid":
            return "分類しました。カテゴリはネットワーク関係で、急ぎだと思います。"
        if self.fault == "invalid_once" and not retried:
            return json.dumps(
                {"category": "ネットワーク", "priority": "緊急", "summary": "VPN に接続できない", "needs_human": False},
                ensure_ascii=False,
            )

        text = messages[1]["content"]  # 問い合わせの件名と本文
        category = "その他"
        for words, name in _MOCK_RULES:
            if any(w in text for w in words):
                category = name
                break
        if any(w in text for w in ("急ぎではない", "いつでもよい")):
            priority = "低"
        elif any(w in text for w in ("至急", "止まって", "できない")):
            priority = "高"
        else:
            priority = "中"
        title = text.split("\n")[0].replace("件名:", "").strip()
        result = {
            "category": category,
            "priority": priority,
            "summary": f"（モック）{title}"[:60],
            "needs_human": category == "その他",
        }
        body = json.dumps(result, ensure_ascii=False)
        if mode == "prompt":
            # 構造化出力を使わないモデルがよくやる返し方（前置きの文と、コードブロック）をまねる
            return f"分類結果は次のとおりです。\n```json\n{body}\n```"
        return body


    def complete_tools(self, model, messages, tools, max_tokens):
        """ツール呼び出しのモック（コマ12）。利用者の文から、決まった規則でツールを選ぶ

          - 「…」で囲んだ言葉があり、「探して」「検索」などがあれば → search_inquiries
          - 問い合わせの ID（UUID）と「対応中」「完了」があれば → update_inquiry_status
          - 「…」の言葉と「すべて」と「対応中」か「完了」があれば → 検索し、見つかったものを1件ずつ更新する（コマ13）
          - 最後のメッセージがツールの結果なら → 結果をまとめた答えの文を返す
        """
        self._maybe_fail(model, messages)
        _check_tool_messages(model, messages)
        names = {t["function"]["name"] for t in tools}
        last = messages[-1]
        if last["role"] == "tool":
            if self.fault == "tool_loop":
                return _mock_call("search_inquiries", {"keyword": "VPN"})
            if self.fault == "error_loop":
                return _mock_call("get_inquiry", {"inquiry_id": f"00000000-0000-4000-8000-{next(_call_ids):012d}"})
            following = _mock_next_update(messages, names, self.fault == "ignore_scope")
            if following:
                return following
            return {"content": _mock_summary(messages), "tool_calls": []}

        text = last["content"]
        if self.fault == "unknown_tool":
            return _mock_call("delete_inquiry", {"inquiry_id": "00000000-0000-0000-0000-000000000000"})
        found = _UUID.search(text)
        if found and "update_inquiry_status" in names:
            status = "完了" if "完了" in text else "対応中" if "対応中" in text else None
            if self.fault == "bad_tool_args":
                status = "解決済み"
            if status:
                return _mock_call("update_inquiry_status", {"inquiry_id": found.group(0), "status": status})
        quoted = re.search(r"「(.+?)」", text)
        if quoted and any(w in text for w in ("探", "検索", "調べ", "一覧", "ある", "すべて")) and "search_inquiries" in names:
            arguments = {"keyword": quoted.group(1)[:50]}
            if "未対応" in text:
                arguments["status"] = "未対応"
            return _mock_call("search_inquiries", arguments)
        return {
            "content": "（モックLLMの応答）ツールを使わずに答える。問い合わせを探すときは、言葉を「」で囲んで"
            "「探して」と書く。ステータスを変えるときは、問い合わせの ID と「対応中」か「完了」を書く。",
            "tool_calls": [],
        }


_MOCK_RAG_THRESHOLD = 0.4
JUDGE_MARK = "【評価の対象】"  # LLM による自動評価のプロンプト（evaluation/judge.py）の先頭の印
_RAG_SOURCE = re.compile(r"^\[(\d+)\] 出典: .*$", re.MULTILINE)
_JAPANESE_RUN = re.compile(r"[ぁ-んァ-ヶ一-龠ーA-Za-z0-9]+")
_STOP_BIGRAMS = {"とき", "きは", "する", "には", "のは", "たい", "ですか", "教え", "えて", "いか", "るか", "って"}


def _bigrams(text):
    grams = set()
    for run in _JAPANESE_RUN.findall(text):
        grams.update(run[i : i + 2] for i in range(len(run) - 1))
    return grams - _STOP_BIGRAMS


def _section(content, name):
    """【名前】の後ろから、次の【…】の前までを取り出す（自動評価のプロンプト用）"""
    m = re.search(rf"【{name}】\n(.*?)(?=\n【|\Z)", content, re.S)
    return m.group(1).strip() if m else ""


def _parse_rag_prompt(content):
    """rag.build_messages の形のプロンプトから、(質問, {番号: 資料の本文}) を取り出す"""
    body, _, question = content.partition("【質問】")
    sources = {}
    matches = list(_RAG_SOURCE.finditer(body))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sources[int(m.group(1))] = body[m.end() : end].strip()
    return question.strip(), sources


def _check_tool_messages(model, messages):
    """本物の API と同じく、tool_calls の後には、すべての要求に対応する tool のメッセージが必要"""
    for i, m in enumerate(messages):
        if m["role"] == "assistant" and m.get("tool_calls"):
            answered = {x.get("tool_call_id") for x in messages[i + 1 :] if x["role"] == "tool"}
            if any(c["id"] not in answered for c in m["tool_calls"]):
                raise LLMError(f"モデルを呼べなかった（HTTP 400・モック・{model}: ツールの要求に対応する結果がない）")


def _mock_next_update(messages, names, ignore_scope):
    """「すべて…にして」の依頼で、検索の結果のうち、まだ更新を要求していない1件の更新を要求する（コマ13）"""
    start = max(i for i, m in enumerate(messages) if m["role"] == "user")
    text = messages[start]["content"]
    status = "完了" if "「完了」" in text else "対応中" if "「対応中」" in text else None
    if "すべて" not in text or status is None:
        return None
    if "update_inquiry_status" not in names and not ignore_scope:
        return None
    found, requested = [], set()
    for m in messages[start + 1 :]:
        if m["role"] == "tool":
            found += [i["id"] for i in json.loads(m["content"]).get("items", [])]
        elif m.get("tool_calls"):
            for c in m["tool_calls"]:
                if c["function"]["name"] == "update_inquiry_status":
                    requested.add(json.loads(c["function"]["arguments"])["inquiry_id"])
    for inquiry_id in found:
        if inquiry_id not in requested:
            return _mock_call("update_inquiry_status", {"inquiry_id": inquiry_id, "status": status})
    return None


def _mock_call(name, arguments):
    return {
        "content": None,
        "tool_calls": [
            {"id": f"call_mock_{next(_call_ids)}", "name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}
        ],
    }


def _mock_summary(messages):
    """ツールの結果（最後の利用者の発言より後の tool のメッセージ）をまとめて、答えの文にする"""
    start = max(i for i, m in enumerate(messages) if m["role"] == "user")
    parts = []
    for m in messages[start + 1 :]:
        if m["role"] != "tool":
            continue
        result = json.loads(m["content"])
        if "error" in result:
            parts.append(f"ツールがエラーを返した（{result['error']}）")
        elif "items" in result:
            titles = "、".join(f"{i['title']}（{i['status']}）" for i in result["items"])
            parts.append(f"{result['count']} 件見つかった: {titles}" if result["items"] else "見つからなかった")
        elif "after" in result:
            parts.append(f"「{result['title']}」のステータスを {result['before']} から {result['after']} にした")
        elif "title" in result:
            parts.append(f"「{result['title']}」の状態は {result['status']} である")
    return "（モックLLMの応答）" + " ／ ".join(parts)


# =====================================================================
# 本物: OpenRouter（OpenAI 互換 API）
# =====================================================================
def _retry_after_seconds(error):
    """エラーの応答の Retry-After ヘッダー（秒）を読む。なければ None"""
    response = getattr(error, "response", None)
    value = response.headers.get("retry-after") if response is not None else None
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None  # 日時の形で来た場合などは、使わない（自分で待ち時間を決める）


def _translate_error(error, model):
    """OpenAI の SDK の例外を、このアプリの2種類の例外に変える"""
    from openai import APIConnectionError, APIStatusError, APITimeoutError

    if isinstance(error, APIStatusError):
        status = error.status_code
        if status == 429:
            return TransientLLMError(f"利用上限に達した（429・{model}）", status, _retry_after_seconds(error))
        if status in (408, 502, 503):
            return TransientLLMError(f"提供元が応答できなかった（HTTP {status}・{model}）", status, _retry_after_seconds(error))
        # 400（要求の誤り）・401（キーの誤り）・402（残高不足）・403（禁止）などは、送り直しても直らない
        return LLMError(f"モデルを呼べなかった（HTTP {status}・{model}）")
    if isinstance(error, (APITimeoutError, APIConnectionError)):
        return TransientLLMError(f"モデルから時間内に応答がなかった（{model}）")
    return LLMError(f"モデルを呼べなかった（{model}）")


class OpenRouterProvider:
    def _client(self, model):
        from openai import OpenAI

        if model.startswith(PLACEHOLDER_PREFIX):
            raise LLMError("モデルが指定されていない（教員の指示を待つ）")
        if not model.endswith(":free"):
            # 許可するモデルは無料モデルだけ。誤って有料モデルを指定しても呼ばない
            raise LLMError("無料モデル以外は呼ばない設定になっている")
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key.startswith("sk-or-"):
            raise LLMError("OpenRouter のキーが届いていない（Key Vault を確かめる）")
        # max_retries=0: SDK の自動の再試行を止める。再試行は resilience.py で、回数を数えながら自分で行う
        #   （SDK も再試行すると、回数が掛け算で増え、共有の利用上限を見えないところで消費する）
        return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key, timeout=30, max_retries=0)

    def stream_chat(self, model, messages, max_tokens):
        from openai import OpenAIError

        client = self._client(model)
        try:
            stream = client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, stream=True
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason == "error":
                    # 途中で失敗した（HTTP の状態は 200 のまま、イベントとして届く）
                    raise LLMError("モデルの応答が途中で失敗した")
                if choice.delta and choice.delta.content:
                    yield choice.delta.content
        except OpenAIError as e:
            raise _translate_error(e, model) from e

    def complete_text(self, model, messages, max_tokens):
        from openai import OpenAIError

        client = self._client(model)
        try:
            response = client.chat.completions.create(model=model, messages=messages, max_tokens=max_tokens)
        except OpenAIError as e:
            raise _translate_error(e, model) from e
        if not response.choices or response.choices[0].message.content is None:
            raise LLMError("モデルの応答が空だった")
        return response.choices[0].message.content

    def complete_json(self, model, messages, schema, mode, max_tokens):
        from openai import OpenAIError

        client = self._client(model)
        options = {}
        if mode == "json_schema":
            options["response_format"] = {"type": "json_schema", "json_schema": schema}
            # 構造化出力に対応した提供元だけに送ってもらう（OpenRouter の機能）
            options["extra_body"] = {"provider": {"require_parameters": True}}
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0,  # 同じ入力には、なるべく同じ出力を返させる
                **options,
            )
        except OpenAIError as e:
            raise _translate_error(e, model) from e
        if not response.choices:
            # 状態コードが 200 でも、本文がエラーで中身がないことがある（提供元の途中の失敗）
            raise TransientLLMError(f"モデルの応答が空だった（{model}）")
        return response.choices[0].message.content or ""

    def complete_tools(self, model, messages, tools, max_tokens):
        """ツールの定義を渡して1回呼ぶ（コマ12）。答えの文か、ツールの要求を返す"""
        from openai import OpenAIError

        client = self._client(model)
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,  # ツールの定義は、要求のたびに渡す（API は前の要求を覚えていない）
                tool_choice="auto",  # ツールを使うかどうかは、モデルに任せる
                max_tokens=max_tokens,
                temperature=0,
                # ツール呼び出しに対応した提供元だけに送ってもらう（OpenRouter の機能）
                extra_body={"provider": {"require_parameters": True}},
            )
        except OpenAIError as e:
            raise _translate_error(e, model) from e
        if not response.choices:
            raise TransientLLMError(f"モデルの応答が空だった（{model}）")
        message = response.choices[0].message
        return {
            "content": message.content,
            "tool_calls": [
                {"id": c.id, "name": c.function.name, "arguments": c.function.arguments or "{}"}
                for c in (message.tool_calls or [])
            ],
        }
