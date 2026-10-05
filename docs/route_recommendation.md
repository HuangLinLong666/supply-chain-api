# 统一路径推荐接口（阶段 8—10）

> 接口版本：`route-recommendation-v1.3-three-factor`
> 主接口：`POST /api/routes/recommend`  
> 配置：`config/recommendation_scoring.yaml`、`config/vehicle_rates.yaml`

## 1. 阶段 8 解决了什么

旧 `GET /api/routes/recommend` 只能用 `risk_weight` 平衡风险和成本，也没有货物、时效权重、硬约束和快照。阶段 8 新增统一 POST 接口，并实现：

1. `min_risk`、`min_cost`、`fastest`、`balanced`、`custom` 五种策略；
2. 风险、成本、时效三项权重，权重之和必须等于 `1`；
3. 先过滤硬约束，再做稳定排序；
4. 使用固定、带版本号的归一化锚点，不依赖当前候选最大值；
5. 缺失风险保持 `null`，不填 `50`，不确定性单独扣分；
6. 成本和时效返回区间、状态、置信度、公式和输入快照；
7. 每次请求写入 `RecommendationSnapshot`，可回读当时的完整输入和结果；
8. 旧 GET 接口继续存在，但 OpenAPI 已标记为 `deprecated`。
9. 路线风险收敛为战争、自然灾害、关税/政策三个可审计因子；计算方法可通过 `GET /api/methodology` 读取。

## 2. 服务端推荐调用顺序

浏览器不应直接携带 `X-Route-Recommendation-Token` 调用本接口；由应用服务端持有 Token 并代理请求。

### 第一步：查询起终点

```http
GET /api/cities?search=Shanghai
GET /api/cities?search=Hamburg
```

`origin` 是路线规划的权威起点。起终点兼容地点 ID、旧别名、节点名称或城市；正式请求应使用 `location-id-v2` 的 `locationId`，例如 `PORT-CNSHG` 或 `AIR-PVG`，避免同名地点歧义。

### 第二步（可选）：查询供应商上下文

```http
GET /api/suppliers?search=CATL
```

只有需要供应商风险和 `SHIPS_FROM` 约束时才提供 `supplierId`。未提供时不会查询、猜测或创建供应商。

提供供应商时，再查询其允许的起点：

```http
GET /api/suppliers/SUP-CATL/origins
```

所选起点必须与真实 `SHIPS_FROM` 关系匹配；否则返回结构化 `422`。完整地点规则见 `docs/location_id_naming.md`。

### 第三步：提交推荐请求

```bash
curl -X POST "http://localhost:8000/api/routes/recommend" \
  -H "Content-Type: application/json" \
  -H "X-Route-Recommendation-Token: $ROUTE_RECOMMENDATION_TOKEN" \
  -d '{
    "origin": "Shanghai",
    "destination": "Hamburg",
    "cargo": {
      "type": "finished_vehicle",
      "vehicleType": "electric_vehicle",
      "quantity": 1,
      "shipmentMethod": "roro"
    },
    "strategy": "balanced",
    "weights": {
      "risk": 0.5,
      "cost": 0.3,
      "duration": 0.2
    },
    "constraints": {
      "maxRiskScore": 70,
      "maxCostUsd": 30000,
      "maxDurationDays": 50,
      "allowedModes": ["road", "rail", "sea"],
      "avoidedZoneIds": [],
      "maxHops": 12
    },
    "limit": 5,
    "autoReroute": true
  }'
```

如果当前没有可验证风险，而请求又设置了 `maxRiskScore`，该候选会被拒绝，因为系统无法证明它满足上限。若业务允许未知风险路线，可不设置 `maxRiskScore`，并通过返回的 `uncertaintyPenalty` 和 `missingData` 向用户提示。

## 3. 策略和权重

| `strategy` | 默认权重 | 用途 |
|---|---|---|
| `min_risk` | 风险 `1.0` | 有 Provider 的路线风险优先；未知风险不会获得风险子分 |
| `min_cost` | 成本 `1.0` | 预计总成本最低 |
| `fastest` | 时效 `1.0` | P50 预计时效最短 |
| `balanced` | 风险 `0.4`、成本 `0.3`、时效 `0.3` | 默认综合策略 |
| `custom` | 无默认值 | 必须提供自定义 `weights` |

