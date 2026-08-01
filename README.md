# 全球整车运输路径网络 API

本项目使用 FastAPI、Neo4j AuraDB、GDELT、Open-Meteo、AISStream.io 和可插拔 Provider，完成整车运输地点采集、候选路径生成、费用与风险评分、幂等入库、查询推荐和审计。天气、新闻和 AIS 数据都带 Provider、观测时间、失效时间与数据状态；缺失数据不会使用中性假分。

## 1. 你需要准备什么

- Python 3.12 或更高版本；
- 一个可用的 Neo4j AuraDB，或者本机 Docker；
- Git 与 VS Code；
- 可选的 AISStream.io API Key（只用于后端常驻 worker）；
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
AURA_NEO4J_DATABASE=
```

不要把 `.env` 提交到 GitHub。数据库名不确定时先留空使用实例默认库；若出现 `DatabaseNotFound`，不要默认填写 `neo4j`，应使用已经通过 `scripts/verify_aura_connection.py` 验证的数据库名。

可选 Provider 开关：

```dotenv
ENABLE_PROVIDER_MARINETRAFFIC=false
ENABLE_PROVIDER_OPENSKY=false
ENABLE_PROVIDER_AVIATION_EDGE=false
ENABLE_PROVIDER_FLIGHTAWARE=false
ENABLE_PROVIDER_CIRIUM=false
```

只有在配置对应 API Key 后再改为 `true`。未启用 Provider 会返回 `disabled`，不会导致整个任务失败。

AISStream.io 只在后端 worker 配置：

```dotenv
AISSTREAM_API_KEY=你的真实AISStream密钥
```

不要把该变量放进前端 `NEXT_PUBLIC_*` 或 `VITE_*` 环境变量。

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
- 路径推荐主接口：`POST http://127.0.0.1:8000/api/routes/recommend`

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

前端主接口：

```bash
curl -X POST "http://127.0.0.1:8000/api/routes/recommend" \
  -H "Content-Type: application/json" \
  -d '{
    "supplierId":"SUP-CATL",
    "origin":"Shanghai",
    "destination":"Hamburg",
    "cargo":{"type":"finished_vehicle","vehicleType":"electric_vehicle","quantity":1},
    "strategy":"balanced",
    "weights":{"risk":0.5,"cost":0.3,"duration":0.2},
    "constraints":{"allowedModes":["road","rail","sea"],"maxHops":12},
    "limit":5,
    "autoReroute":true
  }'
```

先用以下接口取得供应商、供应商允许的发货起点和城市：

```bash
curl "http://127.0.0.1:8000/api/suppliers?search=CATL"
curl "http://127.0.0.1:8000/api/suppliers/SUP-CATL/origins"
curl "http://127.0.0.1:8000/api/cities?search=Shanghai"
```

支持的排序策略：

- `min_risk`：最低风险；
- `min_cost`：最低费用；
- `fastest`：最快到达；
- `balanced`：使用默认风险、费用、时效权重；
- `custom`：必须提供自定义三目标权重。

三项权重之和必须等于 `1`。归一化使用 `config/recommendation_scoring.yaml` 中的固定锚点，不依赖本次候选最大值。缺失风险保持 `null`，不填 50；缺失和低置信度通过 `uncertaintyPenalty` 单独扣分。

响应中的 `snapshotId` 和 `routes[].id` 可用于回读：

```bash
curl "http://127.0.0.1:8000/api/recommendations/推荐快照ID"
curl "http://127.0.0.1:8000/api/routes/路线ID"
```

旧 `GET /api/routes/recommend` 仍可用，但已标记 deprecated。原 `/api/v1/routes/search` 和 `/api/v1/routes/{route_id}` 继续用于查询持久化的旧 `VehicleRoute`。新接口完整说明见 `docs/route_recommendation.md`。

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

审计历史估算路线是否存在跨大洋铁路、跨海公路等问题：

```bash
python scripts/audit_vehicle_route_feasibility.py
```

确认列表后执行软删除：

