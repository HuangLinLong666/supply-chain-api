# 阶段 3：Neo4j 统一数据模型迁移报告

> 生成时间（UTC）：`2026-07-26T02:25:19.428394+00:00`  
> 模式：`dry-run`  
> Schema 版本：`unified-transport-v1`  
> 数据库：`94a63264`

## 1. 执行结论

- 状态：`dry_run`。
- 删除节点：**0**；删除关系：**0**。
- 计划指纹：`82c30e35d45941eab19dbfd11560a89895ec916f9319631bd90fe97f3bf45074`。
- 预计规范化节点：**14018**。
- 预计新增兼容关系：**69**。

## 2. 安全策略

- 本迁移不执行 `DELETE`、`DETACH DELETE` 或节点重建。
- 保留所有旧标签、旧主键和旧关系，只增加规范标签、字段和兼容关系。
- GDELT、Open-Meteo、AIS 节点不会被删除；其原标签继续供旧 API 使用。
- 没有真实 Provider 的数据不会补造 Provider，`data_status` 会标记为 `estimated`、`synthetic` 或 `unavailable`。
- 地点标识冲突不自动合并节点，而是分配可审计后缀并记录冲突，避免唯一约束失败。

## 3. 主键预检

| 实体 | 预计节点 | 有主键 | 缺主键 | 重复主键组 |
|---|---|---|---|---|
| Supplier | 29 | 29 | 0 | 0 |
| Factory | 19 | 19 | 0 | 0 |
| RouteSegment | 613 | 613 | 0 | 0 |
| Route | 26 | 26 | 0 | 0 |
| GeoZone | 10 | 10 | 0 | 0 |
| RiskObservation | 11989 | 11989 | 0 | 0 |
| CostObservation | 609 | 609 | 0 | 0 |
| DelayObservation | 628 | 628 | 0 | 0 |
| Evidence | 11957 | 11957 | 0 | 0 |
| Vessel | 1 | 1 | 0 | 0 |
| PortTrafficSnapshot | 0 | 0 | 0 | 0 |
| RecommendationSnapshot | 0 | 0 | 0 | 0 |

## 4. 地点统一

- 预计 `TransportLocation` 节点：**102**。
- 预计 `Warehouse` 节点：**42**。
- 本次需要更新地点：**102**。
- 已安全消歧的地点 ID 冲突：**7**。

| elementId | 当前标签 | 新增标签 | location_id | 类型 |
|---|---|---|---|---|
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1060 | Port, TransportLocation |  | DE-HAM | port |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1062 | Port, TransportLocation |  | US-LAX | port |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:10696 | Port, TransportLocation |  | CN-LYG | port |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:10697 | Port, TransportLocation |  | US-LGB | port |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:137 | Airport, TransportLocation |  | CN-PVG | airport |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:147 | Airport, TransportLocation |  | DE-FRA | airport |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:19 | Port, TransportLocation |  | CN-SHA | port |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1053 | Port | TransportLocation | CNSZX | port |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1054 | Port | TransportLocation | CNGGZ | port |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1055 | Port | TransportLocation | SGSIN | port |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1056 | Port | TransportLocation | KRPUS | port |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1057 | Port | TransportLocation | AEJEA | port |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1058 | Port | TransportLocation | MYPKG | port |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1059 | Port | TransportLocation | NLRTM | port |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1061 | Port | TransportLocation | BEANR | port |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1063 | Port | TransportLocation | GBFXT | port |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:11 | ExportWarehouse | TransportLocation, Warehouse | loc:warehouse:50b1347046 | warehouse |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:12 | ExportWarehouse | TransportLocation, Warehouse | loc:warehouse:f4148092fc | warehouse |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:13 | ExportWarehouse | TransportLocation, Warehouse | loc:warehouse:a273a31ba8 | warehouse |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:136 | Airport | TransportLocation | PEK | airport |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:138 | Airport | TransportLocation | CAN | airport |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:139 | Airport | TransportLocation | SZX | airport |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:14 | ExportWarehouse | TransportLocation, Warehouse | loc:warehouse:f92c0c431e | warehouse |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:140 | Airport | TransportLocation | TFU | airport |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:141 | Airport | TransportLocation | CGO | airport |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:142 | Airport | TransportLocation | HGH | airport |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:143 | Airport | TransportLocation | XIY | airport |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:144 | Airport | TransportLocation | SIN | airport |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:145 | Airport | TransportLocation | DXB | airport |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:146 | Airport | TransportLocation | DOH | airport |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:148 | Airport | TransportLocation | AMS | airport |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:149 | Airport | TransportLocation | IST | airport |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:15 | ExportWarehouse | TransportLocation, Warehouse | loc:warehouse:3015ffa6a2 | warehouse |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:150 | Airport | TransportLocation | LAX | airport |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:151 | Airport | TransportLocation | NRT | airport |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:16 | ExportWarehouse | TransportLocation, Warehouse | loc:warehouse:36abcca8db | warehouse |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:160 | DepartureWarehouse | TransportLocation, Warehouse | PEK~7da2cc48 | warehouse |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:161 | DepartureWarehouse | TransportLocation, Warehouse | PVG | warehouse |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:162 | DepartureWarehouse | TransportLocation, Warehouse | CAN~b051f1df | warehouse |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:163 | DepartureWarehouse | TransportLocation, Warehouse | SZX~4fb843a7 | warehouse |

