# 阶段 3：Neo4j 统一数据模型迁移报告

> 生成时间（UTC）：`2026-07-26T02:30:09.861036+00:00`  
> 模式：`dry-run`  
> Schema 版本：`unified-transport-v1`  
> 数据库：`94a63264`

## 1. 执行结论

- 状态：`dry_run`。
- 删除节点：**0**；删除关系：**0**。
- 计划指纹：`7a80703c8494ade430776e083bd5195428d016806181199d98b1949fc996ca90`。
- 预计规范化节点：**0**。
- 预计新增兼容关系：**0**。

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
- 本次需要更新地点：**0**。
- 已安全消歧的地点 ID 冲突：**0**。

_无记录_

## 5. 迁移操作

| 操作 | 预计数量 |
|---|---|
| route_leg_to_segment | 0 |
| vehicle_route_to_route | 0 |
| news_zone_to_geo_zone | 0 |
| route_risk_snapshot_to_observation | 0 |
| weather_snapshot_to_observation | 0 |
| country_risk_to_observation | 0 |
| news_event_to_observation_and_evidence | 0 |
| cost_estimate_to_observation | 0 |
| route_cost_to_observation | 0 |
| route_delay_to_observation | 0 |
| source_evidence_to_evidence | 0 |
| canonical_route_segments | 0 |
| canonical_route_risk_observations | 0 |
| canonical_route_cost_observations | 0 |
| canonical_port_weather_observations | 0 |
| normalize_provenance | 0 |
| normalize_route_segments | 0 |
| normalize_routes | 0 |

## 6. 约束与索引

- 待补唯一约束：**0**。
- 待补查询索引：**0**。

_无记录_

## 7. 统一模型数量

| 标签 | 迁移前 | 迁移后 |
|---|---|---|
| Supplier | 29 | 未执行 |
| Factory | 19 | 未执行 |
| Warehouse | 42 | 未执行 |
| TransportLocation | 102 | 未执行 |
| Port | 20 | 未执行 |
| Airport | 16 | 未执行 |
| RailTerminal | 5 | 未执行 |
| RoadTerminal | 0 | 未执行 |
| RouteSegment | 613 | 未执行 |
| Route | 26 | 未执行 |
| GeoZone | 10 | 未执行 |
| RiskObservation | 11989 | 未执行 |
| CostObservation | 609 | 未执行 |
| DelayObservation | 628 | 未执行 |
| Evidence | 11957 | 未执行 |
| Vessel | 1 | 未执行 |
| PortTrafficSnapshot | 0 | 未执行 |
| RecommendationSnapshot | 0 | 未执行 |

## 8. 产物

- JSON：`artifacts/unified_schema_migration_20260726T023009Z.json`。
- Markdown：`artifacts/unified_schema_migration_20260726T023009Z.md`。

## 9. 命令

```bash
python scripts/migrate_unified_schema.py --dry-run
python scripts/migrate_unified_schema.py --execute --confirm APPLY_UNIFIED_SCHEMA_V1
```
