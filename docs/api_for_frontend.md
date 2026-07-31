# FastAPI + Render 公网 API 交付说明

本文档说明如何使用你已经创建的 GitHub 专用仓库 `supply-chain-api` 部署公网后端 API，让前端同事通过 HTTP 接口读取 Neo4j AuraDB 中的供应链图谱数据。

> 本文档主要面向后端部署与联调。交给前端同事的纯接口文档见 `docs/frontend_api_reference.md`。

目标交付物不是 `localhost`，而是一个 Render 提供的公网地址，例如当前项目：

```text
https://supply-chain-api-kyiy.onrender.com
```

前端同事最终调用：

```text
https://supply-chain-api-kyiy.onrender.com/api/graph/summary
```

## 0. 阶段 10 前端交付结论

新前端的主业务流程只有四步：

```text
GET  /api/suppliers
GET  /api/suppliers/{supplier_id}/origins
GET  /api/cities
POST /api/routes/recommend
```

地点 ID 已统一为 `location-id-v2`。前端下拉框的 `value` 必须使用接口返回的 `locationId`，例如 `PORT-CNSHG`、`AIR-PVG`、`RAIL-CN-ALASHANKOU`，不要使用地点名称或自行拼接代码。旧 ID 仅作为后端兼容别名，完整规则见 `docs/location_id_naming.md`。

`GET /api/suppliers/{supplier_id}/origins` 现在只返回能够连接出发路段的规范地点；`origins[].id` 与 `origins[].locationId` 相同。`resolutionStatus` 用于说明旧供应商起点是否成功解析，前端不得再使用旧 `EntryPoint` 的 `code`。

推荐成功后，从响应直接取得：

```text
snapshotId = 整次推荐结果的审计 ID
routes[].id = 某条候选路线的 routeId
```

需要回读时调用：

```text
GET /api/recommendations/{snapshotId}
GET /api/routes/{routeId}
```

`GET /api/supply-chain/routes`、`GET /api/routes/optimize`、`GET /api/routes/recommendations` 和旧 `GET /api/routes/recommend` 是兼容或诊断接口，不应作为新推荐页面的主链路。`/api/v1/*` 主要用于采集、生成、审核和配置管理，也不是普通用户页面的首选接口。

前端还应调用 `GET /api/providers/status` 展示数据新鲜度。任何 `null`、`unavailable` 或 `stale` 都表示当前没有可用 Provider 证据，不得转换成 `0` 或 `50`。

### 0.1 前后端地点 ID 契约

| 用途 | 前端取值 | 前端展示 | 提交给后端 |
|---|---|---|---|
| 供应商 | `/api/suppliers` 的 `suppliers[].id` | `suppliers[].name` | `supplierId` |
| 供应商起点 | `/api/suppliers/{supplier_id}/origins` 的 `origins[].locationId` | `origins[].name` | `origin` |
| 终点 | `/api/cities` 的 `cities[].locationId` | `cities[].name` | `destination` |
| 地图分段 | `routes[].legs[].from.id/to.id` | `from/to.name` | 无需再转换 |
| 港口天气/AIS | 港口的 `locationId` | 港口名称 | URL 中的 `{port_id}` |

前端应把 ID 当作不透明字符串：不拆分、不改写、不从名称推导。后端目前仍可解析 `CN-SHA`、`CNSHA` 等旧别名，但前端新代码、URL、缓存和持久化状态都应保存响应中的新 `locationId`。

## 1. 总体架构

```text
前端浏览器 / Next.js / React
  -> HTTPS 请求
  -> Render 上运行的 FastAPI 服务
  -> Neo4j Python Driver
  -> Neo4j AuraDB

AISStream.io WebSocket
  -> 独立后端 Background Worker
  -> Neo4j AuraDB
  -> FastAPI 把聚合结果返回前端
```

关键原则：

- 前端不能直接连接 AuraDB。
- AuraDB URI、用户名、密码只放在 Render 环境变量中。
- GitHub 仓库只放代码和 `.env.example`，不能放真实 `.env`。
- 前端只需要公网 API 地址和接口文档。
- `AISSTREAM_API_KEY` 只能放在后端 worker，不能放在前端或浏览器请求中。

## 2. GitHub 仓库 `supply-chain-api` 应该怎么配置

建议 `supply-chain-api` 是一个干净的后端仓库，只放 API 必需文件，不放 notebooks、dump、实验数据和外部仓库。

