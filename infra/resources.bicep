// =====================================================================
// リソースグループの中に作るリソース
//   - Static Web Apps（Free）       : フロントエンド（React）
//   - Functions（Flex Consumption） : API（Python）
//   - Storage                       : Functions の動作とデプロイに使う
//   - Cosmos DB（Free tier）        : 問い合わせのデータ
//   - Key Vault（Standard）         : OpenRouter のキーを保管する（コマ6で追加）
//
// コマ6で追加したもの
//   - Key Vault と、Key Vault に対するロールの割り当て（Functions: 読むだけ／自分: 書き込み）
//   - Cosmos DB のデータに対するロールの割り当て（Functions のマネージドID）
//   - Cosmos DB のキーをアプリ設定から削除し、OpenRouter のキーを Key Vault 参照にした
//
// コマ7で追加したもの
//   - Cosmos DB のコンテナー conversations（アシスタントとの会話の履歴）
//   - アプリ設定: COSMOS_CONVERSATIONS_CONTAINER、LLM_PROVIDER、PYTHON_ENABLE_INIT_INDEXING
//
// コマ8: 構成の変更はない（問い合わせのドキュメントに、分類の結果の項目が増えるだけ）
//
// コマ9で追加したもの
//   - Cosmos DB のコンテナー llmcache（LLM の応答のキャッシュ。項目ごとの TTL で自動で消える）
//   - アプリ設定: COSMOS_CACHE_CONTAINER
//
// コマ10で追加・変更したもの
//   - Storage のキュー classify-jobs と、毒キュー classify-jobs-poison
//   - Storage のキューに対するロールの割り当て（Functions のマネージドID・自分）
//   - Cosmos DB のコンテナー jobs（非同期ジョブの状態）、アプリ設定: COSMOS_JOBS_CONTAINER
//   - Functions の最大インスタンス数を 1 にした（同時に LLM を呼ぶ数を絞る。api/host.json の batchSize と合わせて）
// =====================================================================
param location string
param webLocation string
param tags object
param resourceToken string
param cosmosFreeTier bool
param principalId string

var databaseName = 'appdb'
var containerName = 'inquiries'
var conversationsContainerName = 'conversations'
var cacheContainerName = 'llmcache'
var jobsContainerName = 'jobs'
var jobQueueName = 'classify-jobs'
var deploymentContainerName = 'deploymentpackage'
var openRouterSecretName = 'openrouter-api-key'

// 組み込みロールの ID（ロールの名前ではなく、Azure 全体で決まっている ID で指定する）
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6' // Key Vault Secrets User（シークレットを読む）
var keyVaultSecretsOfficerRoleId = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7' // Key Vault Secrets Officer（シークレットを書く）
var cosmosDataContributorRoleId = '00000000-0000-0000-0000-000000000002' // Cosmos DB Built-in Data Contributor（データを読み書きする）
var storageQueueDataContributorRoleId = '974c5e8b-45b9-4653-ba55-5f855dd0fb88' // Storage Queue Data Contributor（キューのメッセージを読み書きする）

// ---------------------------------------------------------------------
// Static Web Apps（Free）: React で作った画面を配信する
// ---------------------------------------------------------------------
resource web 'Microsoft.Web/staticSites@2023-12-01' = {
  name: 'stapp-${resourceToken}'
  location: webLocation
  tags: union(tags, { 'azd-service-name': 'web' })
  sku: {
    name: 'Free'
    tier: 'Free'
  }
  properties: {}
}

// ---------------------------------------------------------------------
// Storage: Functions が内部で使う（デプロイしたコードの置き場所など）
// ---------------------------------------------------------------------
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'st${resourceToken}'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    supportsHttpsTrafficOnly: true
    defaultToOAuthAuthentication: true
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource deploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: deploymentContainerName
}

// キュー（コマ10）: 一括分類のジョブで、問い合わせ1件ごとのメッセージを入れる
//   毒キュー（<キューの名前>-poison）は、上限の回数まで処理に失敗したメッセージが移される先
resource queueService 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource jobQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = {
  parent: queueService
  name: jobQueueName
}

resource jobPoisonQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = {
  parent: queueService
  name: '${jobQueueName}-poison'
}

