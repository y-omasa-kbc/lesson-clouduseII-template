// =====================================================================
// リソースグループの中に作るリソース
//   - Static Web Apps（Free）       : フロントエンド（React）
//   - Functions（Flex Consumption） : API（Python）
//   - Storage                       : Functions の動作とデプロイに使う
//   - Cosmos DB（Free tier）        : 問い合わせのデータ
//   - Key Vault（Standard）         : OpenRouter のキーを保管する（コマ6で追加）
//   - AI Search（Free）             : 文書の検索（コマ14で追加）
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
//
// コマ11で追加したもの
//   - アプリ設定: AUTH_SIGNING_KEY（利用者トークンの署名の鍵。Key Vault 参照）、DEV_LOGIN_ENABLED（擬似認証の入口。既定は false）
//   - 画面の認証（Static Web Apps の組み込み認証）は、app/public/staticwebapp.config.json で設定する（ここには書かない）
//
// コマ12: 構成の変更はない（ツールを使うアシスタントの API が増え、ツールが問い合わせのデータを読み書きするだけ）
//
// コマ13: 構成の変更はない（MCP サーバーは、既存の Functions の HTTP の関数 /api/mcp として公開する）
//
// コマ14で追加したもの
//   - Azure AI Search（Free）と、ロールの割り当て（Functions: 検索だけ／自分: インデックスの作成と文書の投入）
//   - アプリ設定: AZURE_SEARCH_ENDPOINT、AZURE_SEARCH_INDEX、EMBEDDING_PROVIDER
//
// コマ15で追加・変更したもの
//   - Blob のコンテナー docs（取り込む文書を置く）、キュー ingest-events と毒キュー ingest-events-poison
//   - Event Grid のシステム トピック（Storage のイベント）と、イベント サブスクリプション
//     （docs の .txt が作られた・消された → キュー ingest-events にイベントを入れる）
//   - Cosmos DB のコンテナー embcache（埋め込みキャッシュ）と ingestlog（取り込みの記録。7日で消える）
//   - アプリ設定: COSMOS_EMBCACHE_CONTAINER、COSMOS_INGESTLOG_CONTAINER
//   - Functions のマネージドIDの AI Search のロールを、Search Index Data Reader から Search Index Data Contributor に変更
//     （取り込みの関数が、文書を投入・削除するため）
//   - 自分に、Blob のデータを読み書きするロールと、Cosmos DB のデータを読み書きするロールを追加（演習の道具で使う）
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
var searchIndexName = 'kamome-docs'
var docsContainerName = 'docs' // 取り込む文書を置く Blob のコンテナー（コマ15）
var ingestQueueName = 'ingest-events' // Event Grid が Blob のイベントを入れるキュー（コマ15）
var embCacheContainerName = 'embcache' // 埋め込みキャッシュ（コマ15）
var ingestLogContainerName = 'ingestlog' // 取り込みの記録（コマ15）
var authSigningKeySecretName = 'auth-signing-key' // 利用者トークンの署名の鍵（コマ11。値は lesson11/enable-dev-login.sh が書き込む）

// 組み込みロールの ID（ロールの名前ではなく、Azure 全体で決まっている ID で指定する）
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6' // Key Vault Secrets User（シークレットを読む）
var keyVaultSecretsOfficerRoleId = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7' // Key Vault Secrets Officer（シークレットを書く）
var cosmosDataContributorRoleId = '00000000-0000-0000-0000-000000000002' // Cosmos DB Built-in Data Contributor（データを読み書きする）
var storageQueueDataContributorRoleId = '974c5e8b-45b9-4653-ba55-5f855dd0fb88' // Storage Queue Data Contributor（キューのメッセージを読み書きする）
var searchIndexDataContributorRoleId = '8ebe5a00-799e-43f5-93ac-243d3dce84a7' // Search Index Data Contributor（文書を投入する）
var searchServiceContributorRoleId = '7ca78c08-252a-4471-8644-bb5ff32d4ba0' // Search Service Contributor（インデックスを作る）
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe' // Storage Blob Data Contributor（Blob を読み書きする）

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

