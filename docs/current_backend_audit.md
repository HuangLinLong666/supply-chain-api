# 当前后端与 AuraDB 只读审计报告

> 生成时间（UTC）：`2026-07-26T11:40:05.786692+00:00`
> 审计版本：`backend-audit-v1`
> 数据库：`94a63264`
> 本报告由只读 Neo4j 会话生成；本阶段未执行新增、修改或删除。

## 1. 审计结论

- **高** `NONCANONICAL_ROUTE_MODES`：535 个分段使用非 road/rail/sea/air 模式。
- **严重** `BROKEN_SEGMENT_ENDPOINTS`：21 个分段缺少或重复 FROM_NODE/TO_NODE。
- **高** `MISSING_SEGMENT_PROVIDER`：仅 0/613 个分段具有 provider 字段。
- **中** `LOW_ROUTE_WEATHER_COVERAGE`：仅 18/613 个分段具有 route_weather_risk。
- **高** `MISSING_ROUTE_GEOMETRY`：仅 10/613 个分段具有可审计的路线 geometry。
- **高** `REFERENCE_ROUTE_DATA`：594/613 个分段来源属于合成、骨架或外部参考字段，不能视为已验证运输服务。
- **高** `MISSING_LOCATION_COORDINATES`：地点坐标覆盖率为 89/147。
- **高** `AIS_NOT_OPERATIONAL`：AIS 船位观测不足，不能据此计算实时港口拥堵。
- **高** `SUPPLIER_ORIGIN_CHAIN_MISSING`：仅 0/29 个供应商具有 SHIPS_FROM→设施→HAS_ACCESS_LEG 链。

## 2. 数据库总览

| 指标 | 数量 |
|---|---|
| 节点 | 24164 |
| 关系 | 47924 |
| 节点标签种类 | 75 |
| 关系类型种类 | 110 |
| 约束 | 84 |
| 索引 | 121 |

### 2.1 主要节点标签

| 标签 | 数量 |
|---|---|
| RiskObservation | 21475 |
| NewsRiskEvent | 12202 |
| Evidence | 12181 |
| NewsRiskCluster | 9166 |
| DelayObservation | 628 |
| RouteDelayObservation | 628 |
| RouteSegment | 613 |
| CostObservation | 609 |
| RouteCostObservation | 586 |
| PortObservation | 107 |
| TransportLocation | 102 |
| WeatherRiskSnapshot | 63 |
| InventoryRecord | 48 |
| IngestionJob | 43 |
| Warehouse | 42 |
| AutoPart | 41 |
| Component | 41 |
| Part | 40 |
| DeliveryRecord | 36 |
| ProductionPlan | 36 |
| RouteWeatherRiskSnapshot | 36 |
| SalesOrder | 36 |
| EntityAlias | 34 |
| Supplier | 29 |
| Route | 28 |
| CostEstimate | 27 |
| RiskSnapshot | 27 |
| VehicleRoute | 22 |
| RouteLeg | 21 |
| Port | 20 |
| Factory | 19 |
| Feature | 18 |
| VehicleSystem | 18 |
| Country | 17 |
| CountryRiskObservation | 17 |
| Airport | 16 |
| CarModel | 15 |
| Product | 15 |
| RouteRecommendation | 15 |
| VehicleModel | 15 |
| FactoryIssue | 12 |
| TimePeriod | 12 |
| AuditLog | 11 |
| SourceEvidence | 11 |
| TransitHub | 11 |
| ArrivalWarehouse | 10 |
| Destination | 10 |
| DestinationCountry | 10 |
| GeoZone | 10 |
| NewsRiskZone | 10 |

### 2.2 主要关系类型