```bash
python scripts/audit_vehicle_route_feasibility.py --apply
```

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
python scripts/update_route_weather.py --dry-run
python scripts/update_route_weather.py
```

GDELT 全球航运新闻风险：

```bash
python scripts/update_gdelt_risk.py
```

GitHub 每小时任务位于 `.github/workflows/update-gdelt-risk.yml` 和 `.github/workflows/update-weather-risk.yml`。任务直接更新 AuraDB，本地 FastAPI 不需要持续开启。完整的新手配置见 `docs/deployment_and_scheduling.md`。

AIS 是持续 WebSocket，不是每小时 HTTP 拉取。准备与迁移：

```bash
python scripts/run_ais_consumer.py --check-config
python scripts/migrate_ais_stage7.py --execute --confirm APPLY_AIS_STAGE7
```

常驻 worker：

```bash
python scripts/run_ais_consumer.py
```

FastAPI 与 AIS worker 可以部署在不同服务，只要连接同一个 AuraDB；具体 Render 配置见 `docs/aisstream_port_traffic.md`。

## 17. 简化调度与生产调度

`app/vehicle_network/jobs.py` 提供 APScheduler 简化任务，适合单机验证。Render 免费 Web Service 会休眠，因此生产定时采集建议使用 GitHub Actions、Render Cron Job 或独立 Worker。

高并发生产环境可将 `LocationIngestionService` 和 `RouteGenerationService` 包装为 Celery task，并使用 Redis/RabbitMQ；服务函数已经通过 `job_id` 和 `trace_id` 设计为可独立调用。

## 18. 运行测试

```bash
python -m pytest -q
```

测试覆盖距离计算、费用区间、风险权重、混合排序、GDELT 聚类、天气沿线采样、AIS 解析与聚合、地理交叉、跨洋路线拦截、迁移幂等性、TTL 和 API 返回。以当前 `pytest -q` 输出数量为准。

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

### `Missing AISSTREAM_API_KEY`

只有 AIS worker 需要该变量。把 Key 配置在实际运行 `scripts/run_ais_consumer.py` 的本地终端、服务器或 Render Background Worker 中；不要发送给前端。

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
app/recommendation/   阶段 8 请求模型、成本时效 fallback、固定归一化、约束与推荐快照
ais/                  AISStream 解析、去重、聚合、Neo4j 持久化与查询 API
config/               风险权重、费率、GDELT 区域与地理参考快照
data/                 示例地点数据
cypher/               查询、审核、软删除示例
geography/            坐标、GeoJSON、空间交叉与 Neo4j 地理持久化
scripts/              命令行入口
tests/                单元测试
```

## 21. 阶段 3 统一 Neo4j 模型

统一迁移采用增量多标签方式，不删除旧节点、旧标签或旧关系：

```bash
python scripts/migrate_unified_schema.py --dry-run
```

确认 dry-run 没有 `blocking_conflicts` 后，才执行：

```bash
python scripts/migrate_unified_schema.py \
  --execute \
  --confirm APPLY_UNIFIED_SCHEMA_V1
```

迁移后的规范实体、字段、约束、实际数量和 Neo4j Browser 验证语句见 `docs/unified_neo4j_model.md`。迁移后的重复 dry-run 应显示节点更新、关系新增、缺失约束和缺失索引全部为 0。

## 22. 阶段 4 Provider 风险清理与重算

阶段 4 删除无 Provider、无 Evidence 且明确来自合成/派生来源的旧 `RiskFactor`，当时先按运输方式使用有效期内的 GDELT 与 Open-Meteo 信号重算；阶段 7 又为海运增加了同样受 TTL 与证据约束的 AISStream.io 港口拥堵因子：

```bash
python scripts/recalculate_provider_risk.py --dry-run
```

实际执行需要显式确认：

```bash
python scripts/recalculate_provider_risk.py \
  --execute \
  --confirm CLEAN_AND_RECALCULATE_PROVIDER_RISK_V1
```