// 取り込む文書を置くコンテナー（コマ15）。公開はしない（allowBlobPublicAccess: false）
resource docsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: docsContainerName
}

// Blob のイベントを受け取るキューと、その毒キュー（コマ15）
resource ingestQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = {
  parent: queueService
  name: ingestQueueName
}

resource ingestPoisonQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = {
  parent: queueService
  name: '${ingestQueueName}-poison'
}

// ---------------------------------------------------------------------
// Event Grid（コマ15）: Storage で起きたこと（Blob が作られた・消された）を、イベントとして配る
//   システム トピック: Azure のサービス（ここでは Storage）が出すイベントの受け口
//   イベント サブスクリプション: どのイベントを、どこに届けるか。ここでは、docs の .txt のイベントだけを、
//     キュー ingest-events に届ける。キューに入れておけば、関数が止まっていても、イベントは失われない
// ---------------------------------------------------------------------
resource storageEvents 'Microsoft.EventGrid/systemTopics@2022-06-15' = {
  name: 'evgt-${resourceToken}'
  location: location
  tags: tags
  properties: {
    source: storage.id
    topicType: 'Microsoft.Storage.StorageAccounts'
  }
}

resource blobEvents 'Microsoft.EventGrid/systemTopics/eventSubscriptions@2022-06-15' = {
  parent: storageEvents
  name: 'docs-to-ingest-queue'
  properties: {
    eventDeliverySchema: 'EventGridSchema'
    destination: {
      endpointType: 'StorageQueue'
      properties: {
        resourceId: storage.id
        queueName: ingestQueueName
        queueMessageTimeToLiveInSeconds: 86400 // キューの中で1日たったイベントは消える
      }
    }
    filter: {
      includedEventTypes: [
        'Microsoft.Storage.BlobCreated'
        'Microsoft.Storage.BlobDeleted'
      ]
      subjectBeginsWith: '/blobServices/default/containers/${docsContainerName}/blobs/'
      subjectEndsWith: '.txt'
    }
    retryPolicy: {
      maxDeliveryAttempts: 30
      eventTimeToLiveInMinutes: 1440
    }
  }
  dependsOn: [
    ingestQueue
    docsContainer
  ]
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

// 埋め込みキャッシュ（コマ15）: id は「埋め込みの方法＋チャンクの内容」のハッシュ。値はベクトル
//   TTL は付けない。同じ内容のチャンクのベクトルは、埋め込みの方法が同じなら変わらないため
resource embCacheContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: embCacheContainerName
  properties: {
    resource: {
      id: embCacheContainerName
      partitionKey: {
        paths: [ '/id' ]
        kind: 'Hash'
      }
    }
  }
}

// 取り込みの記録（コマ15）: イベント1つの処理の結果。パーティションキーは文書（source）。7日で自動で消える
resource ingestLogContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: ingestLogContainerName
  properties: {
    resource: {
      id: ingestLogContainerName
      partitionKey: {
        paths: [ '/source' ]
        kind: 'Hash'
      }
      defaultTtl: 604800
    }
  }
}

