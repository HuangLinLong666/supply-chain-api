# 前端 API 接入文档


## 1. 基础地址

```text
API_BASE_URL=https://supply-chain-api-kyiy.onrender.com
Swagger=https://supply-chain-api-kyiy.onrender.com/docs
```

所有业务请求都使用 JSON。前端不得接触 AuraDB 密码、AISStream API Key 或任何后端管理令牌。

## 2. 前端主流程

路线推荐页面按以下顺序调用：

```text
1. GET  /api/suppliers
2. GET  /api/suppliers/{supplier_id}/origins
3. GET  /api/cities
4. POST /api/routes/recommend
```

| 页面操作 | 使用接口 | 作用 |
|---|---|---|
| 选择供应商 | `GET /api/suppliers` | 获取供应商下拉框 |
| 选择供应商起点 | `GET /api/suppliers/{supplier_id}/origins` | 只返回该供应商能够使用的出发地点 |
| 选择终点 | `GET /api/cities` | 获取路线网络中的可选地点 |
| 查询推荐路径 | `POST /api/routes/recommend` | 返回多条完整候选路径并完成排序 |
| 查看历史推荐 | `GET /api/recommendations/{snapshotId}` | 回读一次完整推荐结果 |
| 查看单条路线 | `GET /api/routes/{routeId}` | 回读某条候选路线 |

## 3. 地点 ID 约定

所有地点统一使用 `location-id-v2`：

```text
PORT-CNSHG                上海港
PORT-DEHAM                汉堡港
AIR-PVG                   上海浦东机场
RAIL-CN-ALASHANKOU        阿拉山口铁路场站
```

前端规则：

- 下拉框展示 `name`，`value` 使用 `locationId`。
- 推荐请求的 `origin`、`destination` 必须使用接口返回的 `locationId`。
- 不要根据城市名自行拼接 ID，也不要拆分或改写 ID。
- `CN-SHA` 等旧 ID 仅供后端兼容；前端新代码、URL 和缓存只保存新版 ID。
- `routes[].legs[].from.id` 和 `to.id` 也是新版地点 ID，不是 Neo4j 内部 ID。

国家也使用统一字段：`countryCode` 是 ISO 3166-1 alpha-2 稳定值，`country` 是 CLDR 英文显示名，`countryNameZh` 是 CLDR 简体中文显示名。前端筛选、缓存和关联使用 `countryCode`，中文页面展示 `countryNameZh`，不要使用 `America`、`USA` 等别名作为主键。

完整规则见 `docs/location_id_naming.md`。

## 4. 供应商与地点接口

### 4.1 获取供应商

```http
GET /api/suppliers?search=CATL&limit=100
```

作用：填充供应商搜索框或下拉框。

关键响应：

```json
{
  "count": 1,
  "suppliers": [
    {
      "id": "SUP-CATL",
      "name": "CATL",
      "city": "Ningde",
      "country": "China",
      "riskScore": 23.5,
      "riskStatus": "available"
    }
  ]
}
```

前端提交推荐请求时使用 `suppliers[].id` 作为 `supplierId`。

### 4.2 获取供应商可用起点

```http
GET /api/suppliers/SUP-CATL/origins
```

作用：供应商选定后，限制起点下拉框，避免提交该供应商无法出发的地点。

关键响应：

```json
{
  "count": 1,
  "origins": [
    {
      "id": "PORT-CNSHG",
      "locationId": "PORT-CNSHG",
      "name": "上海港",
      "city": "Shanghai",
      "country": "China",
      "countryCode": "CN",
      "countryNameZh": "中国",
      "locationType": "port",
      "lat": 31.23,
      "lng": 121.47,
      "locationIdVersion": "location-id-v2"
    }
  ]
}
```

前端使用 `origins[].locationId` 作为推荐请求的 `origin`。

### 4.3 获取可选地点

```http
GET /api/cities?search=Hamburg&limit=200
```

作用：填充起点或终点搜索框。该接口返回路线网络中实际可参与规划的 `TransportLocation`。