任何策略都可以显式传入 `weights` 覆盖默认值。三项均需在 `0-1` 之间，且总和必须精确等于 `1`（允许 `1e-6` 浮点误差）。

## 4. 硬约束

| 字段 | 实际判断方式 |
|---|---|
| `maxRiskScore` | 路线风险必须已知且不超过上限 |
| `maxCostUsd` | `costEstimate.mostLikely` 不超过上限 |
| `maxDurationDays` | 为避免低估，使用 `durationP90Days` 判断 |
| `allowedModes` | 路径所有分段必须都在允许集合中 |
| `avoidedZoneIds` | 生成候选前移除经过指定 `GeoZone` 的分段 |
| `minDataCompleteness` | 推荐加权后的数据完整度不得低于下限 |
| `requireKnownRisk` | 为 `true` 时拒绝 `riskScore=null` 的路线 |
| `maxHops` | 限制一条路径的最大分段数 |

响应中的 `rejectedCandidates` 会列出被拒绝路线和原因。约束筛选完成后才计算最终排名。

## 5. 成本数据怎样计算

优先级如下：

1. 数量与请求完全一致、带 Provider、状态为 `historical/observed/quoted/contracted` 的 `CostObservation`；
2. 否则使用内部距离费率 fallback。

当前 AuraDB 的旧成本大多为 `synthetic/unavailable`，因此不会冒充报价。fallback 使用：

```text
距离 × 模式每车公里费率 × 数量
× 装载方式/箱型/重量/体积系数
+ 燃油附加费
+ 装卸费
+ 电动车内部估算附加费
+ 可选关税
```

海运可通过 `shipmentMethod=roro/container` 和 `containerType` 选择装载方式；当请求同时提供单车重量以及长、宽、高时，算法会把重量和体积纳入估算。费率来自 `config/vehicle_rates.yaml`，容量、装载方式、箱型、重量、体积和电动车估算参数来自 `config/recommendation_scoring.yaml`。返回值包括：

- `min`、`mostLikely`、`max`；
- `dataStatus`；
- `provider`；
- `confidence`；
- `formula`；
- `costComponents`；
- `missingComponents`；
- `inputSnapshot`。

`dataStatus=estimated` 且 `provider=null` 表示内部估算，不是承运人实时报价。保险、真实关税、港杂费和中转费没有 Provider 时会列入 `missingComponents`，不会偷偷填随机值。

## 6. 时效数据怎样计算

时效响应拆分为：

- `movementDurationDays`；
- `waitingDurationDays`；
- `customsDurationDays`；
- `transferDurationDays`；
- `expectedDelayDays`；
- `durationP50Days`；
- `durationP90Days`。

没有真实班期或延误 Provider 时，系统使用图中已有时效，或使用距离、模式平均速度和场站处理时间估算。等待、海关和中转数据没有 Provider 时返回 `null`。P90 通过版本化模式倍数估算，并明确标记为 `estimated`，不是承运人承诺。

## 7. 稳定归一化与不确定性

归一化边界固定在 `config/recommendation_scoring.yaml`：

```text
riskScore:             0 ～ 100
costPerVehicleUsd:     0 ～ 50000
durationDays:          0 ～ 120
```

每项转换为“越大越好”的 `0-100` 子分。超出边界会裁剪，不会用本次候选的最大值重新缩放，因此同一条路线不会仅因为另一条候选出现或消失而改变子分。

```text
baseScore = Σ(权重 × 子分)
uncertaintyPenalty = 100 × 0.20 × Σ(权重 × (1 - 该项置信度))
finalScore = max(0, baseScore - uncertaintyPenalty)
```

当风险缺失时：

- `riskScore=null`；
- `subScores.risk=null`；
- 风险项不获得效用分；
- 缺失造成的惩罚只显示在 `uncertaintyPenalty`；
- `whyRecommended` 会明确说明没有填入 50 分。

