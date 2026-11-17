#!/bin/sh
# 擬似認証（テスト利用者のトークン）を使えるようにする（コマ11〜15）
#
# 1. トークンの署名の鍵（ランダムな文字列）を作り、Key Vault のシークレット auth-signing-key に書き込む
#    （鍵の値は画面に表示しない。変数に入れて渡し、--output none を付ける）
# 2. アプリ設定 DEV_LOGIN_ENABLED を true にする
#    （設定を変えるとアプリが再起動し、Key Vault 参照 AUTH_SIGNING_KEY も読み直される）
#
# もう一度実行すると、鍵が作り直される。それまでに発行したトークンは、すべて使えなくなる。
set -e
eval "$(azd env get-values)"
SIGNING_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
az keyvault secret set \
  --vault-name "$KEY_VAULT_NAME" \
  --name auth-signing-key \
  --value "$SIGNING_KEY" \
  --output none
unset SIGNING_KEY
az functionapp config appsettings set \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$AZURE_FUNCTION_NAME" \
  --settings "DEV_LOGIN_ENABLED=true" \
  --output none
echo "署名の鍵を Key Vault に置き、DEV_LOGIN_ENABLED を true にした。1分ほど待ってから確かめる。"
