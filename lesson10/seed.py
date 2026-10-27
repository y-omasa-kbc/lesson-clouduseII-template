"""架空の問い合わせを、API に登録する（コマ10）

使い方（azd up の後に、Codespace のターミナルで）
  python lesson10/seed.py                         sample_inquiries.json の8件を登録する
  python lesson10/seed.py extra_inquiries.json    extra_inquiries.json の3件を登録する（ジョブを何度か試すため）

API の URL（azd の環境の値 API_BASE_URL）を azd から読み、POST /api/inquiries で1件ずつ送る。LLM は呼ばない。
"""

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path


def api_base_url():
    """環境変数 API_BASE_URL があればそれを、なければ azd の環境から読む"""
    # 注意: eval "$(azd env get-values)" で作った変数は export されないので、Python からは見えない
    if os.environ.get("API_BASE_URL"):
        return os.environ["API_BASE_URL"]
    try:
        result = subprocess.run(["azd", "env", "get-value", "API_BASE_URL"], capture_output=True, text=True)
    except FileNotFoundError:
        return ""  # azd が見つからない
    return result.stdout.strip() if result.returncode == 0 else ""


base = api_base_url()
if not base.startswith("https://"):
    sys.exit("API の URL が分からない。azd up が終わっているか、azd env list で環境を確かめる。")

filename = sys.argv[1] if len(sys.argv) > 1 else "sample_inquiries.json"
items = json.loads(Path(__file__).with_name(Path(filename).name).read_text(encoding="utf-8"))
for item in items:
    request = urllib.request.Request(
        f"{base}/api/inquiries",
        data=json.dumps(item, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        print(response.status, item["title"])
print(f"{len(items)} 件を登録した。")
