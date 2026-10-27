"""キューの中身を見る・同じメッセージをもう一度入れる（コマ10の演習用）

使い方（azd up の後に、Codespace のターミナルで。事前に pip install azure-storage-queue azure-identity）
  python lesson10/queue_tool.py peek              キューと毒キューに入っているメッセージの数と、毒キューの中身を表示する
  python lesson10/queue_tool.py duplicate <ジョブID>  そのジョブで処理済みの1件と同じメッセージを、もう一度キューに入れる

Storage には、キーではなく、az login でサインインした自分のアカウントで接続する
（azd up のときに、自分に Storage Queue Data Contributor のロールが割り当てられている）。
LLM は呼ばない（duplicate で入れたメッセージを関数が処理するとき、冪等でなければ LLM が呼ばれる）。
"""

import base64
import json
import subprocess
import sys
import urllib.request

QUEUE = "classify-jobs"
POISON = QUEUE + "-poison"


def azd_value(name):
    result = subprocess.run(["azd", "env", "get-value", name], capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        sys.exit(f"azd の環境に {name} がない。azd up が終わっているか、azd env list で環境を確かめる。")
    return result.stdout.strip()


def queue_client(name):
    from azure.identity import AzureCliCredential
    from azure.storage.queue import QueueClient, TextBase64EncodePolicy

    account = azd_value("AZURE_STORAGE_ACCOUNT_NAME")
    return QueueClient(
        account_url=f"https://{account}.queue.core.windows.net",
        queue_name=name,
        credential=AzureCliCredential(),
        message_encode_policy=TextBase64EncodePolicy(),  # 関数（キュー トリガー）と同じ Base64 で入れる
    )


def peek():
    for name in (QUEUE, POISON):
        client = queue_client(name)
        count = client.get_queue_properties().approximate_message_count
        print(f"{name}: およそ {count} 件")
    for m in queue_client(POISON).peek_messages(max_messages=10):
        # peek は、取り出さずに先頭を「のぞく」だけ。メッセージは消えない
        print("  毒キュー:", base64.b64decode(m.content).decode("utf-8"), f"（取り出された回数 {m.dequeue_count}）")


def duplicate(job_id):
    base = azd_value("API_BASE_URL")
    with urllib.request.urlopen(f"{base}/api/jobs?id={job_id}", timeout=30) as response:
        job = json.loads(response.read().decode("utf-8"))
    finished = [r for r in job["results"] if r["status"] in ("done", "needs_review")]
    if not finished:
        sys.exit("このジョブには、処理済みの問い合わせがまだない。")
    message = json.dumps({"jobId": job_id, "inquiryId": finished[0]["inquiryId"]})
    queue_client(QUEUE).send_message(message)
    print("同じメッセージを、もう一度キューに入れた:", message)


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "peek":
        peek()
    elif len(sys.argv) == 3 and sys.argv[1] == "duplicate":
        duplicate(sys.argv[2])
    else:
        sys.exit("使い方: python lesson10/queue_tool.py peek ／ python lesson10/queue_tool.py duplicate <ジョブID>")
