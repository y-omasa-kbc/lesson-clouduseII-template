"""問い合わせをまとめて分類する「非同期ジョブ」（コマ10）

流れ
  1. POST /api/jobs/classify
       ジョブを作り（Cosmos DB の jobs コンテナー）、問い合わせ1件ごとにメッセージをキューに入れて、
       分類を待たずに、すぐ 202（受け付けた）とジョブの ID を返す
  2. キュー トリガーの関数（classify_worker）が、メッセージを1件ずつ取り出して分類する
       失敗したら、キューが少し時間をおいて、同じメッセージをもう一度渡してくる（再実行）
  3. GET /api/jobs?id=...
       画面が、ジョブの状態を数秒ごとに問い合わせる（ポーリング）
  4. 何度再実行しても失敗するメッセージは、毒キュー（classify-jobs-poison）に移される
       → classify_poison が「失敗」として記録する（そうしないと、ジョブがいつまでも終わらない）

jobs コンテナーのドキュメント（パーティションキーは /jobId。1つのジョブの記録は同じパーティションに入る）
  ジョブの見出し : {"id": ジョブID, "jobId": ジョブID, "type": "job", "total": 件数, "createdAt": …}
  1件ごとの結果  : {"id": 問い合わせID, "jobId": ジョブID, "type": "result", "status": …, "detail": …,
                    "processed": 分類した回数, "duplicates": 重複として無視した回数, "updatedAt": …}
  結果の id を「問い合わせID」に決めておくと、同じジョブの同じ問い合わせの結果は、何度保存しても1件になる。

このファイルの関数は、Azure にも OpenRouter にもつながずにテストできる
（保存先 store と、1件を分類する関数 classify_one を、引数で受け取るようにしてある）。
"""

import json
import uuid

QUEUE_NAME = "classify-jobs"
POISON_QUEUE_NAME = QUEUE_NAME + "-poison"  # Azure Functions が自動で使う名前（<キューの名前>-poison）

# 1件ごとの結果の status
DONE = "done"  # 分類して、担当に振り分けた
NEEDS_REVIEW = "needs_review"  # 分類したが、人が確認する（AI が判断できなかった・形式の検証に上限まで失敗した）
FAILED = "failed"  # 分類できなかった（問い合わせが消えていた・再実行の上限まで失敗した）
FINISHED = (DONE, NEEDS_REVIEW, FAILED)


class InquiryGone(Exception):
    """問い合わせが見つからない（削除された）。何度再実行しても直らない失敗"""


# =====================================================================
# ジョブを作る
# =====================================================================
def new_job(inquiry_ids, now):
    """ジョブの見出しと、キューに入れるメッセージ（問い合わせ1件につき1つ）を作る"""
    job_id = str(uuid.uuid4())
    header = {"id": job_id, "jobId": job_id, "type": "job", "total": len(inquiry_ids), "createdAt": now}
    messages = [json.dumps({"jobId": job_id, "inquiryId": i}) for i in inquiry_ids]
    return header, messages


def parse_message(body):
    """メッセージの文字列から、ジョブの ID と問い合わせの ID を取り出す。形が正しくなければ ValueError"""
    data = json.loads(body)
    return str(uuid.UUID(str(data["jobId"]))), str(uuid.UUID(str(data["inquiryId"])))


def result_doc(job_id, inquiry_id, status, detail, now, processed=1, duplicates=0):
    return {
        "id": inquiry_id,
        "jobId": job_id,
        "type": "result",
        "status": status,
        "detail": detail,
        "processed": processed,
        "duplicates": duplicates,
        "updatedAt": now,
    }


# =====================================================================
# ジョブの状態をまとめる（GET /api/jobs）
# =====================================================================
def summarize(header, results):
    """ジョブの見出しと、1件ごとの結果のリストから、画面に返す状態を作る

    返す辞書の項目
      state       : "queued"（まだ1件も終わっていない）／"running"（途中）／"completed"（全件が終わった）
      total       : 件数（見出しの total）
      finished    : 終わった件数（status が FINISHED のどれかである結果の数）
      done・needsReview・failed : status ごとの件数
      pending     : まだ終わっていない件数（total - finished）
      duplicates  : 重複として無視した回数の合計（結果の duplicates の合計）
      reprocessed : 同じ問い合わせを2回以上分類した回数の合計（結果の processed - 1 の合計）
    """
    counts = {DONE: 0, NEEDS_REVIEW: 0, FAILED: 0}
    for r in results:
        if r["status"] in counts:
            counts[r["status"]] += 1
    finished = sum(counts.values())

    # TODO(コマ10 演習手順4): state を決める。いまは、いつも "queued" を返している。
    state = "queued"

    return {
        "jobId": header["jobId"],
        "state": state,
        "total": header["total"],
        "finished": finished,
        "done": counts[DONE],
        "needsReview": counts[NEEDS_REVIEW],
        "failed": counts[FAILED],
        "pending": header["total"] - finished,
        "duplicates": sum(r.get("duplicates", 0) for r in results),
        "reprocessed": sum(max(r.get("processed", 1) - 1, 0) for r in results),
        "results": [
            {"inquiryId": r["id"], "status": r["status"], "detail": r.get("detail", ""), "processed": r.get("processed", 1)}
            for r in results
        ],
    }


