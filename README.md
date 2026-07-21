# 全球整车运输路径网络 API

本项目使用 FastAPI、Neo4j AuraDB、GDELT、Open-Meteo 和可插拔 Provider，完成整车运输地点采集、候选路径生成、费用与风险评分、幂等入库、查询推荐和审计。现有天气、新闻、AIS 及供应链接口保持兼容；新增接口统一位于 `/api/v1`。

## 1. 你需要准备什么

- Python 3.12 或更高版本；
- 一个可用的 Neo4j AuraDB，或者本机 Docker；
- Git 与 VS Code；
- 可选的 GDELT、OpenSky、MarineTraffic、商业航班 API 凭证；
- 可选的中国民航机场 CSV。

项目不会清空数据库。地点、路线、路线腿、证据、风险和费用全部通过 `MERGE` 写入，删除路线默认采用软删除。

## 2. 安装依赖

```bash
cd "/Users/vegeta/全球供应链管理/supply-chain-api"
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

也可以执行：

```bash
make install
```

## 3. 配置 `.env`

复制模板：

```bash
cp .env.example .env
```

使用 AuraDB 时至少填写：

```dotenv
AURA_NEO4J_URI=neo4j+s://你的实例.databases.neo4j.io
AURA_NEO4J_USERNAME=neo4j
AURA_NEO4J_PASSWORD=你的密码
AURA_NEO4J_DATABASE=实际数据库名
```

不要把 `.env` 提交到 GitHub。若出现 `DatabaseNotFound`，不要默认填写 `neo4j`，应使用已经通过 `scripts/verify_aura_connection.py` 验证的数据库名。

可选 Provider 开关：

```dotenv
ENABLE_PROVIDER_MARINETRAFFIC=false
ENABLE_PROVIDER_OPENSKY=false
ENABLE_PROVIDER_AVIATION_EDGE=false
ENABLE_PROVIDER_FLIGHTAWARE=false
ENABLE_PROVIDER_CIRIUM=false
```

只有在配置对应 API Key 后再改为 `true`。未启用 Provider 会返回 `disabled`，不会导致整个任务失败。

## 4. 验证数据库连接

```bash
python scripts/verify_aura_connection.py
```

看到连接成功后再进行地点采集和路线生成。

## 5. 启动 API

```bash
uvicorn app.main:app --reload
```

浏览器访问：

- API 文档：`http://127.0.0.1:8000/docs`
- 整车网络健康检查：`http://127.0.0.1:8000/api/v1/health`
- 原有服务健康检查：`http://127.0.0.1:8000/health`

## 6. 使用 Docker 启动 Neo4j 和 API

```bash
docker compose up --build
```

- Neo4j Browser：`http://localhost:7474`
- FastAPI：`http://localhost:8000/docs`

示例 Docker 密码仅用于本地开发。生产环境必须修改 `docker-compose.yml` 中的密码。

## 7. 批量采集地点

命令行：

```bash
python scripts/ingest_vehicle_locations.py --countries CN,US,DE,BR,MX,AE
```

REST API：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/locations/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "country_scope":["CN","US","DE","BR","MX","AE"],
    "include_ports":true,
    "include_airports":true,
    "include_rail_terminals":true,
    "include_road_terminals":true,
    "force_refresh":false
  }'
```

默认示例 Provider 可离线导入 `data/sample_locations.json`。这些节点明确标记为 `fabricated_for_testing`、`is_inferred=true`、`confidence=0.2`，仅用于跑通流程，不代表正式注册表。正式 UN/LOCODE Provider 可按 `app/vehicle_network/providers/sample_registry.py` 的统一接口替换。中国民航 CSV 路径通过 `CAAC_AIRPORT_CSV` 配置。

## 8. 生成候选路径

先采集地点，再执行：

```bash
python scripts/generate_vehicle_routes.py --origin CN-LYG --destination US-LGB --strategy hybrid
```

仅预览、不写数据库：

```bash
python scripts/generate_vehicle_routes.py --origin CN-LYG --destination US-LGB --no-persist
```

API 示例：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/routes/generate \
  -H "Content-Type: application/json" \
  -d '{
    "origin":"CN-LYG",
    "destination":"US-LGB",
    "origin_kind":"port",
    "destination_kind":"port",
    "allow_multimodal":true,
    "max_transfers":3,
    "ranking_strategy":"hybrid",
    "prefer_observed_routes":true,
    "persist":true
  }'
```