推荐目录结构：

```text
supply-chain-api/
  app/
    __init__.py
    main.py
  database/
    __init__.py
    neo4j_client.py
  scripts/
    verify_aura_connection.py
  docs/
    api_for_frontend.md
  .env.example
  .gitignore
  README.md
  requirements.txt
  render.yaml
```

从当前项目复制这些文件到 `supply-chain-api`：

```text
app/__init__.py
app/main.py
database/__init__.py
database/neo4j_client.py
scripts/verify_aura_connection.py
docs/api_for_frontend.md
.env.example
.gitignore
requirements.txt
ais/
config/ais_observation_targets.json
```

不应该复制：

```text
.env
exports/neo4j.dump
outputs/
SupplyGraph/
external_repos/
supplychain-dataset-gen/
*.ipynb
真实密码或密钥
```

## 3. `.gitignore`

`supply-chain-api/.gitignore` 至少包含：

```gitignore
.env
__pycache__/
.venv/
venv/
.DS_Store
*.pyc
```

如果你把本项目的 `.gitignore` 复制过去，也要确认 `.env` 已经被忽略。

## 4. `.env.example`

`supply-chain-api/.env.example` 放占位符，供本地开发参考：

```bash
AURA_NEO4J_URI=neo4j+s://your-aura-instance.databases.neo4j.io
AURA_NEO4J_USERNAME=neo4j
AURA_NEO4J_PASSWORD=your_aura_database_password
AURA_NEO4J_DATABASE=

API_CORS_ORIGINS=http://localhost:3000,http://localhost:5173,https://your-frontend-domain.com
```

注意：

- `.env.example` 可以提交 GitHub。
- `.env` 不可以提交 GitHub。
- `AURA_NEO4J_PASSWORD` 是 AuraDB 实例数据库密码，不是 Neo4j 官网登录密码。

## 5. `requirements.txt`

`supply-chain-api/requirements.txt` 至少需要：

```text
neo4j>=5.15,<7
python-dotenv>=1.0
fastapi>=0.115
uvicorn>=0.30
websockets>=12,<16
```

如果 API 后续需要数据处理，再添加 `pandas` 等依赖。部署仓库越轻越好。

## 6. FastAPI 服务说明

当前 API 入口：

```text
app/main.py
```

Neo4j AuraDB 连接层：

```text
database/neo4j_client.py
```

本地启动命令：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

本地调试页面：

```text
http://localhost:8000/docs
```

注意：`localhost` 只用于本机调试。给同事使用时，必须使用 Render 部署后的公网地址。

## 7. 本地验证流程

在 `supply-chain-api` 仓库本地创建 `.env`：

```bash
AURA_NEO4J_URI=neo4j+s://your-aura-instance.databases.neo4j.io
AURA_NEO4J_USERNAME=neo4j
AURA_NEO4J_PASSWORD=your_aura_database_password
AURA_NEO4J_DATABASE=
API_CORS_ORIGINS=http://localhost:3000,http://localhost:5173
```

安装依赖：

```bash
pip install -r requirements.txt
```

先验证 AuraDB 连接：

```bash
python scripts/verify_aura_connection.py
```

如果不想把密码写入 `.env`，可以让脚本交互输入：

```bash
python scripts/verify_aura_connection.py --prompt-password
```

成功输出应类似：

```text
AuraDB connection OK
RETURN 1 AS ok -> 1
node_count -> 1234
rel_count -> 5678
```

启动 API：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

测试：

```bash
curl http://localhost:8000/health
curl http://localhost:8000/health/aura
curl http://localhost:8000/api/graph/summary
```

### 风险、成本与路径推荐接口

```text
GET /api/risk/segments
GET /api/cost/segments
GET /api/routes/recommendations
GET /api/routes/nodes
GET /api/routes/optimize
POST /api/routes/recommend
GET /api/recommendations/{snapshot_id}
GET /api/routes/{route_id}
GET /api/suppliers
GET /api/suppliers/{supplier_id}/origins
GET /api/cities
GET /api/providers/status
GET /api/ports/{port_id}/traffic
GET /api/ais/targets/{target_id}/traffic
GET /api/vessels/{mmsi}
```

前端路径规划主接口已经改为 POST：