// ---------------------------------------------------------------------
// Cosmos DB（Free tier）: 問い合わせを保存する
// ---------------------------------------------------------------------
resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: 'cosmos-${resourceToken}'
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    enableFreeTier: cosmosFreeTier
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmos
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
    // データベース全体で 400 RU/s を共有する（Free tier の範囲に収める）
    options: {
      throughput: 400
    }
  }
}

resource container 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: containerName
  properties: {
    resource: {
      id: containerName
      partitionKey: {
        paths: [ '/id' ]
        kind: 'Hash'
      }
    }
  }
}

// 会話の履歴（コマ7）: メッセージ1件を1ドキュメントとして保存する
//   パーティションキーは会話の ID。1つの会話のメッセージは同じパーティションに入るので、
//   会話の履歴を読むときに、1つのパーティションだけを読めばよい
resource conversationsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: conversationsContainerName
  properties: {
    resource: {
      id: conversationsContainerName
      partitionKey: {
        paths: [ '/conversationId' ]
        kind: 'Hash'
      }
    }
  }
}

// LLM の応答のキャッシュ（コマ9）
//   パーティションキーはキャッシュのキー（id）そのもの。1件ずつ読み書きするだけなので、これで足りる
//   defaultTtl: -1 は「TTL を使えるようにするが、既定では消さない」。消すまでの秒数は、項目ごとの ttl で決める
resource cacheContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: cacheContainerName
  properties: {
    resource: {
      id: cacheContainerName
      partitionKey: {
        paths: [ '/id' ]
        kind: 'Hash'
      }
      defaultTtl: -1
    }
  }
}

// 非同期ジョブの状態（コマ10）
//   パーティションキーはジョブの ID。1つのジョブの見出しと、1件ごとの結果が、同じパーティションに入る
//   defaultTtl: 7日たったジョブの記録は、自動で消える
resource jobsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: jobsContainerName
  properties: {
    resource: {
      id: jobsContainerName
      partitionKey: {
        paths: [ '/jobId' ]
        kind: 'Hash'
      }
      defaultTtl: 604800
    }
  }
}

// ---------------------------------------------------------------------
// Functions（Flex Consumption・Python）: API
// ---------------------------------------------------------------------
resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: 'plan-${resourceToken}'
  location: location
  tags: tags
  kind: 'functionapp'
  sku: {
    name: 'FC1'
    tier: 'FlexConsumption'
  }
  properties: {
    reserved: true
  }
}

resource api 'Microsoft.Web/sites@2024-04-01' = {
  name: 'func-${resourceToken}'
  location: location
  tags: union(tags, { 'azd-service-name': 'api' })
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storage.properties.primaryEndpoints.blob}${deploymentContainerName}'
          authentication: {
            type: 'SystemAssignedIdentity'
          }
        }
      }
      scaleAndConcurrency: {
        // 最大インスタンス数（コマ10で 40 から 1 に変更）。Flex Consumption では、HTTP の関数のまとまりと、
        // キュー トリガーの関数のそれぞれが、この数まで増える。キューがたまっても、LLM を同時に呼ぶ数が増えすぎないようにする
        maximumInstanceCount: 1
        instanceMemoryMB: 2048
      }
      runtime: {
        name: 'python'
        version: '3.11'
      }
    }
    siteConfig: {
      // ブラウザ（Static Web Apps の画面）から API を呼べるようにする（CORS）
      cors: {
        allowedOrigins: [
          'https://${web.properties.defaultHostname}'
        ]
      }
      // アプリ設定（プログラムからは環境変数として読める）
      appSettings: [
        {
          name: 'AzureWebJobsStorage__blobServiceUri'
          value: storage.properties.primaryEndpoints.blob
        }
        {
          name: 'AzureWebJobsStorage__queueServiceUri'
          value: storage.properties.primaryEndpoints.queue
        }
        {
          name: 'AzureWebJobsStorage__tableServiceUri'
          value: storage.properties.primaryEndpoints.table
        }
        {
          name: 'COSMOS_ENDPOINT'
          value: cosmos.properties.documentEndpoint
        }
        {
          name: 'COSMOS_DATABASE'
          value: databaseName
        }
        {
          name: 'COSMOS_CONTAINER'
          value: containerName
        }
        {
          name: 'COSMOS_CONVERSATIONS_CONTAINER'
          value: conversationsContainerName
        }
        {
          name: 'COSMOS_CACHE_CONTAINER'
          value: cacheContainerName
        }
        {
          name: 'COSMOS_JOBS_CONTAINER'
          value: jobsContainerName
        }
        {
          name: 'OPENROUTER_API_KEY'
          value: '@Microsoft.KeyVault(VaultName=${keyVault.name};SecretName=${openRouterSecretName})'
        }
        // LLM の呼び先（mock: モックLLM／openrouter: 本物）。既定はモック（共有の利用上限を消費しない）
        {
          name: 'LLM_PROVIDER'
          value: 'mock'
        }
        // HTTP ストリーミング（コマ7）を使うために必要な設定（Python 3.12 以前）
        {
          name: 'PYTHON_ENABLE_INIT_INDEXING'
          value: '1'
        }
      ]
    }
  }
  dependsOn: [
    deploymentContainer
    conversationsContainer
    cacheContainer
    jobsContainer
    jobQueue
    jobPoisonQueue
  ]
}

