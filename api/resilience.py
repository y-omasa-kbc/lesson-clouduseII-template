"""外部の AI API の失敗を、設計で吸収する（コマ9。コマ10の開始状態では演習の解答を反映済み）

  1. 待ち時間の計算（指数バックオフとジッター。Retry-After があれば従う）
  2. 再試行とフォールバック（主のモデルで数回 → だめなら次のモデルへ）
  3. 1件の処理で LLM を呼ぶ回数の上限（CallBudget）

このファイルの関数は、Azure にも OpenRouter にもつながずにテストできる
（モデルを呼ぶ関数 call_model と、待つ関数 sleep を、引数で受け取るようにしてある）。
"""

import random
import time
from dataclasses import dataclass, field

from llm import LLMError, TransientLLMError


# =====================================================================
# 1. 待ち時間の計算
# =====================================================================
def backoff_delay(retry_number, base, cap, retry_after=None, rand=random.random):
    """retry_number 回目の再試行の前に、何秒待つかを返す（retry_number は 1, 2, 3, …）

      - 提供元が Retry-After（retry_after 秒）を知らせてきたら、それに従う
      - そうでなければ、指数バックオフ: base × 2^(retry_number - 1) 秒。ただし cap 秒を超えない
      - さらにジッター: 0 〜 その秒数 の間で、ランダムに選ぶ（rand() は 0 以上 1 未満の数を返す）
    """
    if retry_after is not None:
        return retry_after
    return rand() * min(cap, base * 2 ** (retry_number - 1))


# =====================================================================
# 3. 1件の処理で LLM を呼ぶ回数の上限
# =====================================================================
class CallBudget:
    """1件の処理（例: 問い合わせ1件の分類）で、LLM を呼んでよい回数の上限

    形式の再試行（コマ8）と、通信の再試行・フォールバック（コマ9）は、重なると掛け算で増える。
      例: 形式の再試行 3回 × 通信の再試行 3回 × モデル 2つ = 最悪 18回
    そこで、どこから呼んでも同じ「残り回数」を減らすようにして、合計に上限を付ける。
    """

    def __init__(self, max_calls):
        self.max_calls = max_calls
        self.used = 0

    def take(self):
        if self.used >= self.max_calls:
            raise LLMError(f"1件の処理で LLM を呼べる回数の上限（{self.max_calls}回）に達した")
        self.used += 1


# =====================================================================
# 2. 再試行とフォールバック
# =====================================================================
@dataclass
class ResilientResult:
    text: str
    model: str  # 実際に答えたモデル
    log: list = field(default_factory=list)  # 呼んだ記録（画面に表示する）


def call_with_resilience(
    call_model,
    models,
    budget,
    max_retries_per_model,
    base_delay,
    max_delay,
    max_wait,
    sleep=time.sleep,
    rand=random.random,
    log=None,
):
    """models の順にモデルを呼ぶ。待てば直るかもしれない失敗なら、待って再試行する

      call_model(model)      : そのモデルを1回呼び、出力の文字列を返す関数
      max_retries_per_model  : 1つのモデルで、最初の1回の後に再試行してよい回数
      max_wait               : 1回に待ってよい秒数の上限。これより長く待てと言われたら、待たずに次のモデルへ
      log                    : 呼んだ記録を書き足すリスト（途中で諦めたときも、どこまで試したかが残る）
    """
    log = [] if log is None else log
    last_error = None
    for model in models:
        for retry_number in range(0, max_retries_per_model + 1):
            budget.take()  # 上限に達していたら LLMError が出て、ここで止まる
            try:
                text = call_model(model)
                log.append(f"{model}: 成功")
                return ResilientResult(text, model, log)
            except TransientLLMError as e:
                last_error = e
                log.append(f"{model}: {e}")
                if retry_number == max_retries_per_model:
                    break  # このモデルでの再試行を使い切った → 次のモデルへ
                delay = backoff_delay(retry_number + 1, base_delay, max_delay, e.retry_after, rand)
                if delay > max_wait:
                    break  # 長く待ちすぎる → 待たずに次のモデルへ
                log.append(f"{delay:.1f} 秒待って、もう一度")
                sleep(delay)
            # LLMError（400・401・402 など、送り直しても直らない失敗）は、ここでは捕まえずに、そのまま外へ出る

    raise LLMError(f"どのモデルからも応答を得られなかった（最後の失敗: {last_error}）") from last_error