详细公式见 `docs/risk_scoring.md`。

前端需要展示公式时调用：

```http
GET /api/methodology?strategy=min_risk
GET /api/methodology?strategy=min_cost
GET /api/methodology?strategy=balanced
```

该接口只返回当前策略对应的用户可读公式，不返回模型版本、归一化边界或完整后端配置。

## 8. 关键响应字段

| 字段 | 用途 |
|---|---|
| `snapshotId` | 本次推荐审计 ID |
| `scoringVersion` | 评分版本 |
| `resolvedWeights` | 最终实际使用的权重 |
| `normalization` | 固定归一化方法和边界 |
| `candidateCount` | 约束前生成的去重候选数 |
| `eligibleCount` | 通过硬约束的候选总数 |
| `routes[].rank` | 排名 |
| `routes[].scoreBreakdown` | 子分、贡献、基础分、惩罚和最终分 |
| `routes[].costEstimate` | 成本区间、状态、公式和输入 |
| `routes[].durationEstimate` | P50/P90、拆分字段、状态和假设 |
| `routes[].whyRecommended` | 推荐原因和与下一名的差异 |
| `routes[].missingData` | 缺失的风险、成本和时效数据 |
| `routes[].estimatedFields` | 哪些值是估算 |
| `routes[].legs` | 地图分段、坐标、geometry、风险和分段成本时效 |

## 9. 如何用 ID 回读

读取整次推荐：

```http
GET /api/recommendations/{snapshotId}
```

读取某张路线卡片：

```http
GET /api/routes/{routeId}
```

`routeId` 来自 `routes[].id`，由有序分段 ID 的哈希稳定生成。当前结果保留 30 天，保留期配置位于 `snapshot.retention_days`。

AuraDB 中保存：

```text
(RouteSegment)-[:INCLUDED_IN]->(RecommendationSnapshot)
```

快照存储完整请求 JSON、权重、约束、评分版本、响应 JSON、候选数量和路线 ID。API Key、AuraDB 密码不会写入快照。

## 10. 旧 GET 接口

以下接口仍可供旧前端使用：

```http
GET /api/routes/recommend?supplier=CATL&origin=Shanghai&destination=Hamburg
```

它已在 Swagger 标为 deprecated。新前端必须改用 POST，才能使用货物数量、三目标权重、硬约束、成本/时效区间和推荐快照。

## 11. 常见错误

| 状态码 | 原因 | 处理方式 |
|---|---|---|
| `404` | 供应商、起点、终点或连通路径不存在 | 先查供应商、起点和城市接口 |
| `422` | 权重不等于 1 | 修正 `weights` |
| `422` | 起点不属于供应商 | 调用 `/api/suppliers/{supplier_id}/origins` |
| `200` 且 `routes=[]` | 图有路径，但全部被硬约束拒绝 | 查看 `rejectedCandidates`，不要盲目放宽约束 |
| `503` | AuraDB 查询或快照写入失败 | 检查 `/health/aura` 和 Render 日志 |

## 12. 当前 AuraDB 验证结果

阶段 8 实际调用已验证：

- `RecommendationSnapshot`：新增 3 个真实调用审计快照；
- `INCLUDED_IN`：新增 12 条分段到快照的关系；
- `scoring_version`：前 2 个开发期快照为 `v1.0`，最终验证快照为 `route-recommendation-v1.1`；
- 删除节点：0；
- 删除关系：0；
- 旧 GET OpenAPI：仍存在且标记 deprecated；
- 快照与 `routeId`：均可通过新增 GET 接口回读。

这 3 个快照是阶段 8 的 AuraDB 冒烟调用记录，不是运输 Provider 观测，也不会参与风险计算；它们会按 30 天 TTL 到期。

阶段 9 将当前代码版本提升到 `route-recommendation-v1.2`：Provider 缺失的旧风险值会在进入推荐前强制改为 unavailable，所有返回端点都会提供非空名称和 `coordinateStatus`。本次阶段 9 仅做只读 AuraDB 验证，没有新增快照；验证结果见 `docs/stage9_test_validation.md`。

