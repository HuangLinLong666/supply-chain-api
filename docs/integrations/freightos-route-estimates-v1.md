# Freightos 公共市场成本与时效估算（route-estimates-v1）

> 后端接口：`POST /api/route-estimates/v1`  
> 契约版本：`route-estimates-v1`  
> Provider：Freightos Public Marketplace Shipping Estimates API（Beta）  
> 官方说明：https://ship.freightos.com/api/shippingCalculator  
> 必须署名并链接：https://ship.freightos.com

## 1. 数据性质与使用边界

该接口返回 Freightos 公共市场的成本区间和运输时间区间，仅用于路线方案比较。它不是：

- 承运人报价；
- 已订舱价格；
- 运输合同；
- 舱位或班期承诺；
- 保证到达时间。

Freightos 将该公共 API 标记为 Beta，并声明接口可能变更。公共数据无需 API Key，但仍受 Freightos 条款、可见署名和单 IP 每小时 100 次调用限制约束。商业化、公开再分发或扩大调用量前，必须由项目负责人、法务和采购重新核对最新条款；“无需 API Key”不代表可以无限制商业复用。

当前只支持公共市场可表达的港到港集装箱和 LCL 估算。`shipmentMethod=roro` 会返回 `unsupported`，绝不会静默改写为集装箱。

## 2. 安全设计

- 上游固定为 `https://ship.freightos.com/api/shippingCalculator`，请求不能指定 URL、主机、协议或路径。
- 只发送规范 UN/LOCODE、load type、模式、数量、每 load unit 重量/体积和必要尺寸。
- 不发送供应商名称、产品名称、用户账号、Auth0/Convex 标识或完整货物档案。
- 不记录完整 URL、查询字符串、货物请求或原始响应。
- 不把 Freightos 响应写入 Neo4j，也不修改 `RouteSegment`。
- `requestFingerprint` 是规范化 Provider 请求的 SHA-256；`responseSha256` 是解析成功的 Provider JSON 的规范 SHA-256，二者不包含服务认证 Token。

## 3. 服务端环境变量

以下变量只配置在本地服务端环境、Render Environment 或其他后端密文环境中。文档和测试不得填写真实值。

| 变量 | Production | 说明 |
|---|---|---|
| `ROUTE_ESTIMATE_SERVICE_TOKEN` | 必填 | 服务间请求头 `X-Route-Estimate-Token` 的长随机密文 |
| `APP_ENV` | `production` | Production 下不允许无认证调用 |
| `ROUTE_ESTIMATE_ALLOW_LOCAL_UNAUTHENTICATED` | `false` | 仅 Development 回环地址调试可设为 `true` |
| `FREIGHTOS_TIMEOUT_SECONDS` | 可选，默认 `15` | 单次 HTTP 超时 |
| `FREIGHTOS_MAX_RETRIES` | 可选，默认 `2` | 有限重试次数 |
| `FREIGHTOS_CACHE_TTL_SECONDS` | 可选，默认 `900` | 进程内缓存 TTL |
| `FREIGHTOS_HOURLY_REQUEST_LIMIT` | 可选，最大 `100` | 本进程每小时调用保护上限 |
| `FREIGHTOS_CIRCUIT_FAILURE_THRESHOLD` | 可选，默认 `3` | 连续失败熔断阈值 |
| `FREIGHTOS_CIRCUIT_OPEN_SECONDS` | 可选，默认 `300` | 熔断保持时间 |

公共市场 v1 不需要、也不接受 Freightos API Key。未来接入 Freightos 私有站点时必须建立独立 v2 Provider，不能把私有 Key 放进 URL、前端代码或本接口请求体。

数值配置边界为：超时 `1-60` 秒、重试 `0-5` 次、缓存 `1-3600` 秒、每小时调用 `1-100` 次、熔断失败阈值 `1-20` 次、熔断时间 `1-3600` 秒。越界或无法解析的配置会拒绝启动该服务能力，不会静默采用危险值。

### Development 本地模式

只在本机调试时可使用：