| 关系类型 | 数量 |
|---|---|
| AFFECTS_ZONE | 24628 |
| MEMBER_OF_EVENT_CLUSTER | 15381 |
| HAS_DELAY_OBSERVATION | 628 |
| HAS_COST_OBSERVATION | 609 |
| FROM_NODE | 594 |
| TO_NODE | 594 |
| FROM | 590 |
| TO | 590 |
| USES_MODE | 586 |
| CLASSIFIED_AS | 565 |
| USED_IN | 226 |
| PRODUCES | 182 |
| SUPPLIES_TO_FACTORY | 175 |
| HAS_RISK_OBSERVATION | 139 |
| IN_PERIOD | 132 |
| CAN_BE_TRANSPORTED_BY | 120 |
| EXPOSED_TO_NEWS_CLUSTER | 112 |
| HAS_OBSERVATION | 108 |
| TRANSPORT | 107 |
| EXPOSED_TO_NEWS_RISK | 80 |
| HAS_SYSTEM | 78 |
| WITH_FEATURE | 78 |
| PASSES_THROUGH | 69 |
| HAS_WEATHER_SNAPSHOT | 63 |
| LOCATED_IN | 54 |
| SUPPLIES | 53 |
| HAS_SEGMENT | 49 |
| FOR_PART | 48 |
| HAS_INVENTORY | 48 |
| IS_COMPOSED_OF | 44 |
| REQUIRES_PART | 44 |
| IS_SUPPLIED_BY | 40 |
| STORED_AT | 40 |
| SUPPLIED_BY | 40 |
| ROUTE_TO | 38 |
| GENERATED | 37 |
| DELIVERED_TO | 36 |
| DELIVERS_TO | 36 |
| FOR_MODEL | 36 |
| FULFILLED_BY | 36 |
| HAS_PRODUCTION_PLAN | 36 |
| HAS_ROUTE_WEATHER_SNAPSHOT | 36 |
| PLACES_ORDER | 36 |
| TARGET_MARKET | 36 |
| ALIAS_OF | 34 |
| CONNECTED_TO | 33 |
| LOCATED_AT | 32 |
| EXPORTED_TO | 30 |
| ORDERS_MODEL | 30 |
| SHIPS_FROM | 29 |

## 3. 关键业务实体

| 标签 | 数量 |
|---|---|
| Airport | 16 |
| CostObservation | 609 |
| DelayObservation | 628 |
| Evidence | 12181 |
| Factory | 19 |
| GeoZone | 10 |
| NewsRiskZone | 10 |
| Port | 20 |
| RailTerminal | 5 |
| RecommendationSnapshot | 3 |
| RiskObservation | 21475 |
| RiskSnapshot | 27 |
| Route | 28 |
| RouteCostObservation | 586 |
| RouteDelayObservation | 628 |
| RouteLeg | 21 |
| RouteRecommendation | 15 |
| RouteSegment | 613 |
| SourceEvidence | 11 |
| Supplier | 29 |
| TransportLocation | 102 |
| VehicleRoute | 22 |
| Vessel | 1 |
| VesselObservation | 1 |
| Warehouse | 42 |

### 3.1 供应商发货起点关系

| 供应商 | 有 SHIPS_FROM | 直接作为分段起点 | 具有 SHIPS_FROM→设施→HAS_ACCESS_LEG | SHIPS_FROM 目标数 |
|---|---|---|---|---|
| 29 | 25 | 28 | 0 | 28 |

| SHIPS_FROM 目标标签 | 数量 |
|---|---|
| EntryPoint | 25 |
| ChinaOrigin | 3 |

## 4. 地点与坐标完整度

| 地点总数 | 有名称 | 有坐标 | 坐标有来源 | 标记为估算 | 坐标覆盖率 |
|---|---|---|---|---|---|
| 147 | 147 | 89 | 89 | 56 | 60.5% |

| 标签 | 数量 | 有名称 | 有坐标 | 坐标覆盖率 |
|---|---|---|---|---|
| Airport | 16 | 16 | 16 | 100.0% |
| DeparturePoint | 8 | 8 | 0 | 0.0% |
| Destination | 10 | 10 | 0 | 0.0% |
| EntryPoint | 8 | 8 | 0 | 0.0% |
| Factory | 19 | 19 | 17 | 89.5% |
| Port | 20 | 20 | 20 | 100.0% |
| RailTerminal | 5 | 5 | 4 | 80.0% |
| TransferPoint | 8 | 8 | 0 | 0.0% |
| TransitHub | 11 | 11 | 0 | 0.0% |
| TransportLocation | 102 | 102 | 89 | 87.3% |
| Warehouse | 42 | 42 | 32 | 76.2% |

## 5. RouteSegment 审计

| 指标 | 数量 | 覆盖率 |
|---|---|---|
| 分段总数 | 613 | 100.0% |
| 距离 | 613 | 100.0% |
| 时效 | 594 | 96.9% |
| 成本 | 594 | 96.9% |
| 基础风险 | 25 | 4.1% |
| 动态风险 | 30 | 4.9% |
| 新闻风险 | 22 | 3.6% |
| 有效期内新闻风险 | 0 | 0.0% |
| 天气风险 | 18 | 2.9% |
| 风险明细 | 613 | 100.0% |
| 路线 geometry | 10 | 1.6% |
| 起终点均有坐标 | 57 | 9.3% |
| Provider | 0 | 0.0% |
| 来源 URL | 0 | 0.0% |
| 时间字段 | 613 | 100.0% |
| 置信度 | 605 | 98.7% |
| 包含 0.5/50 默认风险 | 0 | 0.0% |

