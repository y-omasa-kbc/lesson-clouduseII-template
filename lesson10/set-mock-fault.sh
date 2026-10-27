#!/bin/sh
# モックLLM に、わざと失敗させる（コマ8〜10）
#   使い方: sh lesson10/set-mock-fault.sh <値>
#     none             : 正しい JSON を返す（既定）
#     invalid_once     : 1回目だけ、決まりに合わない値を返す（形式の失敗。コマ8）
#     always_invalid   : 毎回、JSON ではない文を返す（形式の失敗。コマ8）
#     rate_limit_once  : 同じ要求の1回目だけ、429（Retry-After: 2秒）で失敗する（通信の失敗。コマ9）
#     primary_down     : 主のモデルだけ、毎回 503 で失敗する（通信の失敗。コマ9）
#     all_rate_limited : どのモデルも、毎回 429 で失敗する（通信の失敗。コマ9）
#   アプリ設定を変えるとアプリが再起動する。1分ほど待ってから確かめる。
set -e
case "$1" in
  none|invalid_once|always_invalid|rate_limit_once|primary_down|all_rate_limited) ;;
  *) echo "使い方: sh lesson10/set-mock-fault.sh none|invalid_once|always_invalid|rate_limit_once|primary_down|all_rate_limited" >&2; exit 1 ;;
esac
eval "$(azd env get-values)"
az functionapp config appsettings set \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$AZURE_FUNCTION_NAME" \
  --settings "MOCK_FAULT=$1" \
  --output none
echo "MOCK_FAULT を $1 にした。1分ほど待ってから確かめる。"