```text
APP_ENV=development
ROUTE_ESTIMATE_ALLOW_LOCAL_UNAUTHENTICATED=true
```

该模式只允许回环客户端，不能描述为 Production 安全。Production 必须设置 `ROUTE_ESTIMATE_SERVICE_TOKEN`。

## 4. 请求契约

```http
POST /api/route-estimates/v1
Content-Type: application/json
X-Route-Estimate-Token: 服务端密文
```

40HC 示例：

```json
{
  "contractVersion": "route-estimates-v1",
  "originPortId": "PORT-CNSHG",
  "destinationPortId": "PORT-USLGB",
  "cargo": {
    "quantity": 2,
    "shipmentMethod": "container",
    "containerType": "40hc",
    "grossWeightKg": 15000,
    "volumeM3": 50
  }
}
```

字段规则：

- `originPortId`、`destinationPortId` 必须是 `location-id-v2` 港口 ID。
- 后端只通过 `TransportLocation:Port` 的精确 `location_id` 查询读取 `canonical_unlocode`。
- 不存在、重复、不是 v2 或缺少规范 UN/LOCODE 均返回 `422`。
- `quantity` 表示 Freightos load unit 数量，范围 `1-100`。
- `grossWeightKg` 和 `volumeM3` 表示本次请求总量；发送给 Freightos 前除以 `quantity`。
- 箱型只支持 `20ft`、`40ft`、`40hc`。
- LCL 必须提供重量，并提供体积或完整的 `lengthCm/widthCm/heightCm`。
- 未知字段、重复 JSON 字段、超过 16 KiB 的请求体都会被拒绝。

## 5. 响应契约

```json
{
  "contractVersion": "route-estimates-v1",
  "status": "available",
  "provider": "Freightos",
  "providerProduct": "Public Marketplace Shipping Estimates API (Beta)",
  "retrievedAt": "2026-08-30T03:00:00Z",
  "cacheExpiresAt": "2026-08-30T03:15:00Z",
  "providerValidUntil": null,
  "currency": "USD",
  "costRange": {"min": 3262, "max": 3554},
  "transitTimeRange": {"minDays": 17, "maxDays": 21},
  "rankingCostUsd": 3554,
  "rankingDurationDays": 21,
  "rankingPolicy": "conservative-upper-bound-v1",
  "attributionLabel": "Shipping estimate powered by Freightos",
  "attributionUrl": "https://ship.freightos.com",
  "requestFingerprint": "64位十六进制摘要",
  "responseSha256": "64位十六进制摘要",
  "missingData": [],
  "assumptions": [
    "Freightos 公共市场 Beta 数据仅为市场参考估算，不是承运人报价或运输承诺"
  ],
  "cacheStatus": "miss"
}
```

状态语义：

| 状态 | 含义 |
|---|---|
| `available` | 成本区间和时效区间都可用 |
| `partial` | 只有成本或只有时效可用 |
| `unsupported` | Freightos 公共市场不支持该货型，例如 RoRo |
| `unavailable` | 无缓存且 Provider、限流或熔断当前不可用 |
| `expired` | 原缓存已过期且刷新失败；不会返回旧区间 |

接口不会生成 `mostLikely`。现有推荐算法需要标量时，只能使用：

```text
rankingCostUsd = costRange.max
rankingDurationDays = transitTimeRange.maxDays
rankingPolicy = conservative-upper-bound-v1
```

前端仍必须展示原始区间和 Freightos 署名，不能只展示排序标量。

### 5.1 HTTP 状态冻结

| HTTP 状态 | 语义 |
|---|---|
| `200` | 业务状态通过响应 `status` 区分 `available/partial/unsupported/unavailable/expired` |
| `401` | `X-Route-Estimate-Token` 缺失或错误 |
| `413` | 请求体超过 16 KiB |
| `422` | JSON、重复/未知字段、货物边界或受审计港口身份无效 |
| `503` | Production 认证未配置，或只读地点注册表不可用 |

冻结文件：

