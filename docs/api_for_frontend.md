# FastAPI + Render 公网 API 交付说明

本文档说明如何使用你已经创建的 GitHub 专用仓库 `supply-chain-api` 部署公网后端 API，让前端同事通过 HTTP 接口读取 Neo4j AuraDB 中的供应链图谱数据。

目标交付物不是 `localhost`，而是一个 Render 提供的公网地址，例如：

```text
https://supply-chain-api.onrender.com
```

前端同事最终调用：

```text
https://supply-chain-api.onrender.com/api/graph/summary
```

## 1. 总体架构

```text
前端同事的浏览器 / Next.js / React
  -> HTTPS 请求
  -> Render 上运行的 FastAPI 服务
  -> Neo4j Python Driver
  -> Neo4j AuraDB
```

关键原则：

- 前端不能直接连接 AuraDB。
- AuraDB URI、用户名、密码只放在 Render 环境变量中。
- GitHub 仓库只放代码和 `.env.example`，不能放真实 `.env`。
- 前端同事只需要公网 API 地址和接口文档。

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
AURA_NEO4J_DATABASE=neo4j

API_HOST=0.0.0.0
API_PORT=8000
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
AURA_NEO4J_URI=neo4j+s://94a63264.databases.neo4j.io
AURA_NEO4J_USERNAME=neo4j
AURA_NEO4J_PASSWORD=your_aura_database_password
AURA_NEO4J_DATABASE=neo4j
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
GET /api/routes/recommend
GET /api/suppliers
GET /api/cities
```

前端路径规划主接口：

```bash
curl "http://localhost:8000/api/routes/recommend?supplier=CATL&origin=Shanghai&destination=Hamburg&limit=5&risk_weight=0.5"
```

参数均支持名称；起点和终点也支持 `/api/cities` 返回的 ID。响应中的每条路线直接包含：

- `riskScore`：0-100 综合风险。
- `cost`：路线总成本 USD。
- `durationDays`：总时效。
- `distanceKm`：总距离。
- `tags`：成本最优、风险最优、时效最优及运输方式。
- `riskFactors`：前端风险进度条数据。
- `legs`：地图分段及端点坐标。

坐标的 `coordinateSource` 有三种取值：

- `database`：AuraDB 原始坐标。
- `city_estimate`：同城市已有节点坐标。
- `graph_neighbor_estimate`：根据相邻运输节点估算，仅用于地图展示。

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
AURA_NEO4J_URI=neo4j+s://94a63264.databases.neo4j.io
AURA_NEO4J_USERNAME=neo4j
AURA_NEO4J_PASSWORD=<你的 AuraDB 数据库密码>
AURA_NEO4J_DATABASE=neo4j
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
https://supply-chain-api.onrender.com
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
https://supply-chain-api.onrender.com
```

先测 API 服务：

```bash
curl https://supply-chain-api.onrender.com/health
```

再测 AuraDB 连接：

```bash
curl https://supply-chain-api.onrender.com/health/aura
```

再测图谱接口：

```bash
curl https://supply-chain-api.onrender.com/api/graph/summary
curl "https://supply-chain-api.onrender.com/api/supply-chain/routes?limit=20"
curl "https://supply-chain-api.onrender.com/api/risk/overview?limit=20"
```

也可以打开 Swagger 页面：

```text
https://supply-chain-api.onrender.com/docs
```

这个地址可以发给前端同事和项目成员，用于查看接口结构和在线测试。

## 11. 给前端同事的 API_BASE_URL

你最终应该给前端同事一个公网基础地址：

```text
API_BASE_URL=https://supply-chain-api.onrender.com
```

前端同事不要使用：

```text
http://localhost:8000
```

`localhost` 只代表他自己的电脑，不代表你的 Render 服务。

## 12. 当前可用接口

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
  "uri_host": "94a63264.databases.neo4j.io"
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

### 12.4 供应链路线样例

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

## 13. 前端调用示例

### 13.1 fetch

```js
const API_BASE_URL = "https://supply-chain-api.onrender.com";

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
  baseURL: "https://supply-chain-api.onrender.com",
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
NEXT_PUBLIC_API_BASE_URL=https://supply-chain-api.onrender.com
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

路线页面：

- 调 `/api/supply-chain/routes` 显示起点、终点、运输方式、估算成本、估算时效、风险分数。

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
- `AURA_NEO4J_DATABASE` 是否是 `neo4j`。
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
https://supply-chain-api.onrender.com/health
```

### 16.4 `/docs` 可以打开，但业务接口 503

说明 FastAPI 服务正常，AuraDB 查询失败。先测：

```text
https://supply-chain-api.onrender.com/health/aura
```

再检查 Render 环境变量和 AuraDB 密码。

### 16.5 前端同事应该拿什么

给前端同事这几项就够：

```text
API_BASE_URL=https://supply-chain-api.onrender.com
Swagger 文档=https://supply-chain-api.onrender.com/docs
图谱总览=GET /api/graph/summary
路线样例=GET /api/supply-chain/routes?limit=20
风险概览=GET /api/risk/overview?limit=20
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
| `/docs` | 能打开 Swagger 文档 |
| Render 环境变量 | 已配置 AuraDB 和 CORS |
| GitHub 仓库 | 没有 `.env`、dump、真实密码 |
| 前端调用地址 | 使用 Render 公网域名，不使用 `localhost` |