```bash
curl -X POST "http://localhost:8000/api/routes/recommend" \
  -H "Content-Type: application/json" \
  -d '{
    "supplierId":"SUP-CATL",
    "origin":"PORT-CNSHG",
    "destination":"PORT-DEHAM",
    "cargo":{"type":"finished_vehicle","vehicleType":"electric_vehicle","quantity":1},
    "strategy":"balanced",
    "weights":{"risk":0.5,"cost":0.3,"duration":0.2},
    "constraints":{
      "maxRiskScore":70,
      "maxCostUsd":30000,
      "maxDurationDays":50,
      "allowedModes":["road","rail","sea"],
      "maxHops":12
    },
    "limit":5,
    "autoReroute":true
  }'
```

推荐前先调用：

```http
GET /api/suppliers?search=CATL
GET /api/suppliers/SUP-CATL/origins
GET /api/cities?search=Shanghai
GET /api/cities?search=Hamburg
```

从上述响应中取值后，请求体对应关系为：

```javascript
const request = {
  supplierId: selectedSupplier.id,
  origin: selectedOrigin.locationId,
  destination: selectedDestination.locationId,
  strategy: "balanced",
  limit: 5
};
```

新接口会验证供应商的 `SHIPS_FROM` 起点。供应商存在但起点不属于它时返回 `422`，不会猜测映射。起点或终点不存在、或两者之间没有可行的有向路径时返回 `404`。

`GET /api/cities` 虽然保留了 `value`，但它是搜索/展示字段，不是业务主键。提交请求时只使用 `locationId`。

权重规则：

- 支持 `min_risk`、`min_cost`、`fastest`、`balanced`、`custom`；
- `risk + cost + duration` 必须等于 `1`；
- 先应用 `maxRiskScore`、`maxCostUsd`、`maxDurationDays`、运输方式、风险区和最大分段数等硬约束，再排序；
- `maxDurationDays` 使用 P90 时效判断，避免低估；
- 风险未知且设置 `maxRiskScore` 时，候选因无法验证而被拒绝。

响应顶层包含：

- `snapshotId`：本次推荐快照 ID；
- `scoringVersion`：评分版本；
- `resolvedWeights`：实际权重；
- `normalization`：固定归一化边界；
- `candidateCount`、`eligibleCount`、`count`；
- `rejectedCandidates`：未通过硬约束的路线和原因；
- `dynamicRouting`：是否避开有效期内的高新闻风险区域。

每条路线直接包含：

- `riskScore`：0-100 综合风险。
- `cost`：本次货物数量的预计总成本 USD。
- `durationDays`：P50 预计总时效。
- `distanceKm`：总距离。
- `tags`：成本最优、风险最优、时效最优及运输方式。
- `riskFactors`：前端风险进度条数据。
- `legs`：地图分段、GeoJSON、端点坐标、坐标来源、可行性和风险区交叉证据。
- `rank`、`finalScore`、`scoreBreakdown`：排名、子分、加权贡献和不确定性扣分。
- `costEstimate`：`min/mostLikely/max`、状态、置信度、公式和成本组成。
- `durationEstimate`：移动、等待、海关、中转、延误、P50/P90 和数据状态。
- `whyRecommended`、`comparisonToNext`：推荐解释和与下一名的差异。
- `missingData`、`estimatedFields`：缺失项与估算项。

`routes[].legs[].from.id` 和 `routes[].legs[].to.id` 也是 `location-id-v2`，例如 `PORT-CNSHG`。这些 ID 可直接用于地图节点关联、天气和 AIS 查询，不再是 Neo4j `elementId()`。

风险缺失时 `riskScore=null`、`scoreBreakdown.subScores.risk=null`。后端不会填 50；不确定性通过 `uncertaintyPenalty` 单独显示。前端也不得把 `null` 转成 0 或 50。

成本 fallback 会使用数量、距离、模式费率、装载方式、箱型、单车重量、单车尺寸、燃油、装卸和电动车估算附加项，但 `dataStatus=estimated`、`provider=null` 表示内部估算，不是实时报价。等待、海关和中转没有 Provider 时为 `null`。

回读本次结果：

```http
GET /api/recommendations/{snapshotId}
GET /api/routes/{routeId}
```

完整请求、字段和错误处理见 `docs/route_recommendation.md`。旧的 GET `/api/routes/recommend?...` 仍可用，但 Swagger 已标记 deprecated，新页面不要继续接入。

阶段 6 后，不要再按固定枚举判断 `coordinateSource`。它会返回真实来源名称，例如：

