"""問い合わせ管理アプリの API（Azure Functions / Python）

エンドポイント
  GET  /api/health      動作確認
  GET  /api/inquiries   問い合わせの一覧を返す（新しい順）
  POST /api/inquiries   問い合わせを1件登録する  {"title": "...", "body": "..."}
  GET  /api/ai/config-check  OpenRouter のキーが届いているかを確かめる（コマ6。値は返さない）
  POST /api/chat        アシスタントに質問する（コマ7。応答をストリーミングで返す）
                        {"conversationId": "...（省略すると新しい会話）", "message": "..."}
  GET  /api/conversations?id=...  会話の履歴を返す（コマ7。画面を開き直したときに使う）
  POST /api/inquiries/classify?id=...  問い合わせを AI で分類し、担当に振り分ける（コマ8）
                        コマ9から、キャッシュ・再試行・フォールバック・呼ぶ回数の上限を加えた
  POST /api/jobs/classify  未対応の問い合わせをまとめて分類するジョブを始める（コマ10。すぐ 202 を返す）
  GET  /api/jobs?id=...    ジョブの状態を返す（コマ10）
  （キュー トリガー）classify_worker・classify_poison  ジョブのメッセージを1件ずつ処理する（コマ10）

接続先などの設定は、コードに書かずに「アプリ設定」（環境変数）から読む。
注意: この時点では認証がなく、誰でも読み書きできる（コマ11で認証を加える）。

コマ7から、応答を少しずつ返す「HTTP ストリーミング」を使う。
Azure Functions（Python）では、ストリーミングを有効にすると、この関数アプリの
すべての HTTP の関数で FastAPI の Request / Response の型を使う必要がある。
"""

import asyncio
import datetime
import hashlib
import json
import logging
import os
import uuid

import azure.functions as func
from azure.cosmos import CosmosClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azurefunctions.extensions.http.fastapi import JSONResponse, Request, StreamingResponse

import classification
import jobs
import llm
import llm_cache
import resilience
from history import trim_history

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

MAX_TITLE = 100
MAX_BODY = 2000
MAX_MESSAGE = 1000  # アシスタントへの質問1件の長さの上限（文字）

_database = None


def get_database():
    """Cosmos DB のデータベースに接続する（最初の1回だけ接続し、以降は使い回す）"""
    global _database
    if _database is None:
        client = CosmosClient(
            os.environ["COSMOS_ENDPOINT"],
            credential=DefaultAzureCredential(),  # キーではなく、マネージドIDで接続する（コマ6）
        )
        _database = client.get_database_client(os.environ["COSMOS_DATABASE"])
    return _database


def get_inquiries():
    return get_database().get_container_client(os.environ["COSMOS_CONTAINER"])


def get_conversations():
    return get_database().get_container_client(os.environ["COSMOS_CONVERSATIONS_CONTAINER"])


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@app.route(route="health", methods=["GET"])
async def health(req: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "llmProvider": llm.provider_name()})


@app.route(route="inquiries", methods=["GET"])
async def list_inquiries(req: Request) -> JSONResponse:
    query = (
        "SELECT c.id, c.title, c.body, c.status, c.createdAt, "
        "c.classification, c.assignee, c.classifyAttempts "
        "FROM c ORDER BY c.createdAt DESC"
    )
    items = list(get_inquiries().query_items(query=query, enable_cross_partition_query=True))
    return JSONResponse(items)


@app.route(route="inquiries", methods=["POST"])
async def create_inquiry(req: Request) -> JSONResponse:
    try:
        data = await req.json()
    except ValueError:
        return JSONResponse({"error": "JSON の形式が正しくない"}, status_code=400)

    title = str(data.get("title", "")).strip()
    body = str(data.get("body", "")).strip()

    # 入力の検証（サーバー側で必ず行う）
    if not title or not body:
        return JSONResponse({"error": "件名と本文は必須である"}, status_code=400)
    if len(title) > MAX_TITLE or len(body) > MAX_BODY:
        return JSONResponse({"error": "件名または本文が長すぎる"}, status_code=400)

    item = {
        "id": str(uuid.uuid4()),
        "title": title,
        "body": body,
        "status": "未対応",
        "createdAt": now_iso(),
    }
    get_inquiries().create_item(item)
    return JSONResponse(item, status_code=201)