当前版本已提升为 `route-recommendation-v1.3-three-factor`：路线风险只计算战争、自然灾害、关税/政策；三者都可能在达到阈值后触发跨运输方式自动改道。AIS 拥堵等旧维度继续保留独立观测接口，但不再进入路线综合风险。

## 12A. 2026-10-05 routing v3 结构化 4xx 错误契约授权前设计

本轮只读核查与纯 fixture 验证的结论为：**暂不能冻结实施批次**。原因是拟定七码中的
`invalid_constraints → 400` 尚没有与现有 FastAPI/Pydantic 422 和“有路径但约束后无候选仍返回
200”区分开的精确业务语义。不得把服务端配置 `ValueError` 误归类为客户端 400。

### 12A.1 当前真实行为

FastAPI `HTTPException` 的对外 envelope 是：

```json
{"detail":{"code":"<business-code>","message":"<safe-message>"}}
```

当前只有三个拟定码已经是结构化生产行为：

| code | status | 当前 envelope |
|---|---:|---|
| `supplier_not_found` | 404 | `detail.code` |
| `supplier_origin_unmapped` | 422 | `detail.code` |
| `supplier_origin_mismatch` | 422 | `detail.code` |

另外四个拟定码尚未成为真实契约：

| 拟定 code | 拟定 status | 当前行为 |
|---|---:|---|
| `origin_not_found` | 404 | 404 + `detail` 自由文本 |
| `destination_not_found` | 404 | 404 + `detail` 自由文本 |
| `no_candidates` | 404 | 404 + `detail` 自由文本（图不连通） |
| `invalid_constraints` | 400 | 请求模型约束失败为 422 + `detail[]`；引擎 `ValueError` 为 422 自由文本 |

`supplier_ambiguous` 是另一个现有结构化 422，但不属于本次七码白名单；下游必须对它
fail-closed，不得自动透传。

### 12A.2 OpenAPI 现状

`POST /api/routes/recommend` 当前 OpenAPI 只声明 `200` 和 FastAPI 自动的 `422`；没有为
400/404/422 业务错误声明统一受限模型。后续如实施，应新增类似以下的模型，并通过
route `responses` 精确声明 400、404 和业务 422：

```text
RouteRecommendationErrorDetail(code: Literal[七码], message: str)
RouteRecommendationError(detail: RouteRecommendationErrorDetail)
```

Pydantic/FastAPI 字段验证 422 仍使用标准 `HTTPValidationError`，不得伪装成业务
`invalid_constraints`。认证 401/503、数据库 503 和内部 5xx 也必须与七码模型分开。

### 12A.3 安全与兼容边界

- 固定 message 由服务端按 code 选择，不得返回 `ValueError` 文本、请求内容、节点标识、
  Token、URI、数据库细节或堆栈。
- 成功响应、评分、候选生成、约束后 `routes=[]` 的 200 语义、快照写入、认证和 Neo4j
  查询语义不得改变。
- `RecommendationEngine` 目前的 `ValueError` 包含缺失策略配置和非法归一化边界，
  这些是服务配置/内部故障，不能统一映射为 `invalid_constraints` 400。
- v2 后续只应解析有界 FastAPI `detail.code`，不应兼容没有真实契约证据的顶层
  `code`。

### 12A.4 纯 fixture 验证

- 无环境子进程中的 TestClient + fake graph/supplier 证明：三个供应商码为结构化
  `detail.code`；起点、终点、无路径为字符串；请求约束失败为 422 `detail[]`；
  OpenAPI responses 仅为 200/422。
- `tests/test_optional_supplier_recommendation.py`、`tests/test_stage8_recommendation.py`和
  `tests/test_stage9_acceptance.py` 在 socket fail-closed、不加载 dotenv 的子进程中 51/51 通过。
- 真实网络、Neo4j、Freightos、v2、Vercel 和其他第三方调用均为 0；数据库读写为 0。
  两个仓库外临时 fixture 均已删除。

### 12A.5 下一决策门

在冻结实施前，需明确以下两种语义之一：