没有真实时刻表时，系统使用球面距离与模式绕行系数生成估算路线，并写入：

```text
source_type=estimated_by_graph
is_inferred=true
review_status=pending
```

## 9. 查询推荐路径

```bash
curl "http://127.0.0.1:8000/api/v1/routes/search?origin=CN-LYG&destination=US-LGB&ranking_strategy=hybrid"
```

查询单条路线：

```bash
curl http://127.0.0.1:8000/api/v1/routes/vehicle_route_cn_lyg_us_lgb_sea_1
```

支持的排序策略：

- `min_risk`：最低风险；
- `min_cost`：最低费用；
- `fastest`：最快到达；
- `hybrid`：风险、费用、时效与置信度混合评分。

混合评分配置位于 `config/vehicle_strategy.yaml`：

```text
final_score =
  risk_weight × normalized_inverse_risk +
  cost_weight × normalized_inverse_cost +
  speed_weight × normalized_inverse_duration +
  confidence_weight × confidence
```

## 10. 修改风险权重和排序策略

查看配置：

```bash
curl http://127.0.0.1:8000/api/v1/config/strategy
```

可以直接编辑 `config/vehicle_strategy.yaml`，也可调用：

```bash
curl -X PUT http://127.0.0.1:8000/api/v1/config/strategy \
  -H "Content-Type: application/json" \
  -d '{
    "risk_weights":{"news_weight":0.25,"weather_weight":0.20,"congestion_weight":0.20,"sanctions_weight":0.25,"schedule_reliability_weight":0.10},
    "ranking_weights":{"risk_weight":0.40,"cost_weight":0.30,"speed_weight":0.20,"confidence_weight":0.10},
    "high_risk_threshold":60,
    "critical_risk_threshold":80,
    "default_ranking_strategy":"hybrid"
  }'
```

每一组权重之和必须等于 `1.0`。

## 11. 修改费用费率

编辑 `config/vehicle_rates.yaml`：

- `mode_rates_per_km`：公路、铁路、海运、空运每公里费率；
- `handling_fees`：港口、机场、铁路终端装卸费；
- `fuel_surcharge_ratio`：燃油附加费比例；
- `optional_tariff_rate`：可选关税比例；
- `uncertainty`：最低和最高费用区间系数。

每个 `CostEstimate` 都保留公式说明和输入参数快照，因此修改费率后仍可审计旧结果。

## 12. 审核路线

```bash
curl -X POST http://127.0.0.1:8000/api/v1/routes/路线ID/review \
  -H "Content-Type: application/json" \
  -d '{"review_status":"approved","reviewed_by":"你的名字","note":"已核对船公司网站"}'
```

推荐状态：

- `pending`：待审核；
- `approved`：已确认；
- `rejected`：不采用；
- `needs_changes`：需要修订。

## 13. 删除和恢复路线

软删除：

```bash
curl -X DELETE http://127.0.0.1:8000/api/v1/routes/路线ID -H "X-Actor: 你的名字"
```

该操作只设置 `deleted_at`，不会删除路线腿、证据和审计记录。恢复路线可在 Neo4j Browser 运行：

```cypher
MATCH (route:VehicleRoute {route_id:'路线ID'})
REMOVE route.deleted_at
SET route.route_status='candidate';
```

不要执行 `MATCH (n) DETACH DELETE n`。

## 14. 手工添加路线与来源

推荐流程：

1. 先通过 `/api/v1/routes/generate` 生成骨架；
2. 在 Neo4j 中补充承运人、船名、航班号、航次号和地图点；
3. 将 `source_type` 改为实际来源；
4. 调用 `/api/v1/audit/source` 添加证据；
5. 调用审核接口批准路线。

