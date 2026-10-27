"""api/jobs.py を確かめるテスト（コマ10）

実行のしかた（Codespace のターミナルで。Azure にも OpenRouter にもつながない）
  pip install pytest pydantic
  python -m pytest lesson10/test_jobs.py -v

Cosmos DB の代わりに、辞書に保存する「偽物の保存先」（FakeStore）を使う。
分類の代わりに、決まった結果を返すか例外を出す「偽物の分類」（FakeClassifier）を使う。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

import jobs  # noqa: E402
from llm import LLMError, TransientLLMError  # noqa: E402

NOW = "2026-10-22T10:00:00+00:00"
A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"


class FakeStore:
    def __init__(self):
        self.results = {}

    def get_result(self, job_id, inquiry_id):
        return self.results.get((job_id, inquiry_id))

    def save_result(self, doc):
        self.results[(doc["jobId"], doc["id"])] = dict(doc)

    def add_duplicate(self, job_id, inquiry_id):
        self.results[(job_id, inquiry_id)]["duplicates"] += 1


class FakeClassifier:
    """呼ばれるたびに、用意した結果（(status, detail)）か例外を、先頭から1つずつ使う"""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, inquiry_id):
        self.calls.append(inquiry_id)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_job(ids):
    header, messages = jobs.new_job(ids, NOW)
    return header, messages


def result(status, processed=1, duplicates=0, inquiry_id=A):
    return {"id": inquiry_id, "jobId": "j", "type": "result", "status": status, "processed": processed, "duplicates": duplicates}


# ---------------------------------------------------------------------
# 演習手順4: ジョブの状態
# ---------------------------------------------------------------------
def test_1件も終わっていなければqueued():
    header, _ = make_job([A, B])
    assert jobs.summarize(header, [])["state"] == "queued"


def test_途中ならrunning():
    header, _ = make_job([A, B])
    summary = jobs.summarize(header, [result(jobs.DONE)])
    assert summary["state"] == "running"
    assert summary["finished"] == 1 and summary["pending"] == 1


def test_全件が終わればcompleted():
    header, _ = make_job([A, B])
    summary = jobs.summarize(header, [result(jobs.DONE), result(jobs.FAILED, inquiry_id=B)])
    assert summary["state"] == "completed"
    assert summary["done"] == 1 and summary["failed"] == 1


def test_失敗が含まれていても全件が終わればcompleted():
    header, _ = make_job([A])
    assert jobs.summarize(header, [result(jobs.FAILED)])["state"] == "completed"


# ---------------------------------------------------------------------
# 演習手順5: 冪等性（同じメッセージが2回届いても、分類は1回だけ）
# ---------------------------------------------------------------------
def test_1回目は分類して結果を保存する():
    header, messages = make_job([A])
    store = FakeStore()
    classify = FakeClassifier((jobs.DONE, "情報システム課"))
    assert jobs.process_message(messages[0], store, classify, NOW) == jobs.DONE
    assert store.get_result(header["jobId"], A)["status"] == jobs.DONE


def test_同じメッセージが2回届いても2回目は分類しない():
    header, messages = make_job([A])
    store = FakeStore()
    classify = FakeClassifier((jobs.DONE, "情報システム課"), (jobs.DONE, "情報システム課"))
    jobs.process_message(messages[0], store, classify, NOW)
    assert jobs.process_message(messages[0], store, classify, NOW) == "skipped"
    assert len(classify.calls) == 1  # LLM を呼ぶ分類は1回だけ
    saved = store.get_result(header["jobId"], A)
    assert saved["duplicates"] == 1 and saved["processed"] == 1


def test_重複を無視した回数がジョブの状態に出る():
    header, messages = make_job([A])
    store = FakeStore()
    classify = FakeClassifier((jobs.NEEDS_REVIEW, "人が確認"), (jobs.DONE, "x"))
    jobs.process_message(messages[0], store, classify, NOW)
    jobs.process_message(messages[0], store, classify, NOW)
    summary = jobs.summarize(header, list(store.results.values()))
    assert summary["duplicates"] == 1 and summary["reprocessed"] == 0


# ---------------------------------------------------------------------
# 演習手順6: 失敗の扱い
# ---------------------------------------------------------------------
def test_問い合わせがなければ失敗として記録し例外を出さない():
    header, messages = make_job([A])
    store = FakeStore()
    classify = FakeClassifier(jobs.InquiryGone("見つからない"))
    assert jobs.process_message(messages[0], store, classify, NOW) == jobs.FAILED
    assert store.get_result(header["jobId"], A)["status"] == jobs.FAILED


def test_LLMを呼べなければ例外を出してキューに再実行させる():
    header, messages = make_job([A])
    store = FakeStore()
    classify = FakeClassifier(TransientLLMError("429", 429, 2))
    with pytest.raises(LLMError):
        jobs.process_message(messages[0], store, classify, NOW)
    assert store.get_result(header["jobId"], A) is None  # 「失敗」と記録しない（まだ終わっていない）


def test_再実行で成功すれば完了になる():
    header, messages = make_job([A])
    store = FakeStore()
    classify = FakeClassifier(TransientLLMError("429", 429, 2), (jobs.DONE, "情報システム課"))
    with pytest.raises(LLMError):
        jobs.process_message(messages[0], store, classify, NOW)
    assert jobs.process_message(messages[0], store, classify, NOW) == jobs.DONE


# ---------------------------------------------------------------------
# 教員が用意した部分（はじめから通る）
# ---------------------------------------------------------------------
def test_壊れたメッセージは再実行させずに捨てる():
    assert jobs.process_message("これは JSON ではない", FakeStore(), FakeClassifier(), NOW) == "invalid"


def test_毒キューに移ったメッセージは失敗として記録する():
    header, messages = make_job([A])
    store = FakeStore()
    assert jobs.record_poison(messages[0], store, NOW) == jobs.FAILED
    summary = jobs.summarize(header, list(store.results.values()))
    assert summary["failed"] == 1