@app.route(route="ai/config-check", methods=["GET"])
async def ai_config_check(req: Request) -> JSONResponse:
    """OpenRouter のキー（OPENROUTER_API_KEY）がアプリに届いているかを確かめる（コマ6）

    キーの値そのものは絶対に返さない。値から計算した「指紋」（SHA-256 の先頭8文字）を返す。
    """
    value = os.environ.get("OPENROUTER_API_KEY", "")
    if not value:
        status = "missing"
    elif value.startswith("@Microsoft.KeyVault("):
        status = "unresolved"
    elif value.startswith("sk-or-"):
        status = "ok"
    else:
        status = "unexpected-format"

    result = {"status": status}
    if status == "ok":
        result["fingerprint"] = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return JSONResponse(result)


# =====================================================================
# アシスタント（コマ7）
# =====================================================================
def sse(event):
    """イベント1つを、Server-Sent Events（SSE）の形式の文字列にする"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def sse_response(events, status_code=200):
    return StreamingResponse(
        events,
        status_code=status_code,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


def parse_conversation_id(value):
    """会話の ID を確かめる（UUID の形でなければ None）"""
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return None


def load_history(conversation_id, limit):
    """この会話の履歴のうち、新しいほうから limit 件を、古い順に並べて返す"""
    query = (
        "SELECT TOP @limit c.role, c.content, c.createdAt FROM c "
        "WHERE c.conversationId = @id ORDER BY c.createdAt DESC"
    )
    items = list(
        get_conversations().query_items(
            query=query,
            parameters=[
                {"name": "@limit", "value": limit},
                {"name": "@id", "value": conversation_id},
            ],
            partition_key=conversation_id,  # 1つの会話は1つのパーティションに入っている
        )
    )
    items.reverse()
    return [{"role": i["role"], "content": i["content"]} for i in items]


def save_message(conversation_id, role, content, created_at):
    """メッセージ1件を Cosmos DB に保存する（1件＝1ドキュメント）"""
    get_conversations().create_item(
        {
            "id": str(uuid.uuid4()),
            "conversationId": conversation_id,
            "role": role,
            "content": content,
            "createdAt": created_at,
        }
    )


@app.route(route="conversations", methods=["GET"])
async def get_conversation(req: Request) -> JSONResponse:
    conversation_id = parse_conversation_id(req.query_params.get("id", ""))
    if conversation_id is None:
        return JSONResponse({"error": "会話の ID が正しくない"}, status_code=400)
    return JSONResponse(load_history(conversation_id, 100))


@app.route(route="chat", methods=["POST"])
async def chat(req: Request) -> StreamingResponse:
    # 入力の検証。エラーも、ストリームの途中のエラーと同じ「イベント」の形で返す
    try:
        data = await req.json()
    except ValueError:
        return sse_response(iter([sse({"type": "error", "message": "JSON の形式が正しくない"})]), 400)

    message = str(data.get("message", "")).strip()
    if not message or len(message) > MAX_MESSAGE:
        return sse_response(iter([sse({"type": "error", "message": "質問が空か、長すぎる"})]), 400)

    if data.get("conversationId"):
        conversation_id = parse_conversation_id(data["conversationId"])
        if conversation_id is None:
            return sse_response(iter([sse({"type": "error", "message": "会話の ID が正しくない"})]), 400)
    else:
        conversation_id = str(uuid.uuid4())  # 新しい会話

    config = llm.load_config()
    budget = int(os.environ.get("HISTORY_TOKEN_BUDGET", config["history_token_budget"]))
    user_created_at = now_iso()

    history = load_history(conversation_id, config["max_history_messages"])
    history.append({"role": "user", "content": message})
    messages = [{"role": "system", "content": config["system_prompt"]}] + trim_history(history, budget)

    def generate():
        # 最初のイベント: 会話の ID と、モデルに送ったメッセージの件数を知らせる
        yield sse({"type": "start", "conversationId": conversation_id, "sentMessages": len(messages)})

        try:
            parts = []
            for piece in llm.stream_chat(messages, config["max_output_tokens"]):
                parts.append(piece)
                yield sse({"type": "delta", "text": piece})
            reply = "".join(parts)
        except llm.LLMError as e:
            # 途中で失敗した。HTTP の状態（200）は送ってしまっているので、エラーもイベントで知らせる
            yield sse({"type": "error", "message": str(e)})
            return

        save_message(conversation_id, "user", message, user_created_at)
        save_message(conversation_id, "assistant", reply, now_iso())

        yield sse({"type": "end"})

    return sse_response(generate())


# =====================================================================
# 問い合わせの分類（コマ8・9）
# =====================================================================
def get_llm_cache():
    return llm_cache.CosmosCache(get_database().get_container_client(os.environ["COSMOS_CACHE_CONTAINER"]))


def classify_with_llm(item, config, mode):
    """問い合わせ1件を分類する（コマ9: キャッシュ → 再試行・フォールバック → 形式の検証と再試行）

    戻り値: (分類の結果, 情報)。情報には、キャッシュを使ったか・LLM を呼んだ回数・呼んだ記録が入る。
    モデルを呼べなかったときは llm.LLMError が出る。
    """
    models = llm.model_candidates(config)
    if not models:
        raise llm.LLMError("呼んでよいモデルが設定されていない（教員の指示を待つ）")
    schema = classification.json_schema()

    # 1. キャッシュ: 同じ入力を前に分類していれば、LLM を呼ばずにその結果を使う
    cache = get_llm_cache()
    # モデルの ID の前に呼び先（mock / openrouter）を付ける。モックの答えを、本物の答えとして使わないように
    model_id = f"{llm.provider_name()}/{models[0]}"
    key = llm_cache.cache_key("classify", model_id, classification.build_messages(item, mode), schema, mode)
    cached = cache.get(key) if key else None  # キーを作れないときは、キャッシュを使わない
    if cached is not None:
        result = classification.ClassifyResult(
            classification.InquiryClassification.model_validate(cached), attempts=0
        )
        return result, {"cached": True, "llmCalls": 0, "callLog": ["キャッシュの結果を使った（LLM は呼んでいない）"]}

    # 2. LLM を呼ぶ。1件の分類で呼ぶ回数の合計に上限を付ける（形式の再試行と通信の再試行が掛け算で増えないように）
    budget = resilience.CallBudget(config["max_llm_calls_per_request"])
    provider = llm.get_provider()
    call_log = []

    def call_llm(messages):
        outcome = resilience.call_with_resilience(
            lambda model: provider.complete_json(
                model, messages, schema, mode, config["classify_max_output_tokens"]
            ),
            models,
            budget,
            max_retries_per_model=config["max_retries_per_model"],
            base_delay=config["retry_base_delay_seconds"],
            max_delay=config["retry_max_delay_seconds"],
            max_wait=config["retry_max_wait_seconds"],
            log=call_log,  # 形式の再試行をまたいで、1つの記録に書き足していく
        )
        return outcome.text

    try:
        result = classification.classify(item, call_llm, config["max_classify_attempts"], mode)
    except llm.LLMError as e:
        e.call_log = call_log  # どこまで試したかを、エラーの応答にも載せる
        raise

    # 3. 人に回した結果（fallback）はキャッシュしない。次に分類し直したときに、もう一度 AI に聞けるように
    if key and not result.fallback:
        cache.set(key, result.classification.model_dump(), config["cache_ttl_seconds"])
    return result, {"cached": False, "llmCalls": budget.used, "callLog": call_log}


def classify_and_save(item, config, mode):
    """問い合わせ1件を分類し、担当を決めて、Cosmos DB に保存する（画面からの分類と、ジョブの両方で使う）"""
    result, info = classify_with_llm(item, config, mode)
    decision = classification.route(result)
    item["classification"] = result.classification.model_dump()
    item["assignee"] = decision["assignee"]
    item["status"] = decision["status"]
    item["classifyAttempts"] = result.attempts
    item["classifiedAt"] = now_iso()
    get_inquiries().replace_item(item=item["id"], body=item)
    return result, decision, info


def structured_output_mode(config):
    # アプリ設定 STRUCTURED_OUTPUT_MODE があれば、設定ファイルより優先する（モデルを変えたときの切り替え用）
    return os.environ.get("STRUCTURED_OUTPUT_MODE", config["structured_output_mode"])


@app.route(route="inquiries/classify", methods=["POST"])
async def classify_inquiry(req: Request) -> JSONResponse:
    inquiry_id = parse_conversation_id(req.query_params.get("id", ""))  # 問い合わせの ID も UUID の形
    if inquiry_id is None:
        return JSONResponse({"error": "問い合わせの ID が正しくない"}, status_code=400)
    try:
        item = get_inquiries().read_item(item=inquiry_id, partition_key=inquiry_id)
    except CosmosResourceNotFoundError:
        return JSONResponse({"error": "問い合わせが見つからない"}, status_code=404)

    config = llm.load_config()
    mode = structured_output_mode(config)

    try:
        # モデルの応答や再試行の待ち時間の間、ほかのリクエストを止めないように、別のスレッドで動かす
        result, decision, info = await asyncio.to_thread(classify_and_save, item, config, mode)
    except llm.LLMError as e:
        # 再試行・フォールバックをしても、モデルから応答を得られなかった。問い合わせは分類しないままにする
        return JSONResponse({"error": str(e), "callLog": getattr(e, "call_log", [])}, status_code=503)

    return JSONResponse(
        {
            "id": item["id"],
            "classification": item["classification"],
            "assignee": decision["assignee"],
            "status": decision["status"],
            "attempts": result.attempts,
            "errors": result.errors,
            "fallback": result.fallback,
            **info,
        }
    )


# =====================================================================
# 一括分類の非同期ジョブ（コマ10）
# =====================================================================
def get_job_store():
    return jobs.CosmosJobStore(get_database().get_container_client(os.environ["COSMOS_JOBS_CONTAINER"]))


def get_queue():
    """ジョブのメッセージを入れるキュー（Storage Queue）。キーではなく、マネージドIDで接続する"""
    from azure.storage.queue import QueueClient, TextBase64EncodePolicy

    return QueueClient(
        account_url=os.environ["AzureWebJobsStorage__queueServiceUri"],
        queue_name=jobs.QUEUE_NAME,
        credential=DefaultAzureCredential(),
        # キュー トリガーは、既定ではメッセージが Base64 で符号化されていると期待する
        message_encode_policy=TextBase64EncodePolicy(),
    )


@app.route(route="jobs/classify", methods=["POST"])
async def start_classify_job(req: Request) -> JSONResponse:
    """未対応の問い合わせを、まとめて分類するジョブを始める。分類の完了は待たずに、すぐ返す"""
    config = llm.load_config()
    query = "SELECT TOP @limit c.id FROM c WHERE c.status = '未対応' ORDER BY c.createdAt"
    targets = list(
        get_inquiries().query_items(
            query=query,
            parameters=[{"name": "@limit", "value": config["max_job_items"]}],
            enable_cross_partition_query=True,
        )
    )
    if not targets:
        return JSONResponse({"error": "未対応の問い合わせがない"}, status_code=400)

    header, messages = jobs.new_job([t["id"] for t in targets], now_iso())
    get_job_store().create_job(header)  # 先にジョブを記録してから、メッセージを入れる
    queue = get_queue()
    for m in messages:
        await asyncio.to_thread(queue.send_message, m)

    # 202 Accepted: 「受け付けた。処理はこれから」。状態を問い合わせる URL も知らせる
    return JSONResponse(
        {"jobId": header["jobId"], "total": header["total"], "statusUrl": f"/api/jobs?id={header['jobId']}"},
        status_code=202,
    )


@app.route(route="jobs", methods=["GET"])
async def get_job(req: Request) -> JSONResponse:
    job_id = parse_conversation_id(req.query_params.get("id", ""))  # ジョブの ID も UUID の形
    if job_id is None:
        return JSONResponse({"error": "ジョブの ID が正しくない"}, status_code=400)
    store = get_job_store()
    header = store.get_header(job_id)
    if header is None:
        return JSONResponse({"error": "ジョブが見つからない"}, status_code=404)
    return JSONResponse(jobs.summarize(header, store.list_results(job_id)))


def classify_one(inquiry_id):
    """ジョブの中で、問い合わせ1件を分類する。(status, detail) を返す"""
    try:
        item = get_inquiries().read_item(item=inquiry_id, partition_key=inquiry_id)
    except CosmosResourceNotFoundError as e:
        raise jobs.InquiryGone("問い合わせが見つからない（削除された）") from e
    config = llm.load_config()
    result, decision, info = classify_and_save(item, config, structured_output_mode(config))
    status = jobs.DONE if decision["status"] == "振り分け済み" else jobs.NEEDS_REVIEW
    return status, f"{decision['assignee']}（LLM を呼んだ回数 {info['llmCalls']} 回）"


# キュー トリガー: メッセージが入ると、Azure Functions が1件ずつこの関数を呼ぶ
#   connection="AzureWebJobsStorage": 関数アプリ自身の Storage に、マネージドIDで接続する
#   （アプリ設定 AzureWebJobsStorage__queueServiceUri などで、接続先を指定してある）
@app.queue_trigger(arg_name="msg", queue_name=jobs.QUEUE_NAME, connection="AzureWebJobsStorage")
def classify_worker(msg: func.QueueMessage) -> None:
    body = msg.get_body().decode("utf-8")
    logging.info("classify_worker: メッセージを受け取った（%d 回目）", msg.dequeue_count)
    outcome = jobs.process_message(body, get_job_store(), classify_one, now_iso())
    logging.info("classify_worker: %s", outcome)


# 毒キュー: 上限の回数まで失敗したメッセージが、ここに移される
@app.queue_trigger(arg_name="msg", queue_name=jobs.POISON_QUEUE_NAME, connection="AzureWebJobsStorage")
def classify_poison(msg: func.QueueMessage) -> None:
    body = msg.get_body().decode("utf-8")
    outcome = jobs.record_poison(body, get_job_store(), now_iso())
    logging.warning("classify_poison: 毒キューのメッセージを記録した（%s）", outcome)
