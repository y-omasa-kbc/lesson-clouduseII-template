#!/bin/sh
# コマ18: Document Intelligence（Free F0）を、自分のリソースグループに作る（教員が用意したもの）
#
# 使い方: sh lesson18/create-docintel.sh <リージョン>
#   リージョンは、授業で指定するもの（コマ1で確認した、使えるリージョン）
#
# 作るもの: リソースグループ rg-cloud2-l18-<GitHubユーザー名>、その中に Document Intelligence（F0）1つ
# エンドポイント（URL。秘密ではない）を lesson18/out/endpoint.txt に保存する。キーは作らない・使わない。
set -eu

LOCATION="${1:-}"
if [ -z "$LOCATION" ]; then
  echo "使い方: sh lesson18/create-docintel.sh <リージョン>"
  exit 1
fi
RG="rg-cloud2-l18-${GITHUB_USER:-me}"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HERE/out"

echo "リソースグループ $RG を $LOCATION に作る"
az group create --name "$RG" --location "$LOCATION" --tags course=cloud2 lesson=18 --output none

PRINCIPAL_ID="$(az ad signed-in-user show --query id --output tsv)"
echo "Document Intelligence（F0）を作り、自分に Cognitive Services User を割り当てる（1〜2分）"
az deployment group create \
  --resource-group "$RG" \
  --template-file "$HERE/infra/docintel.bicep" \
  --parameters principalId="$PRINCIPAL_ID" \
  --query "properties.outputs.endpoint.value" --output tsv > "$HERE/out/endpoint.txt"

echo "作成した。エンドポイント: $(cat "$HERE/out/endpoint.txt")"
echo "ロールの割り当てが行き渡るまで、5〜10分かかることがある。"