关键响应：

```json
{
  "count": 1,
  "cities": [
    {
      "id": "PORT-DEHAM",
      "locationId": "PORT-DEHAM",
      "name": "汉堡港",
      "city": "Hamburg",
      "country": "Germany",
      "countryCode": "DE",
      "countryNameZh": "德国",
      "locationType": "port",
      "lat": 53.54,
      "lng": 9.98,
      "coordinateStatus": "reference",
      "coordinateConfidence": 1.0,
      "locationIdVersion": "location-id-v2"
    }
  ]
}
```

`value` 只用于搜索或辅助展示，不是业务主键；提交时使用 `locationId`。

## 5. 路线推荐接口

### 5.1 提交推荐请求

```http
POST /api/routes/recommend
Content-Type: application/json
```

```json
{
  "supplierId": "SUP-CATL",
  "origin": "PORT-CNSHG",
  "destination": "PORT-DEHAM",
  "cargo": {
    "type": "finished_vehicle",
    "vehicleType": "electric_vehicle",
    "quantity": 1
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
    "maxDurationDays": 70,
    "allowedModes": ["road", "rail", "sea"],
    "avoidedZoneIds": [],
    "minDataCompleteness": 0.3,
    "requireKnownRisk": false,
    "maxHops": 12
  },
  "limit": 5,
  "autoReroute": true
}
```

### 5.2 策略

| `strategy` | 作用 |
|---|---|
| `min_risk` | 优先低风险 |
| `min_cost` | 优先低成本 |
| `fastest` | 优先短时效 |
| `balanced` | 使用后端默认综合权重 |
| `custom` | 使用前端传入的 `weights` |

只有 `strategy=custom` 时必须传 `weights`。若传入权重，`risk + cost + duration` 必须等于 `1`。

### 5.3 货物字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `type` | 是 | 例如 `finished_vehicle` |
| `vehicleType` | 否 | 例如 `electric_vehicle` |
| `quantity` | 是 | 整车或计费单位数量，最小为 1 |
| `grossWeightKg` | 否 | 总重量 |
| `vehicleLengthM/WidthM/HeightM` | 否 | 如填写则三项必须同时提供 |
| `shipmentMethod` | 否 | `roro` 或 `container` |
| `containerType` | 否 | 仅在 `shipmentMethod=container` 时使用 |

### 5.4 硬约束

| 字段 | 作用 |
|---|---|
| `maxRiskScore` | 排除综合风险超过阈值的路线 |
| `maxCostUsd` | 排除超预算路线 |
| `maxDurationDays` | 根据 P90 时效排除超时路线 |
| `allowedModes` | 允许 `road`、`rail`、`sea`、`air` |
| `avoidedZoneIds` | 用户指定必须避开的风险区 ID |
| `minDataCompleteness` | 风险数据最低完整度，范围 `0-1` |
| `requireKnownRisk` | 为 `true` 时排除风险未知的候选 |
| `maxHops` | 最大分段数，范围 `1-20` |

### 5.5 推荐响应

顶层关键字段：

```json
{
  "snapshotId": "recommendation_xxx",
  "scoringVersion": "...",
  "generatedAt": "2026-07-31T00:00:00Z",
  "resolvedWeights": {"risk": 0.5, "cost": 0.3, "duration": 0.2},
  "dynamicRouting": {
    "rerouted": true,
    "avoidedZones": ["suez-canal"],
    "fallbackUsed": false
  },
  "candidateCount": 6,
  "eligibleCount": 3,
  "count": 3,
  "rejectedCandidates": [],
  "routes": []
}
```

每个 `routes[]` 用于一张路线卡片：