- `UN/LOCODE 2025-1`
- `OurAirports airports.csv`
- `GeoNames cities15000 city centroid`
- `city centroid from sourced graph nodes`

前端应主要检查：

- `coordinateStatus=reference`：注册表/机场参考坐标。
- `coordinateStatus=estimated`：城市中心点，只适合低精度展示。
- `coordinateStatus=unavailable`：不应画成真实地点。
- `coordinateConfidence`：`0-1` 置信度。

图邻居坐标估算已经删除，不会再把相邻节点的位置冒充当前节点位置。

Render 的 `API_CORS_ORIGINS` 应加入实际 Vercel 域名，例如：

```text
http://localhost:3000,https://your-project.vercel.app
```

调用动态路径优化前，先通过节点接口取得稳定的 `node_id`：

```bash
curl "http://localhost:8000/api/routes/nodes?search=Port&limit=20"
```

然后把返回的起点和终点 `node_id` 传给优化接口：

```bash
curl "http://localhost:8000/api/routes/optimize?origin_id=<起点ID>&destination_id=<终点ID>&objective=min_cost"
curl "http://localhost:8000/api/routes/optimize?origin_id=<起点ID>&destination_id=<终点ID>&objective=min_risk"
curl "http://localhost:8000/api/routes/optimize?origin_id=<起点ID>&destination_id=<终点ID>&objective=balanced&risk_weight=0.6"
```

`objective` 的含义：

- `min_cost`：累计运输成本最低。
- `min_risk`：累计综合风险权重最低。
- `balanced`：按 `risk_weight` 平衡标准化风险和标准化成本。

已有 `Route-HAS_SEGMENT->RouteSegment` 完整路线可直接排名：

```bash
curl "http://localhost:8000/api/routes/recommendations?objective=balanced&risk_weight=0.5&limit=10"
```

本地都正常后，再部署 Render。

## 8. Render 部署方式一：网页配置

这是最直观的方式，适合第一次部署。

### 8.1 创建 Web Service

1. 打开 Render 控制台：

```text
https://dashboard.render.com/
```

2. 点击 `New +`。
3. 选择 `Web Service`。
4. 连接 GitHub。
5. 选择仓库 `supply-chain-api`。
6. Runtime 选择 `Python`。

### 8.2 配置构建和启动命令

Build Command：

```bash
pip install -r requirements.txt
```

Start Command：

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

说明：

- Render 会通过 `$PORT` 注入服务端口。
- 不能写死 `8000` 作为 Render 生产端口。
- `--reload` 只用于本地开发，不要用于 Render 生产部署。

### 8.3 配置环境变量

在 Render 的 `Environment` 页面添加：

```text
AURA_NEO4J_URI=neo4j+s://your-aura-instance.databases.neo4j.io
AURA_NEO4J_USERNAME=neo4j
AURA_NEO4J_PASSWORD=<你的 AuraDB 数据库密码>
AURA_NEO4J_DATABASE=
API_CORS_ORIGINS=https://你的前端域名,http://localhost:3000,http://localhost:5173
```

如果前端同事还没有部署前端，可以先填本地开发地址：

```text
API_CORS_ORIGINS=http://localhost:3000,http://localhost:5173
```

等前端部署后，再补上：

```text
https://frontend-domain.com
```

### 8.4 部署

点击 `Create Web Service` 或 `Deploy`。

部署完成后 Render 会给你一个公网域名，例如：

```text
https://supply-chain-api-kyiy.onrender.com
```

## 9. Render 部署方式二：`render.yaml`

也可以在 `supply-chain-api` 根目录创建 `render.yaml`，把部署配置写进仓库。

```yaml
services:
  - type: web
    name: supply-chain-api
    runtime: python
    plan: free
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT
    envVars:
      - key: AURA_NEO4J_URI
        sync: false
      - key: AURA_NEO4J_USERNAME
        sync: false
      - key: AURA_NEO4J_PASSWORD
        sync: false
      - key: AURA_NEO4J_DATABASE
        sync: false
      - key: API_CORS_ORIGINS
        sync: false
```

`sync: false` 的意思是：变量名可以写进配置，但值仍然要在 Render 后台手动填写，避免密码进入 GitHub。

首次使用 Render Blueprint 时，选择这个仓库，Render 会读取 `render.yaml` 创建服务。

## 10. 部署后如何验证公网 API

假设 Render 地址是：

