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

## 10. 最小脱敏阶段遥测纯 fixture 实施记录

批次：`freightos-route-estimates-v1-redacted-stage-telemetry-fixture-v1`

计划 SHA-256：
`e35bf7bacded7ae9435c7c118128e72b39e2d4d64ac167971f75268d1e33f5a9`

实施范围符合授权：新增 2 个文件、修改 4 个文件、删除 0 个文件。新增
`app/route_estimates/telemetry.py` 和 `tests/test_route_estimate_telemetry.py`；修改
`api.py`、`service.py`、`freightos.py` 以及本交接文档。

遥测仅记录 `request_received`、`authentication`、`location_resolution`、
`provider_cache`、`provider_call`、`response_mapping` 和 `completed` 阶段。每条新遥测
只有 `event`、`stage`、`outcome`、`elapsedBucket` 四个字段；阶段和结果均使用
固定枚举。不记录请求体、货物、Token、URL/URI、requestId、fingerprint、
错误文本/堆栈、Provider 响应或环境值。

Provider 失败只附加受限分类：`timeout`、`network`、`http_4xx`、`http_5xx`、
`invalid_response` 或 `unavailable`。固定上游、超时、重试、退避、禁止重定向、
响应大小、认证、地点查询、缓存、限流、熔断、HTTP 状态、响应契约、
署名和 fail-soft 业务语义均未改变。

写后 SHA-256：

- `app/route_estimates/api.py`：`175262538d4f1e86d475324b231726f708c7abcacfea075adcdb66a3398774f2`
- `app/route_estimates/service.py`：`25e8c9b397cb006117f3a9aef81cd5054365257d870280a948a233b119d50fe5`
- `app/route_estimates/freightos.py`：`8a86ae2286a7bc103c89345309d02ec9328f165e1e44f3d8856f2b532a676e7b`
- `app/route_estimates/telemetry.py`：`195a76ce3cd355859f379ec4fe3daee1616ced5b83f84ee1881ad1811ebc84ce`
- `tests/test_route_estimate_telemetry.py`：`5988716968c5f1e71d364a86ea8335485b8856ea10b21fd825c277ffb69bc41a`

验证结果：

- `python -m pytest -q tests/test_route_estimate_telemetry.py`：18 passed。
- `python -m pytest -q tests/test_freightos_route_estimates.py`：32 passed。
- `python -m pytest -q`：237 passed。
- `python -m compileall -q app`：首次因受控环境不允许在仓库
  `__pycache__` 写临时字节码而未执行完；将 `PYTHONPYCACHEPREFIX` 指向系统
  临时目录后等价复跑通过，没有产生仓库文件。
- `git diff --check`：通过。

本批测试使用 fake query、fake provider、`MockTransport`、`caplog` 和
fail-closed 网络保护。真实网络调用 0、数据库读写 0、部署 0、环境变量修改 0、
契约修改 0、v2 修改 0、提交 0、推送 0、停止生产请求 0、停止 timer 0。

本批只证明纯 fixture 阶段分类、时延分桶和脱敏输出已完成，不代表 v2、
supply-chain-api 或 Freightos 真实链路已联通。下一次 Development 真实请求必须使用
独立的禁止重放计划并重新获得明确授权。

## 11. HTTP 5xx 根因分类与零 Provider 健康诊断

- `GET /health/route-estimates` 只验证 route-estimates 配置、服务认证是否已配置，以及两个
  Demo 港口能否按 `location-id-v2` 唯一解析；Provider 固定为 `not_called`。
- 非 2xx 的 route-estimates 响应只通过 `X-Route-Estimate-Stage` 和
  `X-Route-Estimate-Outcome` 传递白名单诊断枚举，不返回错误详情或配置值。
- 配置错误、地点注册表不可用、服务初始化失败、响应校验失败和未捕获内部异常均被明确分类；
  Provider 的受控失败仍保持 HTTP 200 和 `unavailable/expired` 业务状态。
