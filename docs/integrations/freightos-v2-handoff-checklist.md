# Freightos route-estimates-v1 交付收口与 v2 对接清单

> 冻结日期：2026-08-30（Asia/Shanghai）  
> Git 基线：`40e7cde4ac7476b44bb3406d5730a42fbbd9197c`  
> 建议功能分支：`feature/freightos-route-estimates-v1`  
> 建议提交主题：`feat(route-estimates): add Freightos public estimate contract`

## 1. 已完成

- [x] 固定服务端端点 `POST /api/route-estimates/v1`。
- [x] 固定契约版本 `route-estimates-v1`。
- [x] 请求只接受 `location-id-v2` 港口 ID 和严格货物字段。
- [x] 地点只读查询要求节点同时具有 `TransportLocation:Port:RoutePlanningPort`。
- [x] 只读取唯一匹配节点的 `canonical_unlocode`，不通过删除 `PORT-` 猜测。
- [x] 上游固定为 Freightos 公共 HTTPS 端点，客户端不能选择主机或路径。
- [x] 响应区分 `available/partial/unsupported/unavailable/expired`。
- [x] 不生成 `mostLikely`，排序使用区间上限和 `conservative-upper-bound-v1`。
- [x] RoRo 返回 `unsupported`，不静默转换为 container。
- [x] 服务认证头固定为 `X-Route-Estimate-Token`。
- [x] Token 不进入 URL、日志、响应、fixture 或浏览器配置。
- [x] 增加请求体、Provider 响应、配置范围、缓存和锁表安全边界。
- [x] 增加进程内 TTL 缓存、单飞去重、100 次/小时保护和熔断。
- [x] 不把 Freightos 结果写入 Neo4j，不修改 `RouteSegment`。
- [x] 生成静态 JSON Schema、OpenAPI 和五类脱敏 fixture。
- [x] `.env.example` 只增加变量名称、默认值和无 Freightos API Key 说明。
- [x] 本轮未调用真实 Freightos，未部署、提交、推送或写 Neo4j。

## 2. 冻结契约

### 2.1 Endpoint 与认证

```text
POST /api/route-estimates/v1
Content-Type: application/json
X-Route-Estimate-Token: <server-side secret>
```

v2 必须在自己的服务端发送 Token。禁止放入浏览器、`VITE_*`、`NEXT_PUBLIC_*`、URL、日志、响应或客户端 fixture。

### 2.2 请求字段

```text
contractVersion = route-estimates-v1
originPortId
destinationPortId
cargo.quantity
cargo.shipmentMethod = container | lcl | roro
cargo.containerType = 20ft | 40ft | 40hc | null
cargo.grossWeightKg
cargo.volumeM3
cargo.lengthCm
cargo.widthCm
cargo.heightCm
```

完整机器可读定义：`contracts/route-estimates-v1/request.schema.json`。

### 2.3 响应字段

```text
contractVersion
status
provider
providerProduct
retrievedAt
cacheExpiresAt
providerValidUntil
currency
costRange.min / costRange.max
transitTimeRange.minDays / transitTimeRange.maxDays
rankingCostUsd
rankingDurationDays
rankingPolicy
attributionLabel
attributionUrl
requestFingerprint
responseSha256
missingData
assumptions
cacheStatus
```

完整机器可读定义：`contracts/route-estimates-v1/response.schema.json`。

### 2.4 业务状态与 HTTP 状态

| 类型 | 状态 | 说明 |
|---|---|---|
| 业务 | `available` | 成本与时效区间均可用 |
| 业务 | `partial` | 仅一个区间可用 |
| 业务 | `unsupported` | 公共市场不支持，例如 RoRo |
| 业务 | `unavailable` | 无有效缓存且 Provider、限流或熔断不可用 |
| 业务 | `expired` | 缓存过期且刷新失败，不返回陈旧区间 |
| HTTP | `200` | 上述五种业务状态 |
| HTTP | `401` | 服务 Token 缺失或错误 |
| HTTP | `413` | 请求体超过 16 KiB |
| HTTP | `422` | 请求或受审计地点身份无效 |
| HTTP | `503` | Production 认证或地点注册表不可用 |

OpenAPI 冻结文件：`contracts/route-estimates-v1/openapi.json`。

## 3. Fixture 位置