```text
https://supply-chain-api-kyiy.onrender.com
```

先测 API 服务：

```bash
curl https://supply-chain-api-kyiy.onrender.com/health
```

再测 AuraDB 连接：

```bash
curl https://supply-chain-api-kyiy.onrender.com/health/aura
```

再测图谱接口：

```bash
curl https://supply-chain-api-kyiy.onrender.com/api/graph/summary
curl "https://supply-chain-api-kyiy.onrender.com/api/suppliers?search=CATL"
curl "https://supply-chain-api-kyiy.onrender.com/api/providers/status"
```

也可以打开 Swagger 页面：

```text
https://supply-chain-api-kyiy.onrender.com/docs
```

这个地址可以发给前端同事和项目成员，用于查看接口结构和在线测试。

## 11. 给前端同事的 API_BASE_URL

你最终应该给前端同事一个公网基础地址：

```text
API_BASE_URL=https://supply-chain-api-kyiy.onrender.com
```

前端同事不要使用：

```text
http://localhost:8000
```

`localhost` 只代表他自己的电脑，不代表你的 Render 服务。

## 12. 通用与兼容查询接口

路径推荐页面优先使用第 0 节的四步主链路。下面的图谱、路段列表和概览接口适合仪表盘、调试或兼容旧页面。

### 12.1 健康检查

```http
GET /health
```

用途：确认 API 服务是否正常启动。

返回示例：

```json
{
  "status": "ok",
  "database": "neo4j",
  "uri_host": "your-aura-instance.databases.neo4j.io"
}
```

### 12.2 AuraDB 连接检查

```http
GET /health/aura
```

用途：确认 Render 后端能连上 AuraDB。

返回示例：

```json
{
  "status": "ok",
  "aura": "connected"
}
```

如果这个接口失败，说明问题在后端到 AuraDB 的连接，不是前端问题。

### 12.3 图谱总览

```http
GET /api/graph/summary
```

用途：用于首页仪表盘、图谱概览、节点/关系统计图。

返回示例：

```json
{
  "nodes": [
    {"labels": ["Supplier"], "count": 26},
    {"labels": ["RouteSegment"], "count": 586}
  ],
  "relationships": [
    {"type": "TRANSPORT", "count": 100},
    {"type": "HAS_RISK", "count": 50}
  ]
}
```

### 12.4 供应链路段兼容列表

```http
GET /api/supply-chain/routes
```

可选参数：

```text
limit=25
```

示例：

```http
GET /api/supply-chain/routes?limit=20
```

返回字段：

| 字段 | 说明 |
|---|---|
| `route_id` | 路线 ID。 |
| `segment_id` | 路线分段 ID。 |
| `sequence` | 分段顺序。 |
| `from_labels` | 起点节点标签。 |
| `from_properties` | 起点节点属性。 |
| `to_labels` | 终点节点标签。 |
| `to_properties` | 终点节点属性。 |
| `segment_properties` | 路线分段属性，包括成本、时间、距离、风险等字段。 |

### 12.5 风险概览

```http
GET /api/risk/overview
```

可选参数：

```text
limit=25
```

示例：

```http
GET /api/risk/overview?limit=30
```

返回包含：

| 字段 | 说明 |
|---|---|
| `counts` | 风险相关节点数量。 |
| `countries` | 国家风险属性。 |
| `ports` | 港口拥堵和等待时间属性。 |
| `route_segments` | 路线分段成本、时效和风险属性。 |

### 12.6 坐标、路线几何与风险区

```http
GET /api/geography/locations
GET /api/geography/zones
GET /api/geography/segments/{segment_id}
```

常用示例：

```bash
curl "$API_BASE_URL/api/geography/locations?status=reference&limit=200"
curl "$API_BASE_URL/api/geography/zones?include_geometry=true"
curl "$API_BASE_URL/api/geography/segments/leg_cn_sha_de_ham_sea_1"
```

单路段接口返回：

| 字段 | 说明 |
|---|---|
| `geometry` | GeoJSON `LineString`；没有可信几何时为 `null`。 |
| `geometrySource` | OSRM/OSM、searoute-py 或计算来源。 |
| `geometryStatus` | 真实状态，例如 `estimated_open_sea_network`、`estimated_endpoint_fallback`。 |
| `geometryConfidence` | 几何置信度。 |
| `feasibilityStatus` | `invalid_cross_ocean` 的公路/铁路不会进入路径推荐。 |
| `exposures` | 路线经过的风险区、交叉距离、暴露比例、方法和置信度。 |

