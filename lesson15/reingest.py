"""文書を取り込み直す（コマ15。教員が用意したもの）

使い方: python lesson15/reingest.py <Blob の名前>
Event Grid が入れるのと同じ形の「作られた」イベントを、キュー ingest-events に入れる。
取り込みは冪等なので、何度やり直しても、インデックスは Blob と同じ状態になる。
失敗した取り込みを、原因を直した後にやり直すときに使う。
"""

import json
import sys

from azure.storage.queue import QueueClient, TextBase64EncodePolicy
from common import azd_value, credential

import ingest

if len(sys.argv) != 2:
    sys.exit("使い方: python lesson15/reingest.py <Blob の名前>")
name = sys.argv[1]
account = azd_value("AZURE_STORAGE_ACCOUNT_NAME")
queue = QueueClient(
    account_url=f"https://{account}.queue.core.windows.net",
    queue_name=ingest.QUEUE_NAME,
    credential=credential(),
    message_encode_policy=TextBase64EncodePolicy(),  # キュー トリガーは、既定では Base64 で符号化されたメッセージを期待する
)
queue.send_message(
    json.dumps(
        {
            "eventType": ingest.CREATED,
            "subject": f"/blobServices/default/containers/{ingest.CONTAINER}/blobs/{name}",
            "data": {"api": "reingest"},
        }
    )
)
print(f"{name} の取り込みのやり直しを、キュー {ingest.QUEUE_NAME} に入れた")