1. **保持现有验证语义**：Pydantic/跨字段约束错误继续为标准 422，不生产
   `invalid_constraints`；v2 白名单缩减为六码或仅消费实际上游码。
2. **引入独立业务语义**：精确定义一个已通过 Pydantic、但在进入查询和评分前能被稳定判定的
   约束冲突，为它新建专用 domain exception，仅该异常映射为
   `invalid_constraints`/400。不得复用宽泛 `ValueError`。

未完成该产品/契约决策前，不生成规范 JSON、计划 SHA、模拟写后 SHA 或实施授权文本，
不修改代码或测试。

## 12B. 2026-10-05 routing v3 七类结构化 4xx 错误契约计划冻结

产品决策已经明确：`invalid_constraints` 当前只表示去重后的 `requiredModes` 数量大于
`maxHops`。这是不依赖图数据即可证明不可能满足的业务冲突；其他字段、范围和跨字段模型错误
继续由 FastAPI/Pydantic 返回标准 422。

兼容性复核没有发现 supply-chain-api 或 v2 消费者依赖起点、终点、无路径和引擎异常的自由
文本。v2 已从 FastAPI `detail.code` 读取上游白名单码，再生成自身的受限响应；不需要兼容上游
顶层 `code`。`supplier_ambiguous` 和 `ambiguous_location` 继续保持现有独立兼容行为，不进入七码。

冻结的 code/status/message 矩阵为：

| code | status | 固定 message |
|---|---:|---|
| `no_candidates` | 404 | `No feasible route candidates were found` |
| `origin_not_found` | 404 | `Origin was not found` |
| `destination_not_found` | 404 | `Destination was not found` |
| `supplier_not_found` | 404 | `Supplier was not found` |
| `supplier_origin_unmapped` | 422 | `Supplier has no shipping origin mapping` |
| `supplier_origin_mismatch` | 422 | `Origin is not linked to the supplied supplier` |
| `invalid_constraints` | 400 | `Required transport modes cannot be satisfied within maxHops` |

最小实施批次为 `routing-v3-seven-structured-4xx-error-contract-fixture-v1`：

- 修改 `app/main.py`；
- 新增 `app/recommendation/errors.py`；
- 新增 `tests/test_route_recommendation_error_contract.py`；
- 新增 2、修改 1、删除 0，模拟 diff 为 `+218/-25`；
- `InvalidRecommendationConstraints` 只由
  `len(set(requiredModes)) > maxHops` 触发；检查发生在认证之后、图读取之前；
- OpenAPI 声明结构化 400/404，以及标准 `HTTPValidationError` 与结构化业务错误的 422
  `oneOf`；
- 引擎宽泛 `ValueError` 改为固定通用 500，不返回异常文本；
- 成功响应、评分、路线生成、认证、Neo4j 查询、快照写入和 Provider 行为不变。

写前/模拟写后 SHA-256：

| 文件 | 写前 | 模拟写后 |
|---|---|---|
| `app/main.py` | `551a2e4753eaa164e09a73fd9039cfadc74e28d9eb54241c0c8deb588e79e84f` | `e40b9016f2f86d0087e100b81b6b77e4d4d15494c11e597ab3a136e552fd5579` |
| `app/recommendation/errors.py` | 不存在 | `10a0115471e720098f910ba759ed62b922e8ad33a4f003ad9624ef37ed1e12b4` |
| `tests/test_route_recommendation_error_contract.py` | 不存在 | `8f4abbd5031e921f21918195cd9e2f3d4349eba41fc96b083257fa1a85b8b53d` |

仓库外候选验证：扩展契约矩阵 71/71、完整测试 287/287、`compileall` 通过。当前工作区基线验证：
专项分别为 14/14、18/18、19/19，完整测试 272/272，`compileall` 和 `git diff --check`
通过。所有测试使用禁用 dotenv 和 socket fail-closed 隔离；真实网络、数据库、Provider、v2、
Vercel 调用及数据库读写均为 0。仓库外候选不作为生产修改。

规范单行 JSON：