- 只为 `app.route_estimates.telemetry` 配置独立 INFO 输出，不启用 root 或整个
  `app.route_estimates` 的 INFO 日志，避免输出既有 requestId 或 fingerprint 日志。
- `render.yaml` 只声明所需变量名并使用 `sync: false`，不包含任何真实值。

## 12. 2026-09-06 invalid_response 根因细分授权前复核

状态：**授权前复核已完成，未修改实现；等待独立明确授权。**

已知脱敏阶段序列证明请求通过认证、地点解析和 Provider 缓存 miss，随后确实进入一次
Freightos Provider 调用，并在 Provider 响应解析阶段归类为 `invalid_response`。这排除了本次
故障属于 timeout、network、HTTP 4xx 或 HTTP 5xx，但现有单一枚举仍无法区分畸形
Content-Length、超大响应、非 JSON、缺少费率节点、运输方式不匹配、币种不匹配、成本/时效
区间非法或无可用估算等分支。记录中不保留 requestId、fingerprint、原始响应或货物数据。

2026-09-06 对 Freightos 官方 Shipping Estimates API 文档进行了只读核对。官方 JSON 成功示例
仍使用 `response.estimatedFreightRates.mode`、`mode.mode`、
`price.min/max.moneyAmount.amount/currency` 和 `transitTimes.min/max`，与当前成功解析路径一致；
接口仍标为 Beta。官方页面没有给出足以冻结兼容实现的 JSON “无结果/仅 warning”结构，因此
本轮不猜测、不新增宽泛兼容分支，也不把未知结构映射为可用估算。官方页面：
<https://ship.freightos.com/api/shippingCalculator>。

只读源码审计还发现：当前解析器在仅返回一个 mode 时，即使该 mode 不等于请求的 FCL/LCL，
也会以单项 fallback 接受。冻结候选将移除此 fallback，只允许精确请求 mode；不会把其他运输
方式当成请求结果。

基线验证：三个现有专项共 `63 passed`；临时目录内存模拟补丁与新 fixture 共 `80 passed`。
两个仓库的 `git diff --check` 均通过。真实网络调用 0、Provider 调用 0、数据库读写 0、部署
0、环境变量修改 0、提交 0、推送 0。临时模拟不构成仓库实现。

### 冻结实施计划

- batchId：`freightos-route-estimates-v1-invalid-response-reason-telemetry-fixture-v1`
- 计划 SHA-256：`a38937fceafd7a88dbbaac0ea8258821ffadb903af22c259c0dbe17af6c48cf8`
- 修改：`app/route_estimates/freightos.py`、`service.py`、`telemetry.py`
- 新增：`tests/test_freightos_invalid_response_reasons.py`
- 新增 1、修改 3、删除 0；模拟 diff 为 `+297/-16`
- 保持 endpoint、redirect 禁止、超时/重试、1 MiB 上限、认证、地点解析、缓存、限流、熔断、
  HTTP/响应契约、fail-soft、null 缺失值和署名语义不变。

写前 → 模拟写后 SHA-256：

- `freightos.py`：`8a86ae2286a7bc103c89345309d02ec9328f165e1e44f3d8856f2b532a676e7b` →
  `10cc1b11e90b0ac7a4b21480e83a68c90bf8803fbacca14d0ff5ae853500a7f7`
- `service.py`：`25e8c9b397cb006117f3a9aef81cd5054365257d870280a948a233b119d50fe5` →
  `c4d8e2a6e06b7e3c1699fd8b0ca65ad0f50e64a705d4bd40bc0dfb6b5c20be69`
- `telemetry.py`：`550f7fdbda91717af7f44eb9357c028474dec2fffbfebf9e64f595c0af503c12` →
  `7f366229d5d12e73863e14931a17157a664e03f9fa40b0265282b1e6de557e9c`
- 新测试：`a498f5bef8174f49de266f19826c62ad5aab9ad2113c6547389a291313688018`

