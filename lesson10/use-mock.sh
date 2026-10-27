#!/bin/sh
# アプリの LLM の呼び先を、モックLLM に戻す（コマ10）
set -e
eval "$(azd env get-values)"
az functionapp config appsettings set \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$AZURE_FUNCTION_NAME" \
  --settings "LLM_PROVIDER=mock" \
  --output none
echo "LLM の呼び先を mock に戻した。"
