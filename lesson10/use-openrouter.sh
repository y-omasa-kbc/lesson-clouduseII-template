#!/bin/sh
# アプリの LLM の呼び先を、本物（OpenRouter）に切り替える（コマ10）
#   教員の合図があったときだけ実行する。確かめ終わったら、すぐに use-mock.sh で戻す。
#
# 1. Codespaces のシークレット（環境変数 OPENROUTER_API_KEY）の値を、Key Vault に書き込む
#    （キーの値は画面に表示しない。--output none を付ける）
# 2. アプリ設定 LLM_PROVIDER を openrouter にする
#    （設定を変えるとアプリが再起動し、Key Vault 参照も読み直される）
set -e
if [ -z "$OPENROUTER_API_KEY" ]; then
  echo "環境変数 OPENROUTER_API_KEY がない。Codespaces のシークレットを確かめる。" >&2
  exit 1
fi
eval "$(azd env get-values)"
az keyvault secret set \
  --vault-name "$KEY_VAULT_NAME" \
  --name openrouter-api-key \
  --value "$OPENROUTER_API_KEY" \
  --output none
az functionapp config appsettings set \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$AZURE_FUNCTION_NAME" \
  --settings "LLM_PROVIDER=openrouter" \
  --output none
echo "LLM の呼び先を openrouter にした。1分ほど待ってから、教員の合図で1件だけ分類する。"
echo "終わったら、すぐに sh lesson10/use-mock.sh を実行して、モックに戻す。"