规范单行 JSON（按键排序、无尾随换行计算 SHA-256）：

```json
{"batchId":"freightos-route-estimates-v1-invalid-response-reason-telemetry-fixture-v1","branch":"feature/freightos-route-estimates-v1","calls":{"databaseReads":0,"databaseWrites":0,"deployments":0,"provider":0,"realNetwork":0},"changes":["attach a bounded invalid_response_reason to FreightosProviderError","classify every existing invalid response branch without retaining provider data","emit provider_response_validation with the bounded reason before provider_call invalid_response","reject a returned mode that does not exactly match the requested FCL or LCL mode","add one fail-closed pure-fixture test module"],"contractChanges":0,"counts":{"add":1,"delete":0,"deletions":16,"insertions":297,"modify":3},"environmentChanges":0,"files":{"add":{"tests/test_freightos_invalid_response_reasons.py":"a498f5bef8174f49de266f19826c62ad5aab9ad2113c6547389a291313688018"},"delete":[],"modify":{"app/route_estimates/freightos.py":{"after":"10cc1b11e90b0ac7a4b21480e83a68c90bf8803fbacca14d0ff5ae853500a7f7","before":"8a86ae2286a7bc103c89345309d02ec9328f165e1e44f3d8856f2b532a676e7b"},"app/route_estimates/service.py":{"after":"c4d8e2a6e06b7e3c1699fd8b0ca65ad0f50e64a705d4bd40bc0dfb6b5c20be69","before":"25e8c9b397cb006117f3a9aef81cd5054365257d870280a948a233b119d50fe5"},"app/route_estimates/telemetry.py":{"after":"7f366229d5d12e73863e14931a17157a664e03f9fa40b0265282b1e6de557e9c","before":"550f7fdbda91717af7f44eb9357c028474dec2fffbfebf9e64f595c0af503c12"}}},"head":"a08e55c7e15bf7689969b2a7da70ae3cfebbba72","invalidResponseReasons":["redirect_response","invalid_content_length","response_too_large","non_json_response","invalid_root_object","missing_estimated_freight_rates","unsupported_rate_mode_shape","requested_mode_absent","currency_mismatch","invalid_cost_range","invalid_transit_range","no_usable_estimate","unknown_invalid_response"],"officialReview":{"checkedAt":"2026-09-06","compatibilityExpansionAuthorized":false,"documentedJsonNoResultShapeAvailable":false,"successShapeMatchesCurrentParser":true,"url":"https://ship.freightos.com/api/shippingCalculator"},"preserve":["fixed provider endpoint","redirect prohibition","timeout and retry policy","one MiB response limit","authentication","location-id-v2 resolution","cache rate-limit circuit-breaker","HTTP and route-estimates-v1 response contract","unavailable fail-soft behavior","missing values remain null","Freightos attribution","no Neo4j writes"],"recovery":"Reverse only this four-file patch and remove only the new test file; do not alter credentials, deployment state, databases, caches, contracts, or unrelated work.","repository":"supply-chain-api","tests":["PYTHON_DOTENV_DISABLED=1 python -m pytest -q -p no:cacheprovider tests/test_freightos_invalid_response_reasons.py","PYTHON_DOTENV_DISABLED=1 python -m pytest -q -p no:cacheprovider tests/test_freightos_route_estimates.py tests/test_route_estimate_telemetry.py tests/test_route_estimate_5xx_diagnostics.py","PYTHON_DOTENV_DISABLED=1 python -m pytest -q -p no:cacheprovider","PYTHONPYCACHEPREFIX=<git-ignored-temp-dir> PYTHON_DOTENV_DISABLED=1 python -m compileall -q app","git diff --check"]}
```

该计划只增加受限原因分类和严格 mode 匹配，不实施未经官方结构支持的兼容解析。未经下一轮
对该 batchId、计划 SHA、文件集合和 diff 的明确授权，不得应用补丁。
