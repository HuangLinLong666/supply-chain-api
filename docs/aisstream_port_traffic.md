# 阶段 7：AISStream.io 港口准实时拥堵风险

本文档面向第一次部署后端的使用者，说明如何配置、运行和检查 AISStream.io 消费器。阶段 7 已完成以下能力：

- 后端通过 WebSocket 连接 AISStream.io，API Key 不进入浏览器；
- 不要求提前知道 MMSI，按上海、新加坡、鹿特丹和苏伊士四个海域订阅；
- 解析 MMSI、IMO、船名、船型、位置、速度、航向、目的地、吃水和导航状态；
- Neo4j 每艘船只保存一条最新状态，不长期堆积全部高频坐标；
- 每个时间窗口生成 `PortTrafficSnapshot`；
- 真实、未过期的快照才会写入海运 `port_congestion` 风险；
- 提供 Provider、港口流量、监测区域和船舶查询接口；
- 支持重复消息过滤、异常消息跳过、批量限写、心跳和指数退避重连。

AISStream.io 官方说明它是 WebSocket API，连接地址为 `wss://stream.aisstream.io/v0/stream`，连接后需要尽快发送包含 `APIKey`、`BoundingBoxes` 和可选 `FilterMessageTypes` 的订阅消息。官方也明确不支持浏览器直接连接，因为这样会暴露 API Key。参考：

- <https://aisstream.io/documentation.html>
- <https://github.com/aisstream>

AISStream.io 仍是 Beta 服务且没有可用性 SLA，因此本项目会把断线和陈旧数据明确显示为 `degraded`、`unavailable` 或 `stale`，不会把缺失数据伪装成低风险。

## 1. 系统如何运行

```text
AISStream.io WebSocket
  -> scripts/run_ais_consumer.py 常驻 worker
  -> 解析、去重、内存窗口聚合
  -> Vessel + 每船一条最新 VesselObservation
  -> PortTrafficSnapshot
  -> Port / GeoZone / RouteSegment
  -> provider_risk 的 sea.port_congestion
  -> FastAPI 查询与路径推荐
```

FastAPI 和 AIS worker 是两个独立进程：

- FastAPI 负责接收前端 HTTP 请求；
- AIS worker 负责长期读取 WebSocket 并更新 AuraDB；
- 两者通过同一个 AuraDB 共享结果；
- 本地 FastAPI 不需要一直开着，Render 上的 FastAPI 也不会自动替代 AIS worker。

不要在前端调用 AISStream.io，也不要把 `AISSTREAM_API_KEY` 写进 `NEXT_PUBLIC_*`、Vite `VITE_*`、网页 JavaScript 或 GitHub 仓库。

## 2. 当前四个订阅区域

配置文件是 `config/ais_observation_targets.json`。

| 监测目标 | 聚合范围 | 外围订阅范围 | 关联图节点 |
|---|---|---|---|
| 上海港附近海域 | `30.75,121.20` 到 `31.55,122.35` | `30.55,120.95` 到 `31.80,122.55` | `Port`：`CN-SHA/CNSHA/CNSHG` |
| 新加坡港附近海域 | `0.95,103.45` 到 `1.55,104.15` | `0.75,103.25` 到 `1.75,104.35` | `Port`：`SGSIN` |
| 鹿特丹港附近海域 | `51.72,3.65` 到 `52.15,4.65` | `51.50,3.40` 到 `52.35,5.00` | `Port`：`NLRTM` |
| 苏伊士运河附近海域 | `29.75,32.15` 到 `31.40,32.75` | `29.50,31.95` 到 `31.60,32.95` | `GeoZone`：`red-sea` |

坐标顺序是 `[纬度, 经度]`，与 AISStream.io 订阅格式一致。外围订阅框比聚合框大，目的是观察船舶从外部进入或离开聚合区的状态变化。它们是项目监测范围，不是法定港界或航海边界。

订阅没有 `FiltersShipMMSI`，因此无需提前知道船舶 MMSI。

## 3. 第一次配置 API Key

### 第 1 步：创建 AISStream.io Key

1. 打开 <https://aisstream.io>；
2. 登录账号；
3. 进入 API Keys 页面；
4. 创建一个 Key；
5. 不要把 Key 发给前端同事，也不要截图上传仓库。

### 第 2 步：仅在 worker 环境配置

