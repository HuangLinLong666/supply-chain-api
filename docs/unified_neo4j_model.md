# 阶段 3：Neo4j 统一数据模型

> 完成时间：2026-07-26（Asia/Shanghai）  
> Schema 版本：`unified-transport-v1`  
> 迁移方式：增量多标签兼容，不删除旧节点、旧标签或旧关系

## 1. 为什么使用多标签兼容

当前数据库同时存在旧物流图、整车运输网络、GDELT、Open-Meteo 和 AIS 数据。直接改名会让旧 FastAPI 查询失效，因此阶段 3 采用以下规则：

1. 旧标签继续保留，例如 `RouteLeg`、`RiskSnapshot`、`NewsRiskEvent`。
2. 同一个节点增加规范标签，例如 `RouteLeg:RouteSegment`。
3. 旧主键继续保留，同时增加统一主键，例如 `leg_id` 与 `segment_id` 并存。
4. 旧关系继续保留，同时通过 `MERGE` 增加规范关系。
5. 不存在真实 Provider 时保持为空，不用假数据补齐。

## 2. 统一实体

| 规范实体 | 唯一主键 | 迁移后数量 | 兼容来源 |
|---|---|---:|---|
| `Supplier` | `supplier_id` | 29 | 原 `Supplier` |
| `Factory` | `factory_id` | 19 | 原 `Factory` |
| `Warehouse` | `warehouse_id` | 42 | `Warehouse`、`ExportWarehouse`、`OverseasWarehouse`、`DepartureWarehouse`、`ArrivalWarehouse` |
| `TransportLocation` | `location_id` | 102 | 工厂、仓库、港口、机场、铁路站、公路站 |
| `Port` | 继承 `location_id` | 20 | 原 `Port` |
| `Airport` | 继承 `location_id` | 16 | 原 `Airport` |
| `RailTerminal` | 继承 `location_id` | 5 | 原 `RailTerminal` |
| `RoadTerminal` | 继承 `location_id` | 0 | 当前没有可迁移节点 |
| `RouteSegment` | `segment_id` | 613 | 原 `RouteSegment` + 19 个 `RouteLeg` |
| `Route` | `route_id` | 26 | 原 `Route`、`VehicleRoute` |
| `GeoZone` | `zone_id` | 10 | 原 `NewsRiskZone` |
| `RiskObservation` | `observation_id` | 11,989 | GDELT 新闻、天气、国家风险和路线风险快照 |
| `CostObservation` | `observation_id` | 609 | `RouteCostObservation`、`CostEstimate` |
| `DelayObservation` | `observation_id` | 628 | `RouteDelayObservation` |
| `Evidence` | `evidence_id` | 11,957 | GDELT 新闻、`SourceEvidence` |
| `Vessel` | `mmsi` | 1 | 旧 DEMO 已在阶段 7 标为 `synthetic + excluded` |
| `VesselObservation` | `observation_id` | 1 | 旧 DEMO 观测已排除；真实 worker 每船只保留一条最新状态 |
| `AisObservationTarget` | `target_id` | 4 | 上海、新加坡、鹿特丹、苏伊士监测区 |
| `AisProviderState` | `provider_id` | 1 | AIS worker 健康状态，不保存 API Key |
| `PortTrafficSnapshot` | `snapshot_id` | 0 | 阶段 7 聚合模型已完成，等待真实 AIS worker 产生快照 |
| `RecommendationSnapshot` | `snapshot_id` | 3 | 阶段 8 真实 API 冒烟调用的算法审计快照 |

`Port`、`Airport`、`RailTerminal` 和 `RoadTerminal` 都使用 `TransportLocation.location_id` 的全局唯一约束，不重复创建互相冲突的地点主键体系。

地点主键进一步统一为 `location-id-v2`：港口使用 `PORT-{UNLOCODE}`，机场使用 `AIR-{IATA/ICAO}`，铁路、公路、工厂和仓库使用带类型与国家码的稳定业务 ID。旧主键保存在 `location_aliases`，详细规则及迁移命令见 `docs/location_id_naming.md`。

## 3. 规范关系

迁移保留旧关系，并增加以下兼容关系：

```text
(Route)-[:HAS_SEGMENT]->(RouteSegment)
(Route)-[:HAS_RISK_OBSERVATION]->(RiskObservation)
(Route)-[:HAS_COST_OBSERVATION]->(CostObservation)
(Port)-[:HAS_RISK_OBSERVATION]->(RiskObservation)
(AisObservationTarget)-[:REPRESENTS_PORT]->(Port)
(AisObservationTarget)-[:REPRESENTS_ZONE]->(GeoZone)
(AisObservationTarget)-[:HAS_TRAFFIC_SNAPSHOT]->(PortTrafficSnapshot)
(RouteSegment)-[:EXPOSED_TO_AIS_TRAFFIC]->(PortTrafficSnapshot)
(RouteSegment)-[:INCLUDED_IN]->(RecommendationSnapshot)
```

本次新增 69 条关系：

- `HAS_SEGMENT`：20 条；
- 路线 `HAS_RISK_OBSERVATION`：23 条；
- 路线 `HAS_COST_OBSERVATION`：23 条；
- 港口天气 `HAS_RISK_OBSERVATION`：3 条。