```json
{"batchId":"routing-v3-seven-structured-4xx-error-contract-fixture-v1","allowedFiles":["app/main.py","app/recommendation/errors.py","tests/test_route_recommendation_error_contract.py"],"writeBeforeSha256":{"app/main.py":"551a2e4753eaa164e09a73fd9039cfadc74e28d9eb54241c0c8deb588e79e84f","app/recommendation/errors.py":null,"tests/test_route_recommendation_error_contract.py":null},"writeAfterSha256":{"app/main.py":"e40b9016f2f86d0087e100b81b6b77e4d4d15494c11e597ab3a136e552fd5579","app/recommendation/errors.py":"10a0115471e720098f910ba759ed62b922e8ad33a4f003ad9624ef37ed1e12b4","tests/test_route_recommendation_error_contract.py":"8f4abbd5031e921f21918195cd9e2f3d4349eba41fc96b083257fa1a85b8b53d"},"errorMatrix":{"no_candidates":[404,"No feasible route candidates were found"],"origin_not_found":[404,"Origin was not found"],"destination_not_found":[404,"Destination was not found"],"supplier_not_found":[404,"Supplier was not found"],"supplier_origin_unmapped":[422,"Supplier has no shipping origin mapping"],"supplier_origin_mismatch":[422,"Origin is not linked to the supplied supplier"],"invalid_constraints":[400,"Required transport modes cannot be satisfied within maxHops"]},"invalidConstraintsCondition":"len(set(requiredModes)) > maxHops","operations":["add dedicated bounded error contract and InvalidRecommendationConstraints","validate business constraints after authentication and before graph access","replace four free-text route errors and centralize three supplier errors","map engine ValueError to fixed generic 500","declare OpenAPI 400/404 and oneOf business-or-validation 422","add fail-closed TestClient contract matrix"],"fileCounts":{"added":2,"modified":1,"deleted":0},"diff":{"insertions":218,"deletions":25},"effects":{"realNetworkCalls":0,"databaseReads":0,"databaseWrites":0,"providerCalls":0,"successContractChanged":false}}
```

计划 SHA-256：
`c54dfaa09be29fed83e31f228b215c45b3cb5a7f16d29d84d442a4a1a2a2b284`。

回归命令为新增契约专项、三个既有推荐专项、完整 pytest、`compileall` 和
`git diff --check`。恢复仅回退 `app/main.py` 的本批 hunk，并删除本批新增的两个文件；不得
触及其他推荐、Freightos 或环境配置。实施必须等待下一轮对 batchId、计划 SHA、文件和精确
操作的明确授权。

## 12C. 2026-10-05 routing v3 七类结构化 4xx 错误契约实施结果

批次 `routing-v3-seven-structured-4xx-error-contract-fixture-v1` 已按冻结计划完成：修改
`app/main.py`，新增 `app/recommendation/errors.py` 和
`tests/test_route_recommendation_error_contract.py`；新增 2、修改 1、删除 0，实施内容 diff
为 `+218/-25`。

写后 SHA-256：

- `app/main.py`：`e40b9016f2f86d0087e100b81b6b77e4d4d15494c11e597ab3a136e552fd5579`；
- `app/recommendation/errors.py`：`10a0115471e720098f910ba759ed62b922e8ad33a4f003ad9624ef37ed1e12b4`；
- `tests/test_route_recommendation_error_contract.py`：
  `8f4abbd5031e921f21918195cd9e2f3d4349eba41fc96b083257fa1a85b8b53d`。

实际错误矩阵保持冻结值：`no_candidates`、`origin_not_found`、
`destination_not_found` 和 `supplier_not_found` 为 404；`supplier_origin_unmapped` 和
`supplier_origin_mismatch` 为 422；`invalid_constraints` 为 400。后者仍只由
`len(set(requiredModes)) > maxHops` 触发，并在认证完成后、图读取前判定。标准 Pydantic
422、认证 401/503、数据库和内部 5xx 保持独立；`supplier_ambiguous` 与
`ambiguous_location` 未进入七码。引擎 `ValueError` 返回固定通用 500，不再泄漏异常文本。