本地临时验证时，在项目根目录 `.env` 添加：

```dotenv
AISSTREAM_API_KEY=你的真实AISStream密钥
```

`.env.example` 只保留空占位符，可以提交；真实 `.env` 已被 `.gitignore` 忽略，不可提交。

### 第 3 步：检查配置，不连接网络

```bash
cd "/Users/vegeta/全球供应链管理/supply-chain-api"
source .venv/bin/activate
python scripts/run_ais_consumer.py --check-config
```

检查输出：

- `configured=true`：当前进程读取到了 Key；
- `targets` 正好有 4 项；
- `apiKeyExposed=false`；
- 输出中绝不能出现真实 Key。

如果只看到 `configured=false`，说明当前终端没有读取到 `AISSTREAM_API_KEY`，但不会影响 FastAPI 查询已经保存到 AuraDB 的旧快照。

## 4. 第一次迁移 AuraDB

先预览，不写数据库：

```bash
python scripts/migrate_ais_stage7.py
```

确认预览后执行：

```bash
python scripts/migrate_ais_stage7.py \
  --execute \
  --confirm APPLY_AIS_STAGE7
```

迁移只执行增量操作：

1. 创建 AIS 唯一约束和索引；
2. 用 `MERGE` 建立 4 个 `AisObservationTarget`；
3. 将三个港区连接到已有 `Port`，将苏伊士连接到 `GeoZone {zone_id:'red-sea'}`；
4. 把名称或呼号明确含 `DEMO/D3MO` 的旧示例观测标为 `synthetic + excluded`，保留审计记录，不作为风险证据；
5. 写入不含密钥的 `AisProviderState`。

重复执行是幂等的，不会创建重复监测目标。

## 5. 本地短时间验证

安装新增依赖：

```bash
pip install -r requirements.txt
```

连接 120 秒但不写 AuraDB：

```bash
python scripts/run_ais_consumer.py --dry-run --duration-seconds 120
```

这一步仍然需要真实 `AISSTREAM_API_KEY`，但只在内存聚合。结束后会输出消息数和快照预览，不输出完整原始报文或 API Key。

确认后，运行 10 分钟并写 AuraDB：

```bash
python scripts/run_ais_consumer.py --duration-seconds 600
```

正式常驻运行：

```bash
python scripts/run_ais_consumer.py
```

按 `Ctrl+C` 停止。停止本地命令后，本地不会再更新 AIS；已经写入 AuraDB 的快照会在 `expires_at` 后自动变为陈旧，不再进入推荐风险。

## 6. Render 上如何常驻运行

### 6.1 推荐结构

保留现在的 Render Web Service：

