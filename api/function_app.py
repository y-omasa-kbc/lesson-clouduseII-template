"""問い合わせ管理アプリの API（Azure Functions / Python）

エンドポイント
  GET  /api/health      動作確認（サインイン不要）
  GET  /api/dev/users   テスト利用者の一覧（コマ11。擬似認証。DEV_LOGIN_ENABLED=true のときだけ）
  POST /api/dev/login   テスト利用者としてトークンを受け取る（コマ11。擬似認証）  {"userId": "..."}
  GET  /api/me          API が「あなたは誰か」をどう認識したかを返す（コマ11）
  GET  /api/inquiries   問い合わせの一覧を返す（新しい順。担当者は全件、それ以外は自分が登録したものだけ）
  POST /api/inquiries   問い合わせを1件登録する  {"title": "...", "body": "..."}
  GET  /api/ai/config-check  OpenRouter のキーが届いているかを確かめる（コマ6。値は返さない）
  POST /api/chat        アシスタントに質問する（コマ7。応答をストリーミングで返す）
                        {"conversationId": "...（省略すると新しい会話）", "message": "..."}
  GET  /api/conversations?id=...  会話の履歴を返す（コマ7。画面を開き直したときに使う）
  GET  /api/conversations/list?user=...  会話の一覧を返す（コマ11）
  POST /api/assist      ツールを使うアシスタント（コマ12。問い合わせの検索・ステータスの変更）  {"message": "..."}
  POST /api/mcp         MCP サーバー（コマ13。JSON-RPC のメッセージを受け取る）
  POST /api/search      文書を検索する（コマ14）  {"query": "...", "mode": "keyword|vector|hybrid"}
  POST /api/ask         社内FAQに質問する（コマ16。利用者が見てよい文書だけで答え、引用を付ける）  {"question": "..."}
  POST /api/inquiries/classify?id=...  問い合わせを AI で分類し、担当に振り分ける（コマ8）
                        コマ9から、キャッシュ・再試行・フォールバック・呼ぶ回数の上限を加えた
  POST /api/jobs/classify  未対応の問い合わせをまとめて分類するジョブを始める（コマ10。すぐ 202 を返す）
  GET  /api/jobs?id=...    ジョブの状態を返す（コマ10）
  （キュー トリガー）classify_worker・classify_poison  ジョブのメッセージを1件ずつ処理する（コマ10）
  （キュー トリガー）ingest_worker・ingest_poison  Blob のイベントを受けて、文書をインデックスに反映する（コマ15）

接続先などの設定は、コードに書かずに「アプリ設定」（環境変数）から読む。

コマ11から、health と dev/* 以外のすべての API で、利用者トークン（Authorization: Bearer …）を確かめる（auth.py）。
分類・一括分類のジョブは、担当者（staff）のロールを持つ利用者だけに許す（authz.py）。

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
import time
import uuid
from pathlib import Path

import azure.functions as func
from azure.cosmos import CosmosClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azurefunctions.extensions.http.fastapi import JSONResponse, Request, Response, StreamingResponse

import auth
import authz
import classification
import conversations
import doc_search
import embedding
import ingest
import jobs
import llm
import llm_cache
import mcp_server
import rag
import resilience
import tools
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


# =====================================================================
# 認証（コマ11）
# =====================================================================
def authenticate(req):
    """要求のトークンを確かめ、利用者（auth.Principal）を返す。確かめられなければ authz.AuthError を出す"""
    key = auth.signing_key()
    if key is None:
        raise authz.NotReady("トークンの署名の鍵が設定されていない（そのコマのフォルダの enable-dev-login.sh を実行する）")
    return authz.require_user(auth.principal_from_headers(req.headers, key, time.time()))


def auth_error(e):
    return JSONResponse({"error": e.message}, status_code=e.status)


def dev_login_enabled():
    return os.environ.get("DEV_LOGIN_ENABLED", "false").lower() == "true"


def load_dev_users():
    path = Path(__file__).with_name("dev_users.json")
    return json.loads(path.read_text(encoding="utf-8"))["users"]


@app.route(route="health", methods=["GET"])
async def health(req: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "llmProvider": llm.provider_name()})


@app.route(route="dev/users", methods=["GET"])
async def dev_users(req: Request) -> JSONResponse:
    """テスト利用者の一覧（擬似認証）。本番では、この API 自体を置かない"""
    if not dev_login_enabled():
        return JSONResponse({"error": "見つからない"}, status_code=404)
    return JSONResponse([{"id": u["id"], "name": u["name"], "roles": u["roles"]} for u in load_dev_users()])


@app.route(route="dev/login", methods=["POST"])
async def dev_login(req: Request) -> JSONResponse:
    """テスト利用者としてトークンを発行する（擬似認証）

    パスワードも本人確認もなく、誰にでもなれる。だから「演習用」であり、本番では使ってはいけない。
    本番では、ここが ID プロバイダー（GitHub・Microsoft Entra ID など）でのサインインに置き換わる。
    """
    if not dev_login_enabled():
        return JSONResponse({"error": "見つからない"}, status_code=404)
    key = auth.signing_key()
    if key is None:
        return auth_error(authz.NotReady("トークンの署名の鍵が設定されていない（そのコマのフォルダの enable-dev-login.sh を実行する）"))
    try:
        data = await req.json()
    except ValueError:
        return JSONResponse({"error": "JSON の形式が正しくない"}, status_code=400)
    user = next((u for u in load_dev_users() if u["id"] == data.get("userId")), None)
    if user is None:
        return JSONResponse({"error": "そのテスト利用者はいない"}, status_code=400)
    token = auth.issue_token(user["id"], user["name"], user["roles"], key, time.time())
    return JSONResponse({"token": token, "userId": user["id"], "name": user["name"], "roles": user["roles"]})


@app.route(route="me", methods=["GET"])
async def me(req: Request) -> JSONResponse:
    """API が、この要求を「誰から」と認識したかを返す（トークンで確かめた結果だけを返す）"""
    try:
        principal = authenticate(req)
    except authz.AuthError as e:
        return auth_error(e)
    return JSONResponse({"userId": principal.user_id, "name": principal.name, "roles": list(principal.roles)})


@app.route(route="inquiries", methods=["GET"])
async def list_inquiries(req: Request) -> JSONResponse:
    try:
        principal = authenticate(req)
    except authz.AuthError as e:
        return auth_error(e)

    fields = (
        "SELECT c.id, c.title, c.body, c.status, c.createdAt, c.createdBy, "
        "c.classification, c.assignee, c.classifyAttempts FROM c "
    )
    if authz.STAFF in principal.roles:
        # 担当者は、すべての問い合わせを見る
        items = get_inquiries().query_items(
            query=fields + "ORDER BY c.createdAt DESC", enable_cross_partition_query=True
        )
    else:
        # それ以外の利用者は、自分が登録した問い合わせだけを見る（条件は、トークンで確かめた本人の ID）
        items = get_inquiries().query_items(
            query=fields + "WHERE c.createdBy = @user ORDER BY c.createdAt DESC",
            parameters=[{"name": "@user", "value": principal.user_id}],
            enable_cross_partition_query=True,
        )
    return JSONResponse(list(items))


@app.route(route="inquiries", methods=["POST"])
async def create_inquiry(req: Request) -> JSONResponse:
    try:
        principal = authenticate(req)
    except authz.AuthError as e:
        return auth_error(e)

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
        "createdBy": principal.user_id,  # 登録した人。画面が送ってきた値ではなく、トークンで確かめた本人
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


def get_conversation_store():
    """会話の履歴の保存先（コマ11で conversations.py に移した。メッセージに所有者 userId を記録する）"""
    return conversations.CosmosConversationStore(get_conversations())


@app.route(route="conversations", methods=["GET"])
async def get_conversation(req: Request) -> JSONResponse:
    try:
        principal = authenticate(req)
        conversation_id = parse_conversation_id(req.query_params.get("id", ""))
        if conversation_id is None:
            return JSONResponse({"error": "会話の ID が正しくない"}, status_code=400)
        return JSONResponse(conversations.read_conversation(get_conversation_store(), principal, conversation_id, 100))
    except authz.AuthError as e:
        return auth_error(e)


@app.route(route="conversations/list", methods=["GET"])
async def list_conversations(req: Request) -> JSONResponse:
    try:
        principal = authenticate(req)
        requested_user = req.query_params.get("user")  # 画面が送ってくる「誰の一覧か」
        return JSONResponse(conversations.list_conversations(get_conversation_store(), principal, requested_user))
    except authz.AuthError as e:
        return auth_error(e)


@app.route(route="chat", methods=["POST"])
async def chat(req: Request) -> StreamingResponse:
    try:
        principal = authenticate(req)
    except authz.AuthError as e:
        return sse_response(iter([sse({"type": "error", "message": e.message})]), e.status)

    # 入力の検証。エラーも、ストリームの途中のエラーと同じ「イベント」の形で返す
    try:
        data = await req.json()
    except ValueError:
        return sse_response(iter([sse({"type": "error", "message": "JSON の形式が正しくない"})]), 400)

    message = str(data.get("message", "")).strip()
    if not message or len(message) > MAX_MESSAGE:
        return sse_response(iter([sse({"type": "error", "message": "質問が空か、長すぎる"})]), 400)

    store = get_conversation_store()
    if data.get("conversationId"):
        conversation_id = parse_conversation_id(data["conversationId"])
        if conversation_id is None:
            return sse_response(iter([sse({"type": "error", "message": "会話の ID が正しくない"})]), 400)
        try:
            conversations.check_can_continue(store, principal, conversation_id)  # 他人の会話に書き足させない
        except authz.AuthError as e:
            return sse_response(iter([sse({"type": "error", "message": e.message})]), e.status)
    else:
        conversation_id = str(uuid.uuid4())  # 新しい会話

    config = llm.load_config()
    budget = int(os.environ.get("HISTORY_TOKEN_BUDGET", config["history_token_budget"]))
    user_created_at = now_iso()

    history = store.load(conversation_id, config["max_history_messages"])
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

        store.save_message(conversation_id, principal.user_id, "user", message, user_created_at)
        store.save_message(conversation_id, principal.user_id, "assistant", reply, now_iso())

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
    try:
        authz.require_role(authenticate(req), authz.STAFF)  # 担当者だけ（LLM を呼ぶ＝共有の利用上限を使う）
    except authz.AuthError as e:
        return auth_error(e)

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
# ツールを使うアシスタント（コマ12）
# =====================================================================
def run_assist(message, principal, config):
    """ツールの実行ループを動かす。モデルを呼ぶたびに、コマ9の再試行・フォールバックと、呼ぶ回数の上限を使う"""
    models = llm.tool_model_candidates(config)
    if not models:
        raise llm.LLMError("ツール呼び出しに使うモデルが設定されていない（教員の指示を待つ）")
    provider = llm.get_provider()
    budget = resilience.CallBudget(config["max_tool_llm_calls"])  # ループ全体で、LLM を呼ぶ回数の上限
    definitions = tools.tool_definitions(tools.TOOLS)
    repo = tools.CosmosInquiryRepo(get_inquiries())
    call_log = []
    steps = []

    def call_model(messages):
        outcome = resilience.call_with_resilience(
            lambda model: provider.complete_tools(model, messages, definitions, config["tool_max_output_tokens"]),
            models,
            budget,
            max_retries_per_model=config["max_retries_per_model"],
            base_delay=config["retry_base_delay_seconds"],
            max_delay=config["retry_max_delay_seconds"],
            max_wait=config["retry_max_wait_seconds"],
            log=call_log,
        )
        return outcome.text  # complete_tools の戻り値（答えの文か、ツールの要求）

    def execute(name, arguments):
        # ツールは、モデルではなく「依頼した利用者」の権限で実行する（principal を渡す）
        return tools.execute_tool(name, arguments, principal, repo)

    messages = [
        {"role": "system", "content": config["tool_system_prompt"]},
        {"role": "user", "content": message},
    ]
    try:
        reply, steps = tools.run_tool_loop(messages, call_model, execute, config["max_tool_steps"], steps)
    except llm.LLMError as e:
        e.call_log = call_log
        e.steps = steps  # モデルを呼べなくなるまでに実行したツール（ツールの実行は取り消されない）
        raise
    return {"reply": reply, "steps": steps, "llmCalls": budget.used, "callLog": call_log}


@app.route(route="assist", methods=["POST"])
async def assist(req: Request) -> JSONResponse:
    try:
        principal = authenticate(req)
    except authz.AuthError as e:
        return auth_error(e)
    try:
        data = await req.json()
    except ValueError:
        return JSONResponse({"error": "JSON の形式が正しくない"}, status_code=400)
    message = str(data.get("message", "")).strip()
    if not message or len(message) > MAX_MESSAGE:
        return JSONResponse({"error": "依頼が空か、長すぎる"}, status_code=400)

    config = llm.load_config()
    try:
        result = await asyncio.to_thread(run_assist, message, principal, config)
    except llm.LLMError as e:
        return JSONResponse(
            {"error": str(e), "callLog": getattr(e, "call_log", []), "steps": getattr(e, "steps", [])}, status_code=503
        )
    return JSONResponse(result)


# =====================================================================
# MCP サーバー（コマ13）
# =====================================================================
@app.route(route="mcp", methods=["POST"])
async def mcp(req: Request) -> JSONResponse:
    # ブラウザから来た要求（Origin ヘッダーがある）は断る。DNS リバインディング攻撃を防ぐため（MCP の仕様の決まり）
    if req.headers.get("origin"):
        return JSONResponse({"error": "この Origin からの要求は許可していない"}, status_code=403)
    try:
        principal = authenticate(req)  # ツールは、トークンの利用者の権限で実行する
    except authz.AuthError as e:
        return auth_error(e)
    try:
        message = await req.json()
    except ValueError:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON の形式が正しくない"}},
            status_code=400,
        )
    try:
        mcp_server.check_headers(message, req.headers)
    except mcp_server.RpcError as e:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": e.code, "message": e.message}},
            status_code=400,
        )
    response = mcp_server.handle(message, principal, tools.CosmosInquiryRepo(get_inquiries()))
    if response is None:
        return Response(status_code=202)  # 通知を受け付けた（本文なし）
    return JSONResponse(response)


# =====================================================================
# 文書の検索（コマ14）
# =====================================================================
def get_search_client():
    """AI Search に接続する。キーではなく、マネージドIDで接続する（Search Index Data Reader のロール）"""
    from azure.search.documents import SearchClient

    return SearchClient(
        os.environ["AZURE_SEARCH_ENDPOINT"], os.environ["AZURE_SEARCH_INDEX"], DefaultAzureCredential()
    )


@app.route(route="search", methods=["POST"])
async def search_documents(req: Request) -> JSONResponse:
    try:
        authenticate(req)
    except authz.AuthError as e:
        return auth_error(e)
    try:
        data = await req.json()
    except ValueError:
        return JSONResponse({"error": "JSON の形式が正しくない"}, status_code=400)
    query = str(data.get("query", "")).strip()
    mode = data.get("mode", "hybrid")
    if not query or len(query) > MAX_MESSAGE or mode not in doc_search.MODES:
        return JSONResponse({"error": "検索の言葉か、検索の種類が正しくない"}, status_code=400)

    try:
        vector = None if mode == "keyword" else await asyncio.to_thread(embedding.embed_query, query)
        hits = await asyncio.to_thread(doc_search.run_search, get_search_client(), mode, query, vector, 3)
    except embedding.EmbeddingError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception:  # noqa: BLE001
        logging.exception("検索に失敗した")
        return JSONResponse({"error": "検索に失敗した（ロールが行き渡っていない可能性がある）"}, status_code=503)
    return JSONResponse({"mode": mode, "hits": hits})


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
    try:
        authz.require_role(authenticate(req), authz.STAFF)
    except authz.AuthError as e:
        return auth_error(e)

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
    try:
        authz.require_role(authenticate(req), authz.STAFF)
    except authz.AuthError as e:
        return auth_error(e)

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


# =====================================================================
# 文書の取り込みパイプライン（コマ15）
#   Blob のコンテナー docs に文書を置く・差し替える・消す
#   → Event Grid がイベントをキュー ingest-events に入れる（infra/resources.bicep の blobEvents）
#   → ingest_worker が1件ずつ処理する
# =====================================================================
def get_ingest_parts():
    """取り込みに使う部品。どれも、キーではなくマネージドIDで接続する"""
    from azure.search.documents import SearchClient
    from azure.storage.blob import BlobServiceClient

    credential = DefaultAzureCredential()
    blobs = BlobServiceClient(os.environ["AzureWebJobsStorage__blobServiceUri"], credential=credential)
    search = SearchClient(os.environ["AZURE_SEARCH_ENDPOINT"], os.environ["AZURE_SEARCH_INDEX"], credential)
    database = get_database()
    return (
        ingest.BlobDocuments(blobs.get_container_client(ingest.CONTAINER)),
        ingest.SearchIndexStore(search),
        ingest.CosmosEmbeddingCache(database.get_container_client(os.environ["COSMOS_EMBCACHE_CONTAINER"])),
    )


def record_ingest(result, attempt):
    """取り込みの結果を、記録のコンテナー（ingestlog。7日で自動で消える）に残す"""
    entry = {**result, "id": str(uuid.uuid4()), "attempt": attempt, "at": now_iso()}
    entry.setdefault("source", "(不明)")
    get_database().get_container_client(os.environ["COSMOS_INGESTLOG_CONTAINER"]).create_item(entry)


@app.queue_trigger(arg_name="msg", queue_name=ingest.QUEUE_NAME, connection="AzureWebJobsStorage")
def ingest_worker(msg: func.QueueMessage) -> None:
    body = msg.get_body().decode("utf-8")
    logging.info("ingest_worker: イベントを受け取った（%d 回目）", msg.dequeue_count)
    blobs, index, cache = get_ingest_parts()
    try:
        result = ingest.process_event(
            body, blobs, index, cache, embedding.embed_documents, embedding.document_model_name()
        )
    except ingest.IngestError as e:
        # やり直しても直らない失敗: 例外を出さずに「失敗」と記録して終える（キューから消える）
        parsed = ingest.parse_event(body)
        source = ingest.source_of(parsed[1]) if parsed else "(不明)"
        result = {"action": "failed", "source": source, "error": str(e)}
    # それ以外の例外（埋め込み・インデックス・Cosmos DB の一時的な失敗）は、そのまま外に出す
    #   → キューが少し時間をおいて、同じメッセージをもう一度渡す（host.json の maxDequeueCount 回まで）
    logging.info("ingest_worker: %s", json.dumps(result, ensure_ascii=False))
    if result["action"] != "ignored":
        record_ingest(result, msg.dequeue_count)


@app.queue_trigger(arg_name="msg", queue_name=ingest.POISON_QUEUE_NAME, connection="AzureWebJobsStorage")
def ingest_poison(msg: func.QueueMessage) -> None:
    body = msg.get_body().decode("utf-8")
    parsed = ingest.parse_event(body)
    source = ingest.source_of(parsed[1]) if parsed else "(不明)"
    record_ingest({"action": "poison", "source": source, "error": "上限の回数まで失敗した（毒キュー）"}, msg.dequeue_count)
    logging.warning("ingest_poison: %s を毒キューから記録した", source)


# =====================================================================
# アクセス制御付きの RAG（コマ16）
# =====================================================================
@app.route(route="ask", methods=["POST"])
async def ask(req: Request) -> JSONResponse:
    try:
        principal = authenticate(req)
    except authz.AuthError as e:
        return auth_error(e)
    try:
        data = await req.json()
    except ValueError:
        return JSONResponse({"error": "JSON の形式が正しくない"}, status_code=400)
    question = str(data.get("question", "")).strip()
    if not question or len(question) > MAX_MESSAGE:
        return JSONResponse({"error": "質問が空か、長すぎる"}, status_code=400)

    # 利用者のグループは、トークンで確かめた利用者から、サーバー側で決める（要求の本文の値は使わない）
    groups = rag.groups_for(principal, load_dev_users())
    config = llm.load_config()
    try:
        vector = await asyncio.to_thread(embedding.embed_query, question)
        hits = await asyncio.to_thread(
            rag.retrieve, get_search_client(), question, vector, groups, config["rag_top_k"], config["rag_search_mode"]
        )
        result = await asyncio.to_thread(
            rag.answer_question,
            question,
            hits,
            lambda messages: llm.complete_text(messages, config["rag_max_output_tokens"]),
            config["rag_system_prompt"],
        )
    except embedding.EmbeddingError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except llm.LLMError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception:  # noqa: BLE001
        logging.exception("質問への回答に失敗した")
        return JSONResponse({"error": "回答に失敗した（ロールが行き渡っていない可能性がある）"}, status_code=503)
    # searched: 検索で候補になった文書（演習で、見えてはいけない文書が混ざっていないかを確かめるために返す）
    return JSONResponse({**result, "groups": groups, "searched": [h["source"] for h in hits]})
