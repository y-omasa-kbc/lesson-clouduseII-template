#!/bin/sh
# Blob のコンテナー docs に、文書を置く・消す・一覧を見る（コマ15。教員が用意したもの）
#
#   sh lesson15/blob.sh put <ファイル> [Blob の名前]   置く（同じ名前があれば上書きする）
#   sh lesson15/blob.sh delete <Blob の名前>          消す
#   sh lesson15/blob.sh list                          一覧を見る
#
# --auth-mode login: キーではなく、自分のサインイン（az login）とロールの割り当て（Storage Blob Data Contributor）で操作する
set -e
ACCOUNT=$(azd env get-value AZURE_STORAGE_ACCOUNT_NAME)
case "$1" in
  put)
    NAME=${3:-$(basename "$2")}
    az storage blob upload --account-name "$ACCOUNT" --container-name docs --auth-mode login \
      --file "$2" --name "$NAME" --overwrite --output none
    echo "docs/$NAME に置いた"
    ;;
  delete)
    az storage blob delete --account-name "$ACCOUNT" --container-name docs --auth-mode login \
      --name "$2" --output none
    echo "docs/$2 を消した"
    ;;
  list)
    az storage blob list --account-name "$ACCOUNT" --container-name docs --auth-mode login \
      --query "[].{name:name, size:properties.contentLength}" --output table
    ;;
  *)
    echo "使い方: sh lesson15/blob.sh put <ファイル> [名前] | delete <名前> | list"
    exit 1
    ;;
esac
