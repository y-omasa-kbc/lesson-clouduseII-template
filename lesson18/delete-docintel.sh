#!/bin/sh
# コマ18: 作った Document Intelligence を、リソースグループごと消す（教員が用意したもの）
#
# 使い方: sh lesson18/delete-docintel.sh
#
# Azure AI のサービスのリソースは、消してもしばらく「論理削除」の状態で残る。残ったままだと、
# F0 をもう一度作れないことがあるので、最後に完全に消す（purge）。
set -eu

RG="rg-cloud2-l18-${GITHUB_USER:-me}"
if [ "$(az group exists --name "$RG")" != "true" ]; then
  echo "$RG はない（すでに消えている）"
else
  NAME="$(az cognitiveservices account list --resource-group "$RG" --query "[0].name" --output tsv)"
  LOCATION="$(az group show --name "$RG" --query location --output tsv)"
  echo "$RG を消す（数分かかる）"
  az group delete --name "$RG" --yes
  if [ -n "$NAME" ]; then
    echo "$NAME を完全に消す（purge）"
    az cognitiveservices account purge --name "$NAME" --resource-group "$RG" --location "$LOCATION"
  fi
fi
echo "残っている論理削除のリソース（空なら OK）:"
az cognitiveservices account list-deleted --query "[].name" --output tsv