## 5. 迁移操作

| 操作 | 预计数量 |
|---|---|
| route_leg_to_segment | 19 |
| vehicle_route_to_route | 0 |
| news_zone_to_geo_zone | 10 |
| route_risk_snapshot_to_observation | 23 |
| weather_snapshot_to_observation | 3 |
| country_risk_to_observation | 17 |
| news_event_to_observation_and_evidence | 11946 |
| cost_estimate_to_observation | 23 |
| route_cost_to_observation | 586 |
| route_delay_to_observation | 628 |
| source_evidence_to_evidence | 11 |
| canonical_route_segments | 20 |
| canonical_route_risk_observations | 23 |
| canonical_route_cost_observations | 23 |
| canonical_port_weather_observations | 3 |
| normalize_provenance | 14018 |
| normalize_route_segments | 613 |
| normalize_routes | 26 |

## 6. 约束与索引

- 待补唯一约束：**8**。
- 待补查询索引：**16**。

| 类型 | 名称 |
|---|---|
| 约束 | unified_warehouse_id_unique |
| 约束 | unified_geo_zone_id_unique |
| 约束 | unified_risk_observation_id_unique |
| 约束 | unified_cost_observation_id_unique |
| 约束 | unified_delay_observation_id_unique |
| 约束 | unified_vessel_mmsi_unique |
| 约束 | unified_port_traffic_snapshot_id_unique |
| 约束 | unified_recommendation_snapshot_id_unique |
| 索引 | unified_transport_location_kind |
| 索引 | unified_transport_location_country |
| 索引 | unified_transport_location_status |
| 索引 | unified_route_segment_mode |
| 索引 | unified_route_segment_status |
| 索引 | unified_route_status |
| 索引 | unified_geo_zone_type |
| 索引 | unified_geo_zone_updated |
| 索引 | unified_risk_observation_observed |
| 索引 | unified_risk_observation_expires |
| 索引 | unified_cost_observation_observed |
| 索引 | unified_delay_observation_observed |
| 索引 | unified_evidence_collected |
| 索引 | unified_vessel_last_observed |
| 索引 | unified_port_traffic_observed |
| 索引 | unified_recommendation_created |

## 7. 统一模型数量

| 标签 | 迁移前 | 迁移后 |
|---|---|---|
| Supplier | 29 | 未执行 |
| Factory | 19 | 未执行 |
| Warehouse | 6 | 未执行 |
| TransportLocation | 7 | 未执行 |
| Port | 20 | 未执行 |
| Airport | 16 | 未执行 |
| RailTerminal | 5 | 未执行 |
| RoadTerminal | 0 | 未执行 |
| RouteSegment | 594 | 未执行 |
| Route | 26 | 未执行 |
| GeoZone | 0 | 未执行 |
| RiskObservation | 0 | 未执行 |
| CostObservation | 0 | 未执行 |
| DelayObservation | 0 | 未执行 |
| Evidence | 0 | 未执行 |
| Vessel | 1 | 未执行 |
| PortTrafficSnapshot | 0 | 未执行 |
| RecommendationSnapshot | 0 | 未执行 |

## 8. 产物

- JSON：`artifacts/unified_schema_migration_20260726T022519Z.json`。
- Markdown：`artifacts/unified_schema_migration_20260726T022519Z.md`。

## 9. 命令

```bash
python scripts/migrate_unified_schema.py --dry-run
python scripts/migrate_unified_schema.py --execute --confirm APPLY_UNIFIED_SCHEMA_V1
```