# =====================================================================
# メッセージ1件を処理する（classify_worker から呼ぶ）
# =====================================================================
def process_message(body, store, classify_one, now):
    """メッセージ1件を処理し、何をしたかを文字列で返す（"skipped"・"invalid"・結果の status）

      store                : 結果の保存先（get_result・save_result・add_duplicate を持つ）
      classify_one(inquiry_id)
                           : 問い合わせ1件を分類して保存し、(status, detail) を返す関数
                             問い合わせがなければ InquiryGone を、LLM を呼べなければ llm.LLMError を出す

    この関数が例外を出すと、Azure Functions は「処理に失敗した」とみなし、
    キューが少し時間をおいて同じメッセージをもう一度渡してくる（上限の回数を超えたら毒キューへ）。
    """
    try:
        job_id, inquiry_id = parse_message(body)
    except (ValueError, KeyError, TypeError):
        return "invalid"  # 形が壊れたメッセージは、何度やり直しても読めない。再実行させずに捨てる

    existing = store.get_result(job_id, inquiry_id)

    # TODO(コマ10 演習手順5): 冪等にする。
    #   existing があり、その status が FINISHED のどれかなら、もう分類は終わっている。
    #   分類し直さずに、store.add_duplicate(job_id, inquiry_id) で重複として数え、"skipped" を返す。
    #   いまは、同じメッセージが2回届くと、2回とも分類している（LLM を2回呼ぶ）。

    processed = (existing or {}).get("processed", 0) + 1

    # TODO(コマ10 演習手順6): 失敗を「再実行しても直らないもの」と「待てば直るかもしれないもの」に分ける。
    #   - InquiryGone（問い合わせがない）は、何度やっても直らない → "failed" として記録して、正常に終わる
    #   - llm.LLMError（429 が続いた など）は、ここでは捕まえない → 例外のまま外へ出し、キューに再実行させる
    #   いまは、どんな失敗も "failed" として記録して終わりにしている（キューの再実行が働かない）。
    try:
        status, detail = classify_one(inquiry_id)
    except Exception as e:  # noqa: BLE001
        store.save_result(result_doc(job_id, inquiry_id, FAILED, str(e), now, processed))
        return FAILED

    store.save_result(result_doc(job_id, inquiry_id, status, detail, now, processed))
    return status


def record_poison(body, store, now):
    """毒キューに移されたメッセージを、「失敗」として記録する（classify_poison から呼ぶ）"""
    try:
        job_id, inquiry_id = parse_message(body)
    except (ValueError, KeyError, TypeError):
        return "invalid"
    existing = store.get_result(job_id, inquiry_id)
    if existing and existing["status"] in FINISHED:
        return "skipped"  # すでに終わっている（別に届いた同じメッセージで処理済み）
    store.save_result(result_doc(job_id, inquiry_id, FAILED, "再実行の上限まで失敗した（毒キューに移された）", now, 0))
    return FAILED


# =====================================================================
# Cosmos DB を使った保存先（教員が用意したもの）
# =====================================================================
class CosmosJobStore:
    def __init__(self, container):
        self.container = container

    def create_job(self, header):
        self.container.create_item(header)

    def get_header(self, job_id):
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            return self.container.read_item(item=job_id, partition_key=job_id)
        except CosmosResourceNotFoundError:
            return None

    def list_results(self, job_id):
        query = "SELECT * FROM c WHERE c.jobId = @job AND c.type = 'result'"
        return list(
            self.container.query_items(
                query=query, parameters=[{"name": "@job", "value": job_id}], partition_key=job_id
            )
        )

    def get_result(self, job_id, inquiry_id):
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            item = self.container.read_item(item=inquiry_id, partition_key=job_id)
        except CosmosResourceNotFoundError:
            return None
        return item if item.get("type") == "result" else None

    def save_result(self, doc):
        # upsert: なければ作り、あれば置き換える。同じ id なので、何度保存しても1件のまま
        self.container.upsert_item(doc)

    def add_duplicate(self, job_id, inquiry_id):
        # patch の incr: 読んでから書くのではなく、Cosmos DB の側で「1増やす」。同時に起きても数え漏れない
        self.container.patch_item(
            item=inquiry_id,
            partition_key=job_id,
            patch_operations=[{"op": "incr", "path": "/duplicates", "value": 1}],
        )
