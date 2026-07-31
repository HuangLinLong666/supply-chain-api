# 统一路径推荐接口（阶段 8—10）

> 接口版本：`route-recommendation-v1.2`  
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

## 2. 前端推荐调用顺序

### 第一步：查询供应商

```http
GET /api/suppliers?search=CATL
```

取得供应商 ID，例如 `SUP-CATL`。

### 第二步：查询供应商允许的起点

```http
GET /api/suppliers/SUP-CATL/origins
```

新 POST 接口不会只检查供应商名称。所选起点必须与供应商的 `SHIPS_FROM` 关系匹配；否则返回 `422`，避免无关供应商使用任意起点。

### 第三步：查询起终点

```http
GET /api/cities?search=Shanghai
GET /api/cities?search=Hamburg
```

起终点兼容地点 ID、旧别名、节点名称或城市；正式前端请求必须使用 `location-id-v2` 的 `locationId`，例如 `PORT-CNSHG` 或 `AIR-PVG`，避免同名地点歧义。完整规则见 `docs/location_id_naming.md`。

### 第四步：提交推荐请求

```bash
curl -X POST "http://localhost:8000/api/routes/recommend" \
  -H "Content-Type: application/json" \
  -d '{
    "supplierId": "SUP-CATL",
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

## 13. 阶段 10 前端接入模板

前端只配置 Base URL：

```text
NEXT_PUBLIC_API_BASE_URL=https://supply-chain-api-kyiy.onrender.com
```

推荐请求示例：

```ts
const response = await fetch(
  `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/routes/recommend`,
  {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      supplierId: "SUP-CATL",
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