- `contracts/route-estimates-v1/openapi.json`
- `contracts/route-estimates-v1/request.schema.json`
- `contracts/route-estimates-v1/response.schema.json`
- `tests/fixtures/route_estimates_v1/available.json`
- `tests/fixtures/route_estimates_v1/partial.json`
- `tests/fixtures/route_estimates_v1/unsupported.json`
- `tests/fixtures/route_estimates_v1/unavailable.json`
- `tests/fixtures/route_estimates_v1/expired.json`

## 6. 缓存、限流和熔断

v1 使用进程内 TTL 缓存和请求指纹去重：

- 同一进程、同一规范请求在 TTL 内只调用一次 Provider；
- 并发相同请求通过单飞锁合并；
- 每个进程最多消耗 100 次/小时的上游调用预算；
- 连续失败达到阈值后暂时熔断；
- TTL 到期后不会返回陈旧区间。

这是 Development 和单实例部署的降级实现，不是生产分布式缓存。多实例 Render 服务会各自计数和缓存。生产扩容前应使用 Redis 等共享缓存、集中限流和分布式单飞锁。

## 7. 与现有推荐算法的关系

v1 保持现有规则不变：

1. `CostObservation` 的 `historical/observed/quoted/contracted` 继续优先。
2. `DelayObservation` 的 `historical/observed` 继续优先。
3. Freightos 公共结果是 `provider estimate`，不得写成 `quoted`、`contracted` 或 `observed`。
4. v1 不把一次货物请求写入 Neo4j，也不永久修改路线腿。
5. Freightos 失败不代表 Neo4j 失败；地点注册表不可用和 Provider 不可用使用不同 HTTP/响应语义。
6. 当前 `/api/routes/recommend` 尚未自动调用 Freightos，仍按原有观测优先与内部 fallback 工作。
7. Freightos 返回的是整条港到港查询的公共市场区间，不是 `RouteSegment` 观测，也不是某条候选航线、船舶、航次或承运人的专属价格。

## 8. v2 服务端推荐接入教程

v2 只能从服务端发送 `X-Route-Estimate-Token`。该 Token 不得进入浏览器 JavaScript、`VITE_*`、`NEXT_PUBLIC_*`、URL、日志、错误响应、推荐快照或前端网络请求。浏览器应调用 v2 自己的受控后端，由 v2 后端再调用本接口。

建议 v2 连接超时为 5 秒、总请求超时为 20 秒。v2 不应在自身再无界重试；本服务内部已经执行有限重试、限流与熔断。

后续 v2 应在推荐请求生命周期内执行，不写 RouteSegment：

1. 先按现有图和风险算法生成候选路线。
2. 保留已有可信 `CostObservation`/`DelayObservation` 的路线，不调用 Freightos 覆盖。
3. 只对缺少可信成本/时效、且包含可精确解析港到港海运腿的候选调用内部 `RouteEstimateService`。
4. `shipmentMethod=roro` 直接保留内部 fallback 或 `unavailable`，不得改成 container。
5. 将 `costRange`、`transitTimeRange`、排序上限、Provider、指纹、响应摘要、获取时间和缓存到期时间只放入本次推荐响应及审计快照。
6. 用 `rankingCostUsd` 和 `rankingDurationDays` 参与该次候选排序，同时向前端保留完整区间。
7. Provider 失败时继续使用当前内部估算；不得映射成 AuraDB 故障。
8. 增加候选数量预算，避免一次推荐请求耗尽每小时 100 次限额。

v2 输入继续使用本页 v1 请求结构；输出可以把本页完整响应嵌入推荐路线的 `externalEstimate` 字段，不应伪装成现有 `mostLikely`。

## 9. 测试与一次性真实验证

常规测试只使用脱敏 fixture：

```bash
python -m pytest -q tests/test_freightos_route_estimates.py
```

如果需要做一次公共端点验证，应使用经过审核的规范 UN/LOCODE 和非敏感测试货物，并确保本小时额度允许。不得在命令或日志中加入任何服务 Token。成功标志是 HTTP 200、可解析 JSON，以及响应中存在 `estimatedFreightRates`；这不代表已取得真实报价。