// Functions の ID（マネージドID）に、Storage の Blob を読み書きする権限を与える
// （Storage Blob Data Owner）
resource apiStorageBlobOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, api.id, 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
    principalId: api.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Functions のマネージドIDに、キューのメッセージを読み書きする権限を与える（コマ10）
//   キュー トリガーがメッセージを取り出す・失敗したメッセージを毒キューに移す・API がメッセージを入れる、に使う
resource apiStorageQueueContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, api.id, storageQueueDataContributorRoleId)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageQueueDataContributorRoleId)
    principalId: api.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// 自分（azd を実行した人）にも、キューのメッセージを読み書きする権限を与える（コマ10）
//   演習で、キューの中身を見る・同じメッセージをもう一度入れる（lesson10/queue_tool.py）ために使う
resource userStorageQueueContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(storage.id, principalId, storageQueueDataContributorRoleId)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageQueueDataContributorRoleId)
    principalId: principalId
    principalType: 'User'
  }
}

// ---------------------------------------------------------------------
// Key Vault（Standard）: OpenRouter のキーを保管する（コマ6）
//   シークレットの値は、ここ（コード）には書かない。構築した後に、自分が CLI で書き込む。
// ---------------------------------------------------------------------
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-${resourceToken}'
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    // アクセスの制御に Azure RBAC（ロールの割り当て）を使う
    enableRbacAuthorization: true
    // 消しても7日間は復元できる（論理削除）。azd down --purge で完全に消す
    softDeleteRetentionInDays: 7
    // 消去保護（purge protection）は有効にしない。有効にすると、演習の後に完全に消せなくなる
  }
}

// Functions のマネージドIDに「シークレットを読む」権限だけを与える（範囲はこの Key Vault だけ）
resource apiKeyVaultSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, api.id, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: api.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// 自分（azd を実行した人）に「シークレットを書く」権限を与える
//   サブスクリプションの所有者（Owner）でも、この割り当てがないとシークレットの値を読み書きできない
resource userKeyVaultSecretsOfficer 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(keyVault.id, principalId, keyVaultSecretsOfficerRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsOfficerRoleId)
    principalId: principalId
    principalType: 'User'
  }
}

// ---------------------------------------------------------------------
// Cosmos DB のデータに対するロールの割り当て（コマ6）
//   Functions のマネージドIDに、データの読み書きだけを許す（アカウントの設定の変更はできない）
//   Cosmos DB のデータの権限は、Azure RBAC とは別の、Cosmos DB 独自の仕組みで割り当てる
// ---------------------------------------------------------------------
resource apiCosmosDataContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmos
  name: guid(cosmos.id, api.id, cosmosDataContributorRoleId)
  properties: {
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${cosmosDataContributorRoleId}'
    principalId: api.identity.principalId
    scope: cosmos.id
  }
}

output apiBaseUrl string = 'https://${api.properties.defaultHostName}'
output webUrl string = 'https://${web.properties.defaultHostname}'
output functionName string = api.name
output keyVaultName string = keyVault.name
output storageAccountName string = storage.name
