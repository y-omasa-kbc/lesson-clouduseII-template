#!/bin/sh
# 取り込みの関数が使う埋め込みの呼び先（アプリ設定 EMBEDDING_PROVIDER）を変える（コマ15。教員が用意したもの）
#
#   sh lesson15/set-embedding.sh broken   わざと失敗させる（存在しない呼び先。OpenRouter は呼ばない）
#   sh lesson15/set-embedding.sh mock     元に戻す（モック埋め込み）
#
# broken のあいだ、取り込みは「一時的な失敗」として扱われ、キューから何度か渡し直されたあと、毒キューに移る。
# 設定を変えるとアプリが再起動する。反映まで1分ほど待つ。
set -e
case "$1" in
  broken|mock) ;;
  *) echo "使い方: sh lesson15/set-embedding.sh broken | mock"; exit 1 ;;
esac
eval "$(azd env get-values)"
az functionapp config appsettings set \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$AZURE_FUNCTION_NAME" \
  --settings "EMBEDDING_PROVIDER=$1" \
  --output none
echo "EMBEDDING_PROVIDER を $1 にした。1分ほど待ってから試す。"