OpenAPI 已声明结构化 400/404，且 422 使用标准 `HTTPValidationError` 与结构化业务错误的
`oneOf`。成功响应、模型、引擎评分、候选生成、认证、Neo4j 查询、快照写入、缓存、timer 和
Provider 行为未修改；`app/recommendation/models.py` 与 `app/recommendation/engine.py`
保持原样。

在禁用 dotenv 和 socket fail-closed 条件下，验证结果为：错误契约专项 20/20、optional
supplier 14/14、Stage 8 18/18、Stage 9 19/19、完整测试 292/292；`compileall` 和
`git diff --check` 通过。真实网络、数据库、Provider、Freightos、v2 和 Vercel 调用为 0，
数据库读写为 0，部署为 0。

下一步必须分别取得新授权后，才能执行 v2 七码 fixture 消费复核或 supply-chain-api
Development 部署；本批未部署、未发送真实路线请求。

## 13. 阶段 10 服务端接入模板

应用服务端配置 Base URL 和 Token；禁止使用 `NEXT_PUBLIC_`、`VITE_` 或其他浏览器公开变量保存它们：

```text
SUPPLY_CHAIN_API_BASE_URL=https://supply-chain-api.example.com
SUPPLY_CHAIN_API_TOKEN=server-only-secret
```

推荐请求示例：

```ts
const response = await fetch(
  `${process.env.SUPPLY_CHAIN_API_BASE_URL}/api/routes/recommend`,
  {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Route-Recommendation-Token": process.env.SUPPLY_CHAIN_API_TOKEN,
    },
    body: JSON.stringify({
      origin: "Shanghai",
      destination: "Hamburg",
      cargo: {
        type: "finished_vehicle",
        vehicleType: "electric_vehicle",
        quantity: 1,
        shipmentMethod: "roro",
      },
      strategy: "balanced",
      constraints: {
        allowedModes: ["road", "rail", "sea", "air"],
        avoidedZoneIds: [],
        requireKnownRisk: false,
        maxHops: 12,
      },
      limit: 5,
      autoReroute: true,
    }),
  },
);

if (!response.ok) {
  throw new Error(`recommendation failed: ${response.status}`);
}

const result = await response.json();
```

页面应按后端返回的 `routes[].rank` 排序，不要在浏览器重新计算推荐分。注意：

- 原始 `riskScore`、`cost`、`durationDays` 都是越低越好；
- `finalScore` 是效用分，越高越好；
- `riskScore=null` 不是零风险；
- `costEstimate.provider=null` 表示内部估算，不是承运商报价；
- `coordinateStatus=estimated` 可以低精度画图，但要标记“估算坐标”；
- `geometryIsNavigational=false` 表示折线只供方案展示。

推荐请求成功后：

```ts
const snapshotId = result.snapshotId;
const routeId = result.routes[0]?.id;
```

这就是 `{snapshot_id}` 和 `{route_id}` 的来源，不需要前端自己生成。

## 14. 自动绕行的真实边界

`autoReroute=true` 不表示系统在任何情况下都一定能绕行。只有同时满足以下条件才可能改道：

1. 路段具有有效风险区暴露关系；
2. GDELT 风险仍在 TTL 内；
3. 风险分类与该运输方式语义匹配；
4. 图中存在不经过高风险区的可行替代路径；
5. 替代路径满足运输方式、成本、时效、风险和最大 hops 等硬约束。

如果没有替代路线，响应可能保留原路线、降低其排名或把它列入 `rejectedCandidates`。前端应读取：

```text
dynamicRouting.rerouted
dynamicRouting.avoidedZones
dynamicRouting.fallbackUsed
rejectedCandidates
```

当前数据库只有少量可审计路线几何，大部分路线仍是参考骨架，因此“全球任意起终点、多条真实运营路线”尚未完全实现。不要在 UI 中承诺已经覆盖所有城市和承运商。

## 15. 配套文档

- 数据真实性：`docs/data_sources.md`
- 风险与推荐公式：`docs/risk_scoring.md`
- 前端完整接口交付：`docs/api_for_frontend.md`
- 当前数据质量：`docs/current_backend_audit.md`
- 部署与每小时刷新：`docs/deployment_and_scheduling.md`