风险缺失现在返回 `null` 和 `unavailable`，不会使用 `0.5/50` 中性默认值；路径排序会对未知风险施加显式不确定性惩罚。完整字段、实际迁移前后统计、备份位置和验证 Cypher 见 `docs/provider_risk_recalculation.md`。

## 23. 阶段 5 GDELT 与路线天气

阶段 5 将 GDELT 原始文章规范化、分类并按 48 小时窗口聚类，保留每一条原始 `NewsRiskEvent`，聚类后只对同一事件计分一次：

```bash
python scripts/migrate_gdelt_events.py
python scripts/migrate_gdelt_events.py \
  --execute \
  --confirm MIGRATE_GDELT_EVENTS_V3
```

Open-Meteo 路线天气优先按 `geometry_geojson` 采样；没有几何时只使用起终点并标记低置信度。估算几何不会伪装成已验证航线，合成、失效和地理不可行路线不会写入真实天气风险。

新增前端查询：

- `GET /api/risk/news/clusters`
- `GET /api/routes/weather-risks`
- `GET /api/routes/weather-risks/{segment_id}`

推荐接口会在读取时再次检查 Provider TTL；过期分数立即按 `unavailable` 处理，不等待下一次重算。算法、数据库实测结果和 GitHub Secrets 配置见 `docs/stage5_gdelt_weather_mapping.md`。

## 24. 阶段 6 坐标、路线几何与风险区

阶段 6 为 102 个 `TransportLocation` 保存可追溯坐标状态，为 10 个 `GeoZone` 建立 GeoJSON，并创建 69 条幂等 `PASSES_THROUGH` 记录；其中 65 条有效，4 条属于无效跨洋路线并已软停用：

```bash
python scripts/migrate_geospatial_data.py --dry-run
python scripts/migrate_geospatial_data.py \
  --execute \
  --confirm APPLY_GEOSPATIAL_STAGE6 \
  --enable-osrm
```

当前 89 个地点有来源坐标；5 条海运和 5 条公路有估算网络几何；4 条不合理跨洋公路/铁路标记为 `invalid_cross_ocean` 并从推荐图过滤。没有可信铁路 Provider 的路线只保留低置信度端点回退，不伪造铁路折线。

新增前端查询：

- `GET /api/geography/locations`
- `GET /api/geography/zones`
- `GET /api/geography/segments/{segment_id}`

数据源、许可证、UN/LOCODE 兼容修正、实际 AuraDB 统计、执行步骤和验证 Cypher 见 `docs/stage6_geospatial_risk_zones.md`。

## 25. 阶段 7 AIS 港口拥堵风险

阶段 7 新增独立后端消费者，并把订阅范围固定为上海港、新加坡港、鹿特丹港和苏伊士运河附近海域。无需提前提供 MMSI：worker 会按边界框接收位置和静态资料消息。

Neo4j 不长期保存全部高频坐标。每个 MMSI 只保留最新 `Vessel` 与一条覆盖更新的 `VesselObservation`，并按 60 分钟窗口生成幂等 `PortTrafficSnapshot`。AIS 拥堵继续作为独立观察信息返回，但当前三因子路线模型不把它加入 `riskScore`。

新增前端查询：

- `GET /health/ais`
- `GET /api/providers/status`
- `GET /api/ais/targets`
- `GET /api/ais/targets/{target_id}/traffic`
- `GET /api/ports/{port_id}/traffic`
- `GET /api/vessels/{mmsi}`

当前 AuraDB 已创建 4 个监测目标；旧 `DEMO CONTAINER` 观测保留审计但标记为 `synthetic + excluded`。未运行真实 worker 前，`PortTrafficSnapshot=0` 和 `traffic=null` 是正确状态，不会生成假拥堵分数。完整的新手部署、Render Background Worker、字段、算法和排错说明见 `docs/aisstream_port_traffic.md`。

## 26. 阶段 8 统一成本、时效与推荐