添加网上查询证据：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/audit/source \
  -H "Content-Type: application/json" \
  -d '{
    "entity_id":"路线ID",
    "entity_type":"route",
    "source":"船公司官方网站",
    "source_url":"https://example.com/schedule",
    "source_type":"manual_web_research",
    "confidence":0.8,
    "note":"人工核对的公开班期"
  }'
```

来源类型说明：

- 官方注册表：`official_registry`；
- 官方班期：`official_schedule`；
- 付费 API：`paid_api`；
- 开放 API：`open_api`；
- AIS 观测：`ais_observed`；
- 航班观测：`flight_observed`；
- 图算法估算：`estimated_by_graph`；
- 人工网页调查：`manual_web_research`；
- 用户创建：`user_created`；
- 仅测试编造：`fabricated_for_testing`。

`confidence` 建议：官方或已观测数据为 `0.8-1.0`，多源间接证据为 `0.6-0.8`，图算法估算为 `0.3-0.6`，自行编造的测试数据不高于 `0.2`。只要路线包含推断内容，就应设置 `is_inferred=true`。

## 15. 查看图数据库与审计

完整示例位于 `cypher/vehicle_transport_network.cypher`。查看路线：

```cypher
MATCH path=(origin)<-[:ORIGIN]-(route:VehicleRoute)-[:HAS_LEG]->(leg:RouteLeg)-[:TO_NODE]->(destination)
RETURN path
LIMIT 20;
```

查看任务：

```cypher
MATCH (job:IngestionJob)
RETURN job.job_id,job.job_type,job.status,job.trace_id,job.started_at,job.finished_at
ORDER BY job.started_at DESC;
```

## 16. 天气和新闻定时更新

天气：

```bash
python scripts/update_port_weather.py
```

GDELT 全球航运新闻风险：

```bash
python scripts/update_gdelt_risk.py
```

GitHub 每小时任务位于 `.github/workflows/update-gdelt-risk.yml`。详细教程见 `docs/gdelt_dynamic_route_risk.md`。

## 17. 简化调度与生产调度

`app/vehicle_network/jobs.py` 提供 APScheduler 简化任务，适合单机验证。Render 免费 Web Service 会休眠，因此生产定时采集建议使用 GitHub Actions、Render Cron Job 或独立 Worker。

高并发生产环境可将 `LocationIngestionService` 和 `RouteGenerationService` 包装为 Celery task，并使用 Redis/RabbitMQ；服务函数已经通过 `job_id` 和 `trace_id` 设计为可独立调用。

## 18. 运行测试

```bash
pytest -q
```

测试覆盖距离计算、费用区间、风险权重、混合排序、GDELT 和天气评分。

## 19. 常见报错

### `Missing AURA_NEO4J_URI`

当前运行目录没有 `.env`，或者变量名错误。确认在项目根目录运行命令。

### `Authentication failed`

AuraDB 密码错误。重置密码后需要同时更新本地 `.env`、Render 环境变量和 GitHub Actions Secrets。

### `DatabaseNotFound`

数据库名错误。使用验证脚本已经连接成功的数据库名。

### `地点不存在`

先调用 `/api/v1/locations/ingest`，或确认地点 ID 与 `TransportLocation.location_id` 一致。

### `起点或终点缺少经纬度`

路线估算必须有坐标。补充地点的 `latitude` 和 `longitude` 后重新生成。

### Provider 返回 `partial_success`

表示部分外部源失败，但其他 Provider 已成功写入。根据返回的 `failures` 和 `job_id` 排查，不需要删除成功数据。

### `429 Too Many Requests`

Provider 已读取 `Retry-After`；没有该响应头时采用指数退避和随机抖动。不要高频手动重复调用。

## 20. 目录说明

```text
app/vehicle_network/
  api.py              REST API
  core.py             配置加载
  models.py           Pydantic 数据模型
  providers/          数据源适配器
  repository.py       Neo4j 幂等写入与审计
  scoring.py          风险、费用和排序
  services.py         采集与路径生成编排
  jobs.py             APScheduler 简化任务
config/               风险权重、费率与 GDELT 区域
data/                 示例地点数据
cypher/               查询、审核、软删除示例
scripts/              命令行入口
tests/                单元测试
```