风险区的 `geometry` 来自小比例尺公共数据，只用于风险归属和地图展示，不用于航海、飞行或车辆导航。

### 12.7 AIS 港口流量与船舶状态

```http
GET /health/ais
GET /api/providers/status
GET /api/ais/targets
GET /api/ais/targets/{target_id}/traffic
GET /api/ports/{port_id}/traffic
GET /api/vessels/{mmsi}
```

常用示例：

```bash
curl "$API_BASE_URL/health/ais"
curl "$API_BASE_URL/api/ports/PORT-CNSHG/traffic"
curl "$API_BASE_URL/api/ports/PORT-SGSIN/traffic"
curl "$API_BASE_URL/api/ais/targets/suez-canal/traffic"
curl "$API_BASE_URL/api/vessels/259000420"
```

港口返回示例：

```json
{
  "port": {
    "id": "PORT-CNSHG",
    "name": "上海港",
    "city": "Shanghai",
    "country": "China",
    "lat": 30.6333,
    "lng": 122.0667
  },
  "targetId": "port-shanghai",
  "targetName": "上海港附近海域",
  "traffic": {
    "status": "available",
    "active": true,
    "vesselCount": 38,
    "anchoredCount": 11,
    "averageSpeedKnots": 3.4,
    "arrivalCount": 4,
    "departureCount": 2,
    "congestionScore": 46.2,
    "confidence": 0.73,
    "dataCompleteness": 0.81,
    "provider": "AISStream.io",
    "observedAt": "2026-07-26T08:22:32Z",
    "expiresAt": "2026-07-26T09:52:32Z"
  }
}
```

上面的数字只用于解释响应结构，不是数据库当前实测值。真实 AIS 快照不存在时，接口返回 `traffic: null`；快照过期时返回 `status: stale` 且 `congestionScore: null`。前端必须展示“暂无数据/数据已过期”，不得自行填 `0` 或 `50`。

船舶响应中的 IMO、船名、船型、速度、目的地、吃水和导航状态都可能为 `null`，因为 AIS 位置报文不一定同时携带静态资料。

前端不需要、也不应获得 `AISSTREAM_API_KEY`。AIS worker 的部署步骤见 `docs/aisstream_port_traffic.md`。

## 13. 前端调用示例

### 13.1 fetch

```js
const API_BASE_URL = "https://supply-chain-api-kyiy.onrender.com";

export async function fetchGraphSummary() {
  const response = await fetch(`${API_BASE_URL}/api/graph/summary`);
  if (!response.ok) {
    throw new Error(`API error: ${response.status}`);
  }
  return response.json();
}
```

### 13.2 axios

```js
import axios from "axios";

const api = axios.create({
  baseURL: "https://supply-chain-api-kyiy.onrender.com",
  timeout: 15000,
});

export async function getRiskOverview() {
  const { data } = await api.get("/api/risk/overview", {
    params: { limit: 30 },
  });
  return data;
}
```

### 13.3 Next.js 环境变量

如果前端是 Next.js，前端仓库可以配置：

```bash
NEXT_PUBLIC_API_BASE_URL=https://supply-chain-api-kyiy.onrender.com
```

前端代码：

```ts
const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL;
const res = await fetch(`${apiBaseUrl}/api/graph/summary`);
const data = await res.json();
```

注意：这里只能放公网 API 地址。不能把 AuraDB 密码放进任何 `NEXT_PUBLIC_` 变量。

## 14. CORS 配置

如果前端浏览器报 CORS，需要在 Render 的环境变量里更新：

```text
API_CORS_ORIGINS=https://你的前端域名,http://localhost:3000,http://localhost:5173
```

更新后在 Render 里重新部署或重启服务。

本地开发时可以包含：

```text
http://localhost:3000
http://localhost:5173
```

前端上线后必须加入真实域名，例如：

```text
https://supply-chain-frontend.vercel.app
```

## 15. 前端展示建议

首页仪表盘：

- 调 `/api/graph/summary` 显示节点数量、关系数量、标签分布。
- 调 `/api/risk/overview` 显示国家风险、港口拥堵、路线风险排行。

路线推荐页面：