| 字段 | 前端用途 |
|---|---|
| `id` | 路线唯一 ID，用于详情页和回读 |
| `rank` | 当前排名 |
| `name` | 路线名称 |
| `riskScore` | 综合风险 `0-100`，可能为 `null` |
| `cost` | 预估总成本 USD，可能为 `null` |
| `durationDays` | P50 预估时效 |
| `distanceKm` | 总距离 |
| `tags` | 例如“风险最优”“成本最优”“含海运” |
| `finalScore` | 最终排序分，越低越优 |
| `riskFactors` | 风险维度、Provider、证据和受影响分段 |
| `legs` | 路线地图分段 |
| `costEstimate` | 成本区间、置信度、组成和估算假设 |
| `durationEstimate` | P50/P90、移动、等待、海关和延误时间 |
| `scoreBreakdown` | 风险/成本/时效子分与加权贡献 |
| `whyRecommended` | 推荐理由 |
| `comparisonToNext` | 与下一名的差异 |
| `missingData` | 缺失的真实数据 |
| `estimatedFields` | 使用估算值的字段 |

地图使用 `legs[]`：

```json
{
  "id": "segment_xxx",
  "from": {
    "id": "PORT-CNSHG",
    "name": "上海港",
    "city": "Shanghai",
    "country": "China",
    "countryCode": "CN",
    "countryNameZh": "中国",
    "lat": 31.23,
    "lng": 121.47,
    "coordinateStatus": "reference"
  },
  "to": {
    "id": "PORT-SGSIN",
    "name": "新加坡港",
    "lat": 1.26,
    "lng": 103.84
  },
  "mode": "sea",
  "cost": 4945,
  "durationDays": 15,
  "distanceKm": 10100,
  "riskScore": 58,
  "geometry": {"type": "LineString", "coordinates": []}
}
```

有 `geometry` 时按 GeoJSON 画线；没有时可连接可信的端点坐标。`coordinateStatus=unavailable` 的坐标不能当作真实地点展示。

## 6. 推荐结果回读

### 6.1 回读整次推荐

```http
GET /api/recommendations/{snapshotId}
```

作用：刷新页面或进入历史记录时，恢复当时完整的请求、排序和候选路线。`snapshotId` 来自推荐响应顶层。

### 6.2 回读单条路线

```http
GET /api/routes/{routeId}
```

作用：进入路线详情页时读取单条候选路线。`routeId` 来自 `routes[].id`，不能用 `snapshotId` 代替。

## 7. 实时风险与数据新鲜度

### 7.1 Provider 状态

```http
GET /api/providers/status
```

作用：显示 GDELT、Open-Meteo 和 AISStream.io 是否可用、最后观测时间和数据是否过期。

前端必须区分：

- `available`：有可用 Provider 数据。
- `partial`：只有部分证据。
- `stale`：数据已过期。
- `unavailable`：没有可用数据。
- `null`：未知，不得显示成 0 分或默认 50 分。

### 7.2 风险新闻

```http
GET /api/risk/news?zone_id=suez-canal&limit=50
GET /api/risk/news/zones
GET /api/risk/news/clusters?zone_id=suez-canal&active_only=true&limit=50
```

| 接口 | 作用 |
|---|---|
| `/api/risk/news` | 新闻列表，包含标题、链接、严重度、类别和风险区 |
| `/api/risk/news/zones` | 各风险区的当前风险分、等级、置信度和有效期 |
| `/api/risk/news/clusters` | 去重聚类后的风险事件，适合前端事件卡片 |

### 7.3 港口天气

```http
GET /api/ports/weather-risks?min_score=40&page=1&page_size=50
GET /api/ports/weather-risks/high
GET /api/ports/PORT-CNSHG/weather
GET /api/ports/PORT-CNSHG/weather/history?page=1&page_size=50
```

| 接口 | 作用 |
|---|---|
| `/api/ports/weather-risks` | 港口天气风险排行和筛选 |
| `/api/ports/weather-risks/high` | 快速获取高天气风险港口 |
| `/api/ports/{port_id}/weather` | 某港口当前天气、海况和风险 |
| `/api/ports/{port_id}/weather/history` | 某港口历史天气风险快照 |

### 7.4 路线天气

```http
GET /api/routes/weather-risks?active_only=true&limit=100
GET /api/routes/weather-risks/{segment_id}
```