阶段 8 新增 `POST /api/routes/recommend`。当前评分版本为 `route-recommendation-v1.3-three-factor`；接口支持货物数量、五种策略、三目标权重、风险/成本/P90 时效/运输方式/风险区等硬约束，并在约束筛选后稳定排序。路线风险仅使用战争、自然灾害、关税/政策三个 Provider 支持的因子。

成本优先读取数量匹配且带 Provider 的 `CostObservation`；否则运行明确标为 `estimated` 的每车公里费率 fallback。时效优先读取可验证观测；否则返回估算 P50/P90，等待、海关和中转没有 Provider 时保持 `null`。

每次请求使用 `MERGE` 写入 `RecommendationSnapshot`，并建立：

```text
(RouteSegment)-[:INCLUDED_IN]->(RecommendationSnapshot)
```

新增查询：

- `GET /api/suppliers/{supplier_id}/origins`
- `GET /api/recommendations/{snapshot_id}`
- `GET /api/routes/{route_id}`
- `GET /api/methodology`

旧 `GET /api/routes/recommend` 保留并在 OpenAPI 标记 deprecated。算法、请求示例、约束语义、响应字段和当前 AuraDB 验证结果见 `docs/route_recommendation.md`；评分公式见 `docs/risk_scoring.md`。

## 27. 阶段 9 测试与真实数据验证

阶段 9 新增与 15 条验收标准一一对应的测试，覆盖跨洋铁路/公路过滤、Provider 风险、天气排序、GDELT 绕行、AIS 可用与缺失语义、四种策略、供应商起点、节点元数据、风险来源、清理/导入幂等性和新旧 OpenAPI。

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/validate_stage9_data.py
# 或使用当前激活环境
make validate-stage9
```

阶段 9 当时全量结果为 `152 passed`；阶段 10 新增 OpenAPI 审计回归测试后，以当前 `pytest -q` 输出为准。AuraDB 只读验证为 6 项通过、3 项警告、0 项失败；警告分别是 4 个供应商尚无 `SHIPS_FROM`、当前 GDELT 路线风险已过期、AIS 尚无真实快照。系统会把这些数据标成 unavailable，不会生成默认风险。完整矩阵和排错方法见 `docs/stage9_test_validation.md`。

## 28. 阶段 10 文档与交付

阶段 10 不执行数据库删除或业务数据写入，重点是把十个阶段形成可复现的新手交付包：

- 修复只读审计脚本的 OpenAPI 枚举方式，使延迟挂载的 v1 与 AIS 路由也进入接口清单；
- 刷新 `docs/current_backend_audit.md` 和 `artifacts/database_inventory.json`；
- 新增真实 Provider、参考数据、估算结果和许可证边界说明；
- 新增安全清理、Render、GitHub Actions、AIS worker 和全部环境变量教程；
- 把新前端主链路统一为供应商、起点、地点和 `POST /api/routes/recommend`；
- 生成十阶段更新、API、前端影响、剩余问题和路线图总览。

阶段 10 最终全量测试结果：`153 passed in 1.51s`。

核心文档：

| 文档 | 用途 |
|---|---|
| `docs/十阶段项目更新总结.md` | 十阶段总体交付、接口、影响和未来路线图 |
| `docs/current_backend_audit.md` | 当前 AuraDB 与 50 个 OpenAPI 操作的只读审计 |
| `docs/data_sources.md` | 真实 API、静态参考、估算数据和许可证边界 |
| `docs/risk_scoring.md` | 风险、成本、时效、完整度和推荐分公式 |
| `docs/route_recommendation.md` | 前端推荐请求、响应、routeId 和 snapshotId |
| `docs/database_cleanup.md` | dry-run、备份、限定删除和恢复注意事项 |
| `docs/api_for_frontend.md` | 前端公网 API 交付契约 |
| `docs/deployment_and_scheduling.md` | Render、GitHub 每小时任务和 AIS worker 配置 |

当前数据库数量会随每小时任务变化。重新生成实时快照：

```bash
python scripts/audit_current_backend.py
```