- 调 `/api/suppliers` 填供应商下拉框。
- 调 `/api/suppliers/{supplier_id}/origins` 限定该供应商可用起点。
- 调 `/api/cities` 填起终点下拉框。
- 下拉框展示 `name`，保存和提交 `locationId`；不要保存 `value`、旧别名或 Neo4j 内部 ID。
- 调 `POST /api/routes/recommend` 返回多条完整候选路线。
- 使用 `routes[].legs[].from/to.lat/lng` 和 `geometry` 画地图。
- 使用 `riskStatus`、`riskDataCompleteness`、`riskProviders`、`estimatedFields` 和 `missingData` 标明真实性。
- 使用 `GET /api/providers/status` 显示 GDELT、Open-Meteo 和 AIS 的最后更新时间。

图谱可视化页面：

- 当前 API 返回列表型 JSON。
- 后续可以新增专门给图谱组件使用的接口，例如：

```http
GET /api/graph/neighborhood?label=Supplier&id=CATL
```

返回：

```json
{
  "nodes": [],
  "edges": []
}
```

可直接对接 Cytoscape.js、ECharts Graph、Sigma.js 或 D3。

## 16. 常见问题

### 16.1 Render 部署成功，但 `/health/aura` 失败

优先检查 Render 环境变量：

- `AURA_NEO4J_URI` 是否是 `neo4j+s://...databases.neo4j.io`。
- `AURA_NEO4J_USERNAME` 是否是 `neo4j` 或 AuraDB 指定用户名。
- `AURA_NEO4J_PASSWORD` 是否是 AuraDB 数据库密码。
- `AURA_NEO4J_DATABASE` 是否与实例中的真实数据库一致；不确定时先留空使用默认数据库。
- AuraDB 实例是否处于 Running。

### 16.2 前端能打开 API 地址，但浏览器请求失败

大概率是 CORS。把前端域名加入 Render 环境变量：

```text
API_CORS_ORIGINS=https://前端域名
```

然后重启 Render 服务。

### 16.3 Render 免费服务第一次访问很慢

Render 免费实例可能会休眠。第一次请求需要等待服务唤醒。项目演示前建议先访问：

```text
https://supply-chain-api-kyiy.onrender.com/health
```

### 16.4 `/docs` 可以打开，但业务接口 503

说明 FastAPI 服务正常，AuraDB 查询失败。先测：

```text
https://supply-chain-api-kyiy.onrender.com/health/aura
```

再检查 Render 环境变量和 AuraDB 密码。

### 16.5 前端同事应该拿什么

给前端同事这些内容：

```text
API_BASE_URL=https://supply-chain-api-kyiy.onrender.com
Swagger 文档=https://supply-chain-api-kyiy.onrender.com/docs
供应商=GET /api/suppliers
供应商起点=GET /api/suppliers/{supplier_id}/origins
地点=GET /api/cities
路径推荐=POST /api/routes/recommend
Provider状态=GET /api/providers/status
```

不要给前端同事：

```text
AURA_NEO4J_PASSWORD
AURA_NEO4J_URI 的账号密码组合
.env
Neo4j Browser 登录密码
```

## 17. 最终交付检查清单

部署完成后逐项确认：

| 检查项 | 预期结果 |
|---|---|
| Render 服务状态 | Running |
| `/health` | 返回 `status: ok` |
| `/health/aura` | 返回 `aura: connected` |
| `/api/graph/summary` | 返回节点和关系统计 |
| `/api/cities` | 每个可选地点均返回 `locationId` 和 `locationIdVersion: location-id-v2` |
| `POST /api/routes/recommend` | 返回多条带风险、成本、时效和地图分段的候选路线 |
| `/api/providers/status` | 返回 Provider 配置、新鲜度和可用状态 |
| `/docs` | 能打开 Swagger 文档 |
| Render 环境变量 | 已配置 AuraDB 和 CORS |
| GitHub 仓库 | 没有 `.env`、dump、真实密码 |
| 前端调用地址 | 使用 Render 公网域名，不使用 `localhost` |

## 18. 配套文档

- 地点 ID 规则与旧 ID 映射：`docs/location_id_naming.md`
- 推荐请求与响应：`docs/route_recommendation.md`
- 评分公式与缺失数据：`docs/risk_scoring.md`
- 数据来源与真实性：`docs/data_sources.md`
- Render 和定时任务：`docs/deployment_and_scheduling.md`
- 当前数据库审计：`docs/current_backend_audit.md`
- 十阶段总览：`docs/十阶段项目更新总结.md`