// ---------------------------------------------------------------------
// Azure AI Search（Free）: 文書の全文検索とベクトル検索（コマ14）
//   Free は1サブスクリプションに1つだけ。API キーを使わず、ロールの割り当てで接続する
//   aadOrApiKey: Microsoft Entra ID のトークン（マネージドID・自分のサインイン）でも接続できるようにする
// ---------------------------------------------------------------------
resource search 'Microsoft.Search/searchServices@2025-05-01' = {
  name: 'srch-${resourceToken}'
  location: location
  tags: tags
  sku: {
    name: 'free'
  }
  properties: {
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
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
        // 取り込み（コマ15）: 埋め込みキャッシュと、取り込みの記録
        {
          name: 'COSMOS_EMBCACHE_CONTAINER'
          value: embCacheContainerName
        }
        {
          name: 'COSMOS_INGESTLOG_CONTAINER'
          value: ingestLogContainerName
        }
        {
          name: 'OPENROUTER_API_KEY'
          value: '@Microsoft.KeyVault(VaultName=${keyVault.name};SecretName=${openRouterSecretName})'
        }
        // 利用者トークンの署名の鍵（コマ11）。値はコードにも Bicep にも書かず、Key Vault に置く
        {
          name: 'AUTH_SIGNING_KEY'
          value: '@Microsoft.KeyVault(VaultName=${keyVault.name};SecretName=${authSigningKeySecretName})'
        }
        // 擬似認証の入口（POST /api/dev/login）を開くか（コマ11）。既定は閉じておき、演習のときだけ開く
        {
          name: 'DEV_LOGIN_ENABLED'
          value: 'false'
        }
        // 検索（コマ14）: 接続先とインデックスの名前。キーは使わず、マネージドIDで接続する
        {
          name: 'AZURE_SEARCH_ENDPOINT'
          value: 'https://${search.name}.search.windows.net'
        }
        {
          name: 'AZURE_SEARCH_INDEX'
          value: searchIndexName
        }
        // 質問の埋め込みの作り方（mock: モック埋め込み／openrouter: 本物）。既定はモック（費用がかからない）
        {
          name: 'EMBEDDING_PROVIDER'
          value: 'mock'
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
    ingestQueue
    ingestPoisonQueue
    embCacheContainer
    ingestLogContainer
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

// Functions のマネージドIDに、文書を検索・投入・削除する権限を与える
//   コマ14では「検索する」権限（Search Index Data Reader）だけだった。コマ15で、同じ関数アプリの中の
//   取り込みの関数（ingest_worker）が文書を投入・削除するため、Search Index Data Contributor に変えた。
//   本番では、取り込みを別の関数アプリ（別のマネージドID）に分け、画面から呼ばれる API には検索の権限だけを残す
resource apiSearchIndexContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, api.id, searchIndexDataContributorRoleId)
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributorRoleId)
    principalId: api.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// 自分（azd を実行した人）に、インデックスを作り、文書を投入する権限を与える（コマ14）
resource userSearchServiceContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(search.id, principalId, searchServiceContributorRoleId)
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchServiceContributorRoleId)
    principalId: principalId
    principalType: 'User'
  }
}

resource userSearchIndexContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(search.id, principalId, searchIndexDataContributorRoleId)
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributorRoleId)
    principalId: principalId
    principalType: 'User'
  }
}

// 自分（azd を実行した人）に、Blob を読み書きする権限を与える（コマ15）
//   演習で、文書を docs に置く・消す（lesson15/blob.sh。az storage blob … --auth-mode login）ために使う
resource userStorageBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(storage.id, principalId, storageBlobDataContributorRoleId)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
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

// 自分（azd を実行した人）にも、Cosmos DB のデータを読み書きする権限を与える（コマ15）
//   演習で、取り込みの記録と埋め込みキャッシュの件数を見る（lesson15/status.py）ために使う
resource userCosmosDataContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = if (!empty(principalId)) {
  parent: cosmos
  name: guid(cosmos.id, principalId, cosmosDataContributorRoleId)
  properties: {
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${cosmosDataContributorRoleId}'
    principalId: principalId
    scope: cosmos.id
  }
}

output apiBaseUrl string = 'https://${api.properties.defaultHostName}'
output webUrl string = 'https://${web.properties.defaultHostname}'
output functionName string = api.name
output keyVaultName string = keyVault.name
output storageAccountName string = storage.name
output searchEndpoint string = 'https://${search.name}.search.windows.net'
output searchIndexName string = searchIndexName
output cosmosEndpoint string = cosmos.properties.documentEndpoint