### 5.1 运输方式

| mode | 数量 |
|---|---|
| multimodal | 390 |
| delivery | 82 |
| truck | 63 |
| road | 28 |
| sea | 23 |
| rail | 17 |
| air | 10 |

### 5.2 分段来源

| 来源 | 数量 |
|---|---|
| external_repos_reference_fields | 565 |
| standard_skeleton_reference | 21 |
| 图路径距离估算器 | 19 |
| Tesla SEC route skeleton | 8 |

### 5.3 数据来源质量分类

| 分类 | 数量 |
|---|---|
| synthetic_or_reference | 594 |
| attributed | 19 |

### 5.4 结构异常

- 缺少或重复 `FROM_NODE/TO_NODE` 的分段：**21**。
- 使用非标准 mode 的分段：**535**。
- 包含可疑 0.5/50 默认风险的分段：**0**。

## 6. 实时与观测数据基线

> 这些记录是后续清理阶段必须优先保护的数据基线。数量为 0 不代表 Provider 不可接入，只表示当前 AuraDB 没有相应记录。

| 标签 | 数量 | 最近时间 | 来源 |
|---|---|---|---|
| NewsRiskEvent | 12202 | 2026-07-26T10:14:13.518927000+00:00 | GDELT, GDELT DOC 2.0 API |
| NewsRiskZone | 10 | 2026-07-26T10:14:13.518927000+00:00 | GDELT |
| WeatherRiskSnapshot | 63 | 2026-07-26T11:39:30.124897000+00:00 | Open-Meteo |
| VesselObservation | 1 | 2026-07-05T07:30:00.000000000+00:00 | AISStream |
| PortTrafficSnapshot | 0 | — |  |
| RouteCostObservation | 586 | 2026-07-04T09:53:35.528000000+00:00 | external_repos_reference_fields, standard_skeleton_reference |
| RouteDelayObservation | 628 | 2026-07-05T13:19:41.000000000+00:00 | external_repos_reference_fields, standard_skeleton_reference, Public Risk Crawler |
| CountryRiskObservation | 17 | 2026-07-04T09:53:35.474000000+00:00 | standard_skeleton_reference, synthetic_experiment |
| PortObservation | 107 | 2026-07-05T13:15:00.000000000+00:00 | synthetic_cross_border_transport_risk, standard_skeleton_reference, GDELT DOC 2.1 API, Open-Meteo Forecast API |

### 6.1 项目 Provider 实现状态

| Provider | 代码状态 | 凭证是否配置 | 相关文件 |
|---|---|---|---|
| GDELT DOC API | implemented | 不需要 | gdelt/client.py, gdelt/service.py, .github/workflows/update-gdelt-risk.yml |
| Open-Meteo Forecast / Marine API | implemented | 不需要 | weather/client.py, weather/service.py |
| AISStream.io | implemented | 否 | config/ais_observation_targets.json, scripts/migrate_ais_stage7.py, scripts/run_ais_consumer.py, tests/test_ais_aggregation.py, tests/test_ais_parser.py, tests/test_ais_service.py |
| MarineTraffic | disabled_stub | 否 | app/vehicle_network/providers/stubs.py |
| OpenSky | disabled_stub | 否 | app/vehicle_network/providers/stubs.py |
| Aviation Edge | disabled_stub | 否 | app/vehicle_network/providers/stubs.py |
| FlightAware | disabled_stub | 否 | app/vehicle_network/providers/stubs.py |
| Cirium | disabled_stub | 否 | app/vehicle_network/providers/stubs.py |

### 6.2 当前 FastAPI 路由

当前 OpenAPI 共公开 **50** 个 HTTP 操作（不包含 `/docs` 等框架页面）。