```text
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

再创建一个独立 Background Worker：

```text
python scripts/run_ais_consumer.py
```

Render 官方说明 Background Worker 是持续运行、不会提供 HTTP 地址的服务，适合长期调用第三方 API。参考：<https://render.com/docs/background-workers>。

### 6.2 Dashboard 逐步配置

1. 推送当前代码到 GitHub；
2. 登录 Render Dashboard；
3. 点击 `New`；
4. 选择 `Background Worker`；
5. 连接与 Web Service 相同的 GitHub 仓库；
6. Branch 选择 `main`；
7. Runtime 选择 `Python 3`；
8. Build Command 填：

```text
pip install -r requirements.txt
```

9. Start Command 填：

```text
python scripts/run_ais_consumer.py
```

10. 在 Worker 的 Environment 中添加：

```text
AURA_NEO4J_URI
AURA_NEO4J_USERNAME
AURA_NEO4J_PASSWORD
AURA_NEO4J_DATABASE
AISSTREAM_API_KEY
```

11. 点击部署；
12. 打开 Worker Logs，确认出现 `AISStream connection established for 4 target areas`；
13. 不要在日志里手工打印环境变量。

### 6.3 为什么不能只依赖 Render 免费 Web Service

Render 官方免费说明只把 Web Service、Static Site 和部分数据库列为 Free 类型；Blueprint 文档明确指出 `free` 不适用于 Background Worker。免费 Web Service 还会在一段时间没有入站流量后休眠，而 AIS 连接是由服务主动连接外部 Provider，不适合作为可靠常驻采集器。参考：

- <https://render.com/docs/free>
- <https://render.com/docs/blueprint-spec>

如果暂时不付费：

- 可以在一台不会休眠的自有服务器、学校服务器或云主机运行 worker；
- 可以手工短时运行以做课程演示；
- GitHub Actions 定时运行只能形成间歇采样，不是准实时服务，并可能消耗 Actions 分钟，本项目没有默认启用这种高消耗工作流。

无论 worker 在哪里运行，FastAPI 都只从 AuraDB 读取结果，不要求你的电脑一直开机。

## 7. 前端可调用接口

前端只调用你的 Render API，不调用 AISStream.io。

### 7.1 Provider 健康状态

```http
GET /health/ais
GET /api/providers/status
```

`/health/ais` 只返回 AISStream.io；`/api/providers/status` 同时汇总 AISStream.io、GDELT 和 Open-Meteo。

示例：

```json
{
  "id": "aisstream",
  "provider": "AISStream.io",
  "status": "unavailable",
  "reason": "missing_api_key_or_worker_not_configured",
  "configured": false,
  "connected": false,
  "lastMessageAt": null,
  "lastSnapshotAt": null,
  "apiKeyExposed": false
}
```

状态含义：

- `healthy`：worker 已连接且最近收到消息；
- `degraded`：配置过但没有连接或消息已陈旧；
- `unavailable`：未配置、认证失败或 Provider 拒绝订阅。

### 7.2 港口交通

```http
GET /api/ports/CN-SHA/traffic
GET /api/ports/SGSIN/traffic
GET /api/ports/NLRTM/traffic
```

真实快照不存在时返回港口信息和 `traffic: null`，不会返回伪造的 `vesselCount=0` 或 `congestionScore=50`。

### 7.3 苏伊士监测区

```http
GET /api/ais/targets/suez-canal/traffic
```

苏伊士是走廊，不是当前数据库中的独立 `Port`，因此使用 target 接口。

列出四个区域：

```http
GET /api/ais/targets
```

### 7.4 船舶最新状态

```http
GET /api/vessels/259000420
```

返回字段：

```json
{
  "mmsi": "259000420",
  "imo": "9353333",
  "name": "AUGUSTSON",
  "type": 70,
  "callSign": "LBHF",
  "position": {
    "lat": 31.1,
    "lng": 121.9,
    "speedKnots": 0.4,
    "courseDegrees": 308,
    "headingDegrees": 235,
    "observedAt": "2026-07-26T08:22:32Z"
  },
  "destination": "ROTTERDAM",
  "draughtM": 4.5,
  "navigationalStatus": "at_anchor",
  "provider": "AISStream.io",
  "dataStatus": "observed",
  "rawPayloadRetained": false
}
```

AIS 报文不保证包含全部字段，前端必须允许 `imo`、船名、船型、速度、目的地、吃水或导航状态为 `null`。

## 8. `PortTrafficSnapshot` 字段

| 字段 | 说明 |
|---|---|
| `vessel_count` | 窗口内最后位置仍在聚合区的唯一 MMSI 数量 |
| `anchored_count` | 导航状态明确为 `at_anchor` 或 `moored` 的船数 |
| `average_speed` | 有 SOG 的区内船舶平均速度，单位节 |
| `arrival_count` | 已观察到从外围区进入聚合区的次数 |
| `departure_count` | 已观察到从聚合区进入外围区的次数 |
| `congestion_score` | 0-100 派生拥堵分数 |
| `observed_at` | 本窗口最后一条真实 AIS 位置时间 |
| `expires_at` | 风险失效时间，默认最后观测后 90 分钟 |
| `confidence` | 样本数、时间覆盖、速度和导航状态覆盖形成的置信度 |
| `data_completeness` | 位置、速度、导航状态和时间覆盖完整度 |

拥堵公式只使用真实观测和显式模型参数：

```text
拥堵分数 = 可用维度重新归一化后的加权结果