- `tests/fixtures/route_estimates_v1/available.json`
- `tests/fixtures/route_estimates_v1/partial.json`
- `tests/fixtures/route_estimates_v1/unsupported.json`
- `tests/fixtures/route_estimates_v1/unavailable.json`
- `tests/fixtures/route_estimates_v1/expired.json`

Fixture 全部是固定脱敏数据，不来源于本轮真实 Provider 请求。

## 4. 数据真实性边界

Freightos 公共结果是路线级港到港公共市场区间，不是：

- `RouteSegment` 观测；
- 承运人或货代报价；
- 某条候选航线、船舶、航次或班期的专属价格；
- 舱位、合同或到达承诺。

结果只保存在进程缓存和当前 HTTP 响应中，不写 Neo4j。当前 `/api/routes/recommend` 未修改，也不会自动调用 Freightos。

## 5. 港口身份验证

本轮运行进程没有提供 AuraDB 运行时凭据。为避免读取 `.env`，未连接 Neo4j，也未猜测验证结果。仓库所有者可在 Aura Console 的 Query 页面执行以下只读语句：

```cypher
UNWIND ['PORT-CNSHG', 'PORT-USLAX'] AS requestedId
OPTIONAL MATCH (location {location_id: requestedId})
WITH requestedId, [item IN collect(CASE WHEN location IS NULL THEN null ELSE {
  labels: labels(location),
  version: location.location_id_version,
  canonicalUnlocode: location.canonical_unlocode
} END) WHERE item IS NOT NULL] AS matches
RETURN requestedId,
       size(matches) AS matchCount,
       matches,
       size(matches) = 1
         AND all(label IN ['TransportLocation','Port','RoutePlanningPort']
                 WHERE label IN matches[0].labels)
         AND matches[0].version = 'location-id-v2'
         AND matches[0].canonicalUnlocode IS NOT NULL AS valid;
```

预期每个 ID 都是 `matchCount=1` 且 `valid=true`。如果不满足，应停止 v2 对接并修复地点主数据；不得由应用猜测 UN/LOCODE，也不得在本交付批次写数据库。

## 6. v2 对接前置条件

- [ ] 完成上节两个港口的 Aura Console 只读验证。
- [ ] 在 v2 服务端密文环境配置 `ROUTE_ESTIMATE_SERVICE_TOKEN`。
- [ ] 确认 Token 未进入浏览器构建变量和客户端网络请求。
- [ ] v2 使用固定 endpoint，不允许用户输入上游 URL。
- [ ] v2 连接超时建议 5 秒，总超时建议 20 秒。
- [ ] v2 展示 `costRange`、`transitTimeRange`、Provider、Beta 和 Freightos 署名。
- [ ] v2 不把 `rankingCostUsd/rankingDurationDays` 显示成最可能值。
- [ ] v2 对 `unsupported/unavailable/expired` 提供内部估算或不可用降级。
- [ ] 多实例生产部署前增加共享缓存和集中限流；当前进程内缓存不是分布式缓存。
- [ ] 商业化前重新完成 Freightos 条款、法务和采购复核。

## 7. 测试结果

- Freightos 专项测试：`32 passed`。
- 全量测试：`219 passed`。
- `git diff --check`：通过。
- 真实 Freightos 请求：`0` 次。
- 当前警告：FastAPI `TestClient` 对当前 `httpx` 组合产生一条既有弃用警告，不影响测试结果。

## 8. 建议提交范围

应提交本次工作区中的 Freightos 实现、契约、fixture、测试和说明文档，包括：

```text
.env.example
README.md
app/main.py
app/route_estimates/**
contracts/route-estimates-v1/**
docs/data_sources.md
docs/integrations/freightos-route-estimates-v1.md
docs/integrations/freightos-v2-handoff-checklist.md
tests/fixtures/route_estimates_v1/**
tests/test_freightos_route_estimates.py
```

不得提交 `.env`。本轮不自动创建分支、暂存、提交、推送或部署。

## 9. 未完成项

- AuraDB 中 `PORT-CNSHG` 和 `PORT-USLAX` 的三标签、唯一性、v2 版本和 UN/LOCODE 仍需人工只读确认。
- 当前缓存与限流仅在单进程内有效。
- `/api/routes/recommend` 尚未接入该估算；这属于后续独立 v2 任务。
- Freightos Beta 和商业使用条款仍需上线前复核。
