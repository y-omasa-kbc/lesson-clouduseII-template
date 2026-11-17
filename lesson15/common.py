"""コマ15の道具で共通に使うもの（教員が用意したもの）"""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "api"))


def azd_value(name):
    """環境変数 name があればそれを、なければ azd の環境から読む"""
    # 注意: eval "$(azd env get-values)" で作った変数は export されないので、Python からは見えない
    if os.environ.get(name):
        return os.environ[name]
    try:
        result = subprocess.run(["azd", "env", "get-value", name], capture_output=True, text=True)
    except FileNotFoundError:
        result = None
    if result is None or result.returncode != 0:
        sys.exit(f"{name} が分からない。azd up が終わっているか、azd env list で環境を確かめる。")
    return result.stdout.strip()


def credential():
    """自分のサインイン（az login）で接続する。キーは使わない"""
    from azure.identity import AzureCliCredential

    return AzureCliCredential()


def search_clients():
    from azure.search.documents import SearchClient
    from azure.search.documents.indexes import SearchIndexClient

    endpoint = azd_value("AZURE_SEARCH_ENDPOINT")
    index_name = azd_value("AZURE_SEARCH_INDEX")
    cred = credential()
    return SearchIndexClient(endpoint, cred), SearchClient(endpoint, index_name, cred), index_name


def cosmos_container(name):
    from azure.cosmos import CosmosClient

    client = CosmosClient(azd_value("COSMOS_ENDPOINT"), credential=credential())
    return client.get_database_client("appdb").get_container_client(name)
