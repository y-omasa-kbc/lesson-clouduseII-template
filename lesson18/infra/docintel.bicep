// コマ18: Document Intelligence（Free F0）を1つ作る（教員が用意したもの）
//
//   ・キーでの接続を無効にする（disableLocalAuth）。自分のサインイン（Microsoft Entra ID）だけで呼ぶ
//   ・Entra ID で呼ぶには、カスタム サブドメイン（customSubDomainName）が必要
//   ・自分に「Cognitive Services User」のロールを、このリソースの範囲だけで割り当てる（最小権限）
//
// 使い方は lesson18/create-docintel.sh を参照。

@description('リージョン（授業で指定する）')
param location string = resourceGroup().location

@description('ロールを割り当てる自分のオブジェクト ID')
param principalId string

@description('リソースの名前（カスタム サブドメインにもなる。世界で一意）')
param name string = 'di-cloud2-${uniqueString(resourceGroup().id)}'

resource docIntel 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: name
  location: location
  kind: 'FormRecognizer'
  sku: {
    name: 'F0'
  }
  tags: {
    course: 'cloud2'
    lesson: '18'
  }
  properties: {
    customSubDomainName: name
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

// Cognitive Services User（組み込みロール）
var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'

resource userRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(docIntel.id, principalId, cognitiveServicesUserRoleId)
  scope: docIntel
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
  }
}

output endpoint string = docIntel.properties.endpoint
output name string = docIntel.name