数据库已有其他规范关系，因此迁移完成后的总数可能高于本次新增量。

## 4. 统一来源字段

规范实体使用以下字段。字段没有可信值时可以不存在，不允许伪造：

| 字段 | 含义 |
|---|---|
| `schema_version` | 当前模型版本，现为 `unified-transport-v1` |
| `source` | 原始数据源名称 |
| `source_type` | `official_registry`、`open_api`、`estimated_by_graph` 等来源类别 |
| `provider` | 真实 Provider；没有时保持空值 |
| `source_url` | 可追溯 URL；没有时保持空值 |
| `collected_at` | 原数据采集、观测或计算时间 |
| `confidence` | 原有置信度，不使用统一默认分数补齐 |
| `data_status` | 数据真实性和可用状态 |
| `is_inferred` | 是否由估算或图算法推断 |
| `schema_migrated_at` | 首次进入统一模型的迁移时间 |

`data_status` 只使用以下值：

- `verified`：官方注册、官方班期或人工审核通过；
- `observed`：GDELT、Open-Meteo、AIS 等真实 API 观测；
- `estimated`：明确标记为图算法估算；
- `synthetic`：测试、sample、mock、标准骨架等合成数据；
- `unavailable`：无法判断真实来源或缺少 Provider。

没有 Provider 的成本、延误和风险观测会保留，但 `status=unavailable`，后续阶段不得直接把它们加入风险加权。

## 5. 路线字段

`RouteSegment` 新增：

- `canonical_mode`：只允许 `road`、`rail`、`sea`、`air`；
- `legacy_mode`：保留迁移前运输方式；
- `feasibility_status`：`unreviewed` 或 `invalid_or_ambiguous_mode`；
- `validity_status`：是否具有 `valid_from` / `valid_until`；
- `scoring_version`：旧数据使用 `legacy_unversioned`。

阶段 3 不强行改写含义不明的 `multimodal`。无法可靠映射的旧值保持在 `legacy_mode`，`canonical_mode` 留空，并标记为 `invalid_or_ambiguous_mode`，等待阶段 4 清理或阶段 6 重建。

## 6. 地点 ID 冲突处理

迁移发现 7 个机场与旧机场仓库共用了 `PEK`、`CAN`、`SZX`、`TFU`、`CGO`、`HGH`、`XIY` 等代码。

脚本没有自动合并这些节点，而是：

1. 保留机场的原代码；
2. 给冲突仓库增加稳定哈希后缀；
3. 写入 `identity_status=conflict_disambiguated`；
4. 写入 `identity_conflict_key` 供后续人工核对。

这种处理只解决唯一约束，不代表仓库已经获得真实经纬度或官方注册信息。

## 7. 安全迁移命令

只预览：

```bash
python scripts/migrate_unified_schema.py --dry-run
```

实际执行：

```bash
python scripts/migrate_unified_schema.py \
  --execute \
  --confirm APPLY_UNIFIED_SCHEMA_V1
```

脚本默认限制最多规范化 25,000 个节点、最多新增 2,000 条关系。迁移不包含任何节点或关系删除语句。

本次执行报告：

- `artifacts/unified_schema_migration_20260726T022804Z.json`
- `artifacts/unified_schema_migration_20260726T022804Z.md`

迁移后再次 dry-run 的地点更新、实体操作、关系操作、约束和索引待办均为 0，证明脚本可重复执行。

## 8. Neo4j Browser 验证

查看统一模型链路：

```cypher
MATCH path=(route:Route)-[:HAS_SEGMENT]->(segment:RouteSegment)-[:TO_NODE]->(location:TransportLocation)
RETURN path
LIMIT 30;
```

查看带证据的新闻风险：

```cypher
MATCH path=(event:NewsRiskEvent:RiskObservation:Evidence)-[:AFFECTS_ZONE]->(zone:GeoZone)
RETURN path
LIMIT 30;
```

查看数据状态与 Provider：

```cypher
MATCH (node)
WHERE node.schema_version = 'unified-transport-v1'
RETURN labels(node) AS labels,
       node.data_status AS dataStatus,
       node.provider AS provider,
       count(*) AS count
ORDER BY count DESC;
```

## 9. 尚未在阶段 3 解决的问题

- 未删除无 Provider 的默认风险，交由阶段 4 处理；
- 未执行 GDELT 去重、分类与 TTL 重构，交由阶段 5 处理；
- 地点坐标、路线 geometry、GeoZone Polygon 和 `PASSES_THROUGH` 已在阶段 6 完成，见 `docs/stage6_geospatial_risk_zones.md`；
- 阶段 7 AIS 消费、聚合、健康检查与路段拥堵风险已完成；当前因未配置真实 worker，`PortTrafficSnapshot=0`，见 `docs/aisstream_port_traffic.md`；
- 阶段 8 已创建 `RecommendationSnapshot`，保存请求、权重、约束、评分版本和完整响应，并用 `INCLUDED_IN` 连接入选分段；
- 未把来源不明的数据错误标记为真实 Provider。