作用：查看沿路线采样后的天气风险。`segment_id` 来自推荐路线的 `legs[].id`。

### 7.5 AIS 港口流量和船舶

```http
GET /api/ais/targets
GET /api/ais/targets/{target_id}/traffic
GET /api/ports/PORT-CNSHG/traffic
GET /api/vessels/{mmsi}
```

| 接口 | 作用 |
|---|---|
| `/api/ais/targets` | 获取 AIS 观测海域，如上海、新加坡、鹿特丹和苏伊士 |
| `/api/ais/targets/{target_id}/traffic` | 获取观测海域的船舶数、速度、进出港和拥堵分 |
| `/api/ports/{port_id}/traffic` | 根据统一港口 ID 获取最新流量 |
| `/api/vessels/{mmsi}` | 查看某船的 MMSI、IMO、船名、船型、位置、速度、航向和目的地 |

## 8. 地图与风险区接口

```http
GET /api/geography/locations?status=reference&limit=200
GET /api/geography/zones?include_geometry=true
GET /api/geography/segments/{segment_id}
```

| 接口 | 作用 |
|---|---|
| `/api/geography/locations` | 查询地点坐标、坐标来源和置信度 |
| `/api/geography/zones` | 在地图上绘制红海、马六甲、苏伊士等风险区 |
| `/api/geography/segments/{segment_id}` | 获取单个分段的 GeoJSON 和经过的风险区 |

风险区几何只用于风险归属和可视化，不是航海、飞行或车辆导航数据。

## 9. 仪表盘可选接口

```http
GET /api/graph/summary
GET /api/risk/overview?limit=30
GET /api/risk/segments?limit=25&minimum_risk=0.3
GET /api/cost/segments?order=asc&limit=25
```

| 接口 | 作用 |
|---|---|
| `/api/graph/summary` | 显示图谱节点、关系和标签数量 |
| `/api/risk/overview` | 显示国家、港口和路段风险概览 |
| `/api/risk/segments` | 按综合风险排序路段 |
| `/api/cost/segments` | 按预估成本排序路段 |

这些接口适合仪表盘，不应替代 `POST /api/routes/recommend` 完成用户路线推荐。

## 10. 前端调用示例

```javascript
const API_BASE_URL = "https://supply-chain-api-kyiy.onrender.com";

export async function recommendRoutes(payload) {
  const response = await fetch(`${API_BASE_URL}/api/routes/recommend`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  const data = await response.json();
  if (!response.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
  }
  return data;
}
```

生产前端建议通过环境变量配置：

```text
NEXT_PUBLIC_API_BASE_URL=https://supply-chain-api-kyiy.onrender.com
```

## 11. 错误处理

| HTTP 状态码 | 含义 | 前端建议 |
|---|---|---|
| `400` | 起终点相同等无效请求 | 提示用户修改条件 |
| `404` | 供应商/地点/路线不存在，或没有可行路径 | 展示 `detail` 并允许重新选择 |
| `422` | 字段校验失败、权重不等于 1、起点不属于供应商 | 读取 `detail` 定位表单字段 |
| `500` | 后端内部错误 | 提示稍后重试，保留请求参数 |
| `503` | 数据库或外部服务暂不可用 | 显示服务暂不可用 |

Render 免费实例休眠后首次请求可能较慢，前端应显示 loading 并设置合理超时。

## 12. 前端必须遵守的数据规则

- `riskScore=null` 表示风险数据未知，不得转换为 `0` 或 `50`。
- `dataStatus=estimated` 表示内部估算，不得标记为实时报价。
- `stale` 或 `expiresAt` 已过期的数据需明确标记。
- 地图优先使用 `legs[].geometry`；端点缺少可信坐标时不要画成真实路线。
- 推荐列表按后端返回的 `rank` 排序，不要在前端重新计算综合分。
- `snapshotId` 标识整次推荐，`routes[].id` 标识单条路线，二者不能混用。