船舶密度压力                    35%
锚泊/靠泊比例                   30%
低速船比例                      25%
到港大于离港的窗口失衡          10%
```

`congestion_reference_vessel_count` 是透明的模型校准参数，不冒充 Provider 观测。导航状态、速度或完整窗口不可用时，对应维度不参与，并降低 `data_completeness/confidence`。

## 9. 如何影响路线推荐

只有同时满足以下条件，AIS 拥堵才进入海运风险：

1. 快照 `provider='AISStream.io'`；
2. `source_type='ais_observed'`；
3. `calculation_status='derived_from_observed_ais'`；
4. 存在真实 `observation_count`；
5. `expires_at` 仍在未来；
6. 路段运输方式为 `sea`；
7. 路段端点是对应港口，或通过 `PASSES_THROUGH` 经过苏伊士对应 `GeoZone`；
8. 路段不是 `synthetic`，也不是 `invalid_cross_ocean`。

满足后，系统写入：

```text
RouteSegment.ais_congestion_score
RouteSegment.ais_congestion_provider
RouteSegment.ais_congestion_snapshot_ids
RouteSegment.ais_congestion_observed_at
RouteSegment.ais_congestion_expires_at
```

然后重新计算 `provider_risk_score`。AIS 只映射到海运 `port_congestion`，不会错误进入铁路、公路或空运风险。

## 10. Neo4j 为什么不保存全部轨迹

AIS 位置消息频率高，直接把每条坐标长期写入 AuraDB 会快速增加节点、关系和写入费用。当前策略：

- `Vessel`：每艘船一个节点，保存最新静态资料和最新位置；
- `VesselObservation`：每艘船一个 `ais-latest:{mmsi}` 节点，覆盖更新；
- 不保存完整 `raw_payload_json`，只保存 SHA-256 哈希用于去重审计；
- `PortTrafficSnapshot`：按窗口 ID 使用 `MERGE` 覆盖当前窗口，保留聚合历史。

`ais/storage.py` 定义了 `AisStorage` 接口。以后接入 TimescaleDB/PostgreSQL 时，可以把高频位置写入时序库，同时继续把最新状态和聚合风险写入 Neo4j，不需要改解析器和聚合器。

## 11. 常见问题

### `Missing AISSTREAM_API_KEY`

Key 没有配置在运行 worker 的环境。它与 FastAPI Web Service、GitHub Actions、Render Worker 的环境变量相互独立，需要在实际运行 worker 的服务中配置。

### `/health/ais` 显示 `unavailable`

先检查：

1. 是否执行阶段 7 迁移；
2. Render Background Worker 是否存在；
3. Worker 环境是否有 AuraDB 四项变量和 `AISSTREAM_API_KEY`；
4. Worker Logs 是否出现认证错误；
5. AISStream.io 是否暂时不可用。

### `traffic` 是 `null`

这代表没有真实快照，不是接口坏了。可能原因：worker 未运行、该区域暂时没有收到位置、Provider 断线或快照已经过期。

### 船名、IMO、目的地是 `null`

位置报文和静态资料报文是不同消息。先收到位置时，这些字段可以为空；以后收到 `ShipStaticData` 或 `StaticDataReport` 会补齐，不会用假值填充。

### 到港/离港数量偏少

只有观察到船舶在外围框和聚合框之间发生状态变化时才计数。worker 刚启动时不会把所有已在港内的船误算为新到港；AIS 覆盖缺口也会降低统计完整度。

## 12. 验证命令

```bash
python scripts/run_ais_consumer.py --check-config
python scripts/migrate_ais_stage7.py
pytest -q tests/test_ais_parser.py tests/test_ais_aggregation.py tests/test_ais_service.py tests/test_stage7_api.py tests/test_provider_risk.py
```

AuraDB Browser：

```cypher
MATCH (target:AisObservationTarget)
OPTIONAL MATCH (target)-[:REPRESENTS_PORT]->(port:Port)
OPTIONAL MATCH (target)-[:REPRESENTS_ZONE]->(zone:GeoZone)
RETURN target.target_id,target.name,port.unlocode,zone.zone_id;
```

```cypher
MATCH (target:AisObservationTarget)-[:HAS_TRAFFIC_SNAPSHOT]->(snapshot:PortTrafficSnapshot)
RETURN target.target_id,snapshot.vessel_count,snapshot.anchored_count,
       snapshot.average_speed,snapshot.arrival_count,snapshot.departure_count,
       snapshot.congestion_score,snapshot.observed_at,snapshot.expires_at
ORDER BY snapshot.observed_at DESC;
```

```cypher
MATCH (segment:RouteSegment)-[:EXPOSED_TO_AIS_TRAFFIC]->(snapshot:PortTrafficSnapshot)
WHERE snapshot.expires_at > datetime()
RETURN segment.segment_id,segment.ais_congestion_score,
       segment.provider_risk_score,segment.provider_risk_factors_json,
       snapshot.snapshot_id;
```