| 方法 | 路径 | 标签 | deprecated |
|---|---|---|---|
| GET | / | Service | 否 |
| POST | /api/admin/gdelt/update | Dynamic News Risk Admin | 否 |
| POST | /api/admin/weather/routes/update | Route Weather Admin | 否 |
| POST | /api/admin/weather/update | Port Weather Admin | 否 |
| GET | /api/ais/targets | AIS Port Traffic | 否 |
| GET | /api/ais/targets/{target_id}/traffic | AIS Port Traffic | 否 |
| GET | /api/cities | Route Planning | 否 |
| GET | /api/cost/segments | Risk & Cost | 否 |
| GET | /api/geography/locations | Geospatial | 否 |
| GET | /api/geography/segments/{segment_id} | Geospatial | 否 |
| GET | /api/geography/zones | Geospatial | 否 |
| GET | /api/graph/summary |  | 否 |
| GET | /api/ports/weather-risks | Port Weather | 否 |
| GET | /api/ports/weather-risks/high | Port Weather | 否 |
| GET | /api/ports/{port_id}/traffic | AIS Port Traffic | 否 |
| GET | /api/ports/{port_id}/weather | Port Weather | 否 |
| GET | /api/ports/{port_id}/weather/history | Port Weather | 否 |
| GET | /api/providers/status | AIS Port Traffic | 否 |
| GET | /api/recommendations/{snapshot_id} | Route Planning | 否 |
| GET | /api/risk/news | Dynamic News Risk | 否 |
| GET | /api/risk/news/clusters | Dynamic News Risk | 否 |
| GET | /api/risk/news/zones | Dynamic News Risk | 否 |
| GET | /api/risk/overview |  | 否 |
| GET | /api/risk/segments | Risk & Cost | 否 |
| GET | /api/routes/nodes | Route Optimization | 否 |
| GET | /api/routes/optimize | Route Optimization | 否 |
| GET | /api/routes/recommend | Route Planning | 是 |
| POST | /api/routes/recommend | Route Planning | 否 |
| GET | /api/routes/recommendations | Route Optimization | 否 |
| GET | /api/routes/weather-risks | Route Weather | 否 |
| GET | /api/routes/weather-risks/{segment_id} | Route Weather | 否 |
| GET | /api/routes/{route_id} | Route Planning | 否 |
| GET | /api/suppliers | Route Planning | 否 |
| GET | /api/suppliers/{supplier_id}/origins | Route Planning | 否 |
| GET | /api/supply-chain/routes |  | 否 |
| POST | /api/v1/audit/source | 整车运输路径网络 | 否 |
| GET | /api/v1/config/strategy | 整车运输路径网络 | 否 |
| PUT | /api/v1/config/strategy | 整车运输路径网络 | 否 |
| GET | /api/v1/health | 整车运输路径网络 | 否 |
| POST | /api/v1/locations/ingest | 整车运输路径网络 | 否 |
| POST | /api/v1/routes/generate | 整车运输路径网络 | 否 |
| GET | /api/v1/routes/search | 整车运输路径网络 | 否 |
| DELETE | /api/v1/routes/{route_id} | 整车运输路径网络 | 否 |
| GET | /api/v1/routes/{route_id} | 整车运输路径网络 | 否 |
| POST | /api/v1/routes/{route_id}/review | 整车运输路径网络 | 否 |
| POST | /api/v1/routes/{route_id}/score/recompute | 整车运输路径网络 | 否 |
| GET | /api/vessels/{mmsi} | AIS Port Traffic | 否 |
| GET | /health |  | 否 |
| GET | /health/ais | AIS Port Traffic | 否 |
| GET | /health/aura |  | 否 |

## 7. 重复、孤立与连通性

| 问题 | 分组数量 |
|---|---|
| 重复地点标识 | 15 |
| 重复 RouteSegment 标识 | 0 |
| 重复 Route/VehicleRoute 标识 | 0 |
| 存在孤立节点的标签 | 5 |
| 路线图弱连通分量 | 1 |
| 最大弱连通分量节点数 | 283 |

### 7.1 孤立节点

| 标签 | 数量 | 示例 elementId |
|---|---|---|
| IngestionJob | 22 | 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:9477, 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:9478, 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:9479, 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:9480, 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:9481 |
| AisProviderState | 1 | 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:24091 |
| AutoPart | 1 | 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1065 |
| Component | 1 | 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1065 |
| RiskMatrixCategory | 1 | 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:75 |

## 8. 阶段 2 清理设计建议（未执行）

1. 以本报告的 `protected_realtime_baseline` 为保护清单，任何清理规则必须先排除实时 API 与有明确来源的数据。
2. 优先将 `fabricated_for_testing`、`synthetic`、`sample`、`mock` 和 `standard_skeleton_reference` 标记为候选，不要仅凭字段为空删除。
3. 对 `external_repos_reference_fields` 和 `Tesla SEC route skeleton` 先判定业务用途，再决定保留为 `estimated/reference` 或删除。
4. 实际清理前必须导出候选节点、关系、属性和删除原因，并执行 dry-run。
5. 无 Provider 的 0.5/50 风险应在风险迁移阶段改为 unavailable，而不是把整条路线直接删除。

## 9. 查询错误

本次审计查询全部成功。

## 10. 复现命令

```bash
python scripts/audit_current_backend.py
```

机器可读库存：`artifacts/database_inventory.json`。
