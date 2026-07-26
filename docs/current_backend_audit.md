# 当前后端与 AuraDB 只读审计报告

> 生成时间（UTC）：`2026-07-25T10:23:43.119275+00:00`  
> 审计版本：`backend-audit-v1`  
> 数据库：`94a63264`  
> 本报告由只读 Neo4j 会话生成；本阶段未执行新增、修改或删除。

## 1. 审计结论

- **高** `NONCANONICAL_ROUTE_MODES`：535 个分段使用非 road/rail/sea/air 模式。
- **严重** `BROKEN_SEGMENT_ENDPOINTS`：21 个分段缺少或重复 FROM_NODE/TO_NODE。
- **高** `NEUTRAL_DEFAULT_RISK`：594 个分段风险明细包含 0.5/50 默认值，需要在后续迁移中核验 Provider。
- **高** `MISSING_SEGMENT_PROVIDER`：仅 0/594 个分段具有 provider 字段。
- **中** `LOW_ROUTE_WEATHER_COVERAGE`：仅 1/594 个分段具有 route_weather_risk。
- **高** `MISSING_ROUTE_GEOMETRY`：仅 0/594 个分段具有可审计的路线 geometry。
- **高** `REFERENCE_ROUTE_DATA`：594/594 个分段来源属于合成、骨架或外部参考字段，不能视为已验证运输服务。
- **高** `MISSING_LOCATION_COORDINATES`：地点坐标覆盖率为 21/111。
- **高** `AIS_NOT_OPERATIONAL`：AIS 船位观测不足，不能据此计算实时港口拥堵。
- **高** `SUPPLIER_ORIGIN_CHAIN_MISSING`：仅 0/29 个供应商具有 SHIPS_FROM→设施→HAS_ACCESS_LEG 链。

## 2. 数据库总览

| 指标 | 数量 |
|---|---|
| 节点 | 14729 |
| 关系 | 22997 |
| 节点标签种类 | 67 |
| 关系类型种类 | 105 |
| 约束 | 72 |
| 索引 | 82 |

### 2.1 主要节点标签

| 标签 | 数量 |
|---|---|
| NewsRiskEvent | 11439 |
| RouteDelayObservation | 628 |
| RiskFactor | 612 |
| RouteSegment | 594 |
| RouteCostObservation | 586 |
| PortObservation | 107 |
| InventoryRecord | 48 |
| AutoPart | 41 |
| Component | 41 |
| IngestionJob | 41 |
| Part | 40 |
| DeliveryRecord | 36 |
| ProductionPlan | 36 |
| SalesOrder | 36 |
| EntityAlias | 34 |
| Supplier | 29 |
| Route | 26 |
| CostEstimate | 23 |
| RiskSnapshot | 23 |
| Port | 20 |
| RiskEvent | 20 |
| VehicleRoute | 20 |
| Factory | 19 |
| RouteLeg | 19 |
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
| NewsRiskZone | 10 |
| OverseasWarehouse | 10 |
| ChinaOrigin | 8 |
| DeparturePoint | 8 |
| DepartureWarehouse | 8 |
| EntryPoint | 8 |
| ExportWarehouse | 8 |
| TransferPoint | 8 |
| TransportLocation | 7 |

### 2.2 主要关系类型

| 关系类型 | 数量 |
|---|---|
| AFFECTS_ZONE | 14301 |
| HAS_RISK | 650 |
| HAS_DELAY_OBSERVATION | 628 |
| AFFECTS | 625 |
| FROM_NODE | 592 |
| TO_NODE | 592 |
| FROM | 590 |
| TO | 590 |
| HAS_COST_OBSERVATION | 586 |
| USES_MODE | 586 |
| CLASSIFIED_AS | 565 |
| USED_IN | 226 |
| PRODUCES | 182 |
| SUPPLIES_TO_FACTORY | 175 |
| IN_PERIOD | 132 |
| CAN_BE_TRANSPORTED_BY | 120 |
| HAS_OBSERVATION | 108 |
| TRANSPORT | 107 |
| HAS_SYSTEM | 78 |
| WITH_FEATURE | 78 |
| EXPOSED_TO_NEWS_RISK | 66 |
| LOCATED_IN | 54 |
| SUPPLIES | 53 |
| FOR_PART | 48 |
| HAS_INVENTORY | 48 |
| IS_COMPOSED_OF | 44 |
| REQUIRES_PART | 44 |
| IS_SUPPLIED_BY | 40 |
| STORED_AT | 40 |
| SUPPLIED_BY | 40 |
| ROUTE_TO | 38 |
| DELIVERED_TO | 36 |
| DELIVERS_TO | 36 |
| FOR_MODEL | 36 |
| FULFILLED_BY | 36 |
| HAS_PRODUCTION_PLAN | 36 |
| PLACES_ORDER | 36 |
| TARGET_MARKET | 36 |
| ALIAS_OF | 34 |
| CONNECTED_TO | 33 |
| GENERATED | 33 |
| LOCATED_AT | 32 |
| EXPORTED_TO | 30 |
| ORDERS_MODEL | 30 |
| HAS_SEGMENT | 29 |
| SHIPS_FROM | 29 |
| HAS_COST_ESTIMATE | 23 |
| HAS_RISK_SNAPSHOT | 23 |
| INGESTED | 23 |
| DESTINATION | 20 |

## 3. 关键业务实体

| 标签 | 数量 |
|---|---|
| Airport | 16 |
| Factory | 19 |
| NewsRiskZone | 10 |
| Port | 20 |
| RailTerminal | 5 |
| RiskSnapshot | 23 |
| Route | 26 |
| RouteCostObservation | 586 |
| RouteDelayObservation | 628 |
| RouteLeg | 19 |
| RouteRecommendation | 15 |
| RouteSegment | 594 |
| SourceEvidence | 11 |
| Supplier | 29 |
| TransportLocation | 7 |
| VehicleRoute | 20 |
| Vessel | 1 |
| VesselObservation | 1 |
| Warehouse | 6 |

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
| 111 | 111 | 21 | 21 | 0 | 18.9% |

| 标签 | 数量 | 有名称 | 有坐标 | 坐标覆盖率 |
|---|---|---|---|---|
| Airport | 16 | 16 | 2 | 12.5% |
| DeparturePoint | 8 | 8 | 0 | 0.0% |
| Destination | 10 | 10 | 0 | 0.0% |
| EntryPoint | 8 | 8 | 0 | 0.0% |
| Factory | 19 | 19 | 0 | 0.0% |
| Port | 20 | 20 | 19 | 95.0% |
| RailTerminal | 5 | 5 | 0 | 0.0% |
| TransferPoint | 8 | 8 | 0 | 0.0% |
| TransitHub | 11 | 11 | 0 | 0.0% |
| TransportLocation | 7 | 7 | 7 | 100.0% |
| Warehouse | 6 | 6 | 0 | 0.0% |

## 5. RouteSegment 审计

| 指标 | 数量 | 覆盖率 |
|---|---|---|
| 分段总数 | 594 | 100.0% |
| 距离 | 594 | 100.0% |
| 时效 | 594 | 100.0% |
| 成本 | 594 | 100.0% |
| 基础风险 | 594 | 100.0% |
| 动态风险 | 573 | 96.5% |
| 新闻风险 | 573 | 96.5% |
| 有效期内新闻风险 | 573 | 96.5% |
| 天气风险 | 1 | 0.2% |
| 风险明细 | 594 | 100.0% |
| 路线 geometry | 0 | 0.0% |
| 起终点均有坐标 | 2 | 0.3% |
| Provider | 0 | 0.0% |
| 来源 URL | 0 | 0.0% |
| 时间字段 | 0 | 0.0% |
| 置信度 | 586 | 98.7% |
| 包含 0.5/50 默认风险 | 594 | 100.0% |

### 5.1 运输方式

| mode | 数量 |
|---|---|
| multimodal | 390 |
| delivery | 82 |
| truck | 63 |
| road | 21 |
| sea | 18 |
| rail | 10 |
| air | 10 |

### 5.2 分段来源

| 来源 | 数量 |
|---|---|
| external_repos_reference_fields | 565 |
| standard_skeleton_reference | 21 |
| Tesla SEC route skeleton | 8 |

### 5.3 数据来源质量分类

| 分类 | 数量 |
|---|---|
| synthetic_or_reference | 594 |

### 5.4 结构异常

- 缺少或重复 `FROM_NODE/TO_NODE` 的分段：**21**。
- 使用非标准 mode 的分段：**535**。
- 包含可疑 0.5/50 默认风险的分段：**594**。

## 6. 实时与观测数据基线

> 这些记录是后续清理阶段必须优先保护的数据基线。数量为 0 不代表 Provider 不可接入，只表示当前 AuraDB 没有相应记录。

| 标签 | 数量 | 最近时间 | 来源 |
|---|---|---|---|
| NewsRiskEvent | 11439 | 2026-07-25T09:30:00.000000000+00:00 | GDELT DOC 2.0 API |
| NewsRiskZone | 10 | 2026-07-25T10:05:50.578478000+00:00 | GDELT DOC 2.0 API |
| WeatherRiskSnapshot | 3 | 2026-07-12T14:45:00.000000000+00:00 | Open-Meteo Forecast API |
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
| AISStream.io | missing | 否 | — |
| MarineTraffic | disabled_stub | 否 | app/vehicle_network/providers/stubs.py |
| OpenSky | disabled_stub | 否 | app/vehicle_network/providers/stubs.py |
| Aviation Edge | disabled_stub | 否 | app/vehicle_network/providers/stubs.py |
| FlightAware | disabled_stub | 否 | app/vehicle_network/providers/stubs.py |
| Cirium | disabled_stub | 否 | app/vehicle_network/providers/stubs.py |

### 6.2 当前 FastAPI 路由

当前应用共注册 **27** 个 HTTP 路由（包含 `/docs` 等框架路由）。

| 方法 | 路径 | 标签 | deprecated |
|---|---|---|---|
| GET | / | Service | 否 |
| POST | /api/admin/gdelt/update | Dynamic News Risk Admin | 否 |
| POST | /api/admin/weather/update | Port Weather Admin | 否 |
| GET | /api/cities | Route Planning | 否 |
| GET | /api/cost/segments | Risk & Cost | 否 |
| GET | /api/graph/summary |  | 否 |
| GET | /api/ports/weather-risks | Port Weather | 否 |
| GET | /api/ports/weather-risks/high | Port Weather | 否 |
| GET | /api/ports/{port_id}/weather | Port Weather | 否 |
| GET | /api/ports/{port_id}/weather/history | Port Weather | 否 |
| GET | /api/risk/news | Dynamic News Risk | 否 |
| GET | /api/risk/news/zones | Dynamic News Risk | 否 |
| GET | /api/risk/overview |  | 否 |
| GET | /api/risk/segments | Risk & Cost | 否 |
| GET | /api/routes/nodes | Route Optimization | 否 |
| GET | /api/routes/optimize | Route Optimization | 否 |
| GET | /api/routes/recommend | Route Planning | 否 |
| GET | /api/routes/recommendations | Route Optimization | 否 |
| GET | /api/suppliers | Route Planning | 否 |
| GET | /api/supply-chain/routes |  | 否 |
| GET, HEAD | /docs |  | 否 |
| GET, HEAD | /docs/oauth2-redirect |  | 否 |
| GET | /favicon.ico |  | 否 |
| GET | /health |  | 否 |
| GET | /health/aura |  | 否 |
| GET, HEAD | /openapi.json |  | 否 |
| GET, HEAD | /redoc |  | 否 |

## 7. 重复、孤立与连通性

| 问题 | 分组数量 |
|---|---|
| 重复地点标识 | 15 |
| 重复 RouteSegment 标识 | 0 |
| 重复 Route/VehicleRoute 标识 | 0 |
| 存在孤立节点的标签 | 4 |
| 路线图弱连通分量 | 2 |
| 最大弱连通分量节点数 | 275 |

### 7.1 孤立节点

| 标签 | 数量 | 示例 elementId |
|---|---|---|
| IngestionJob | 22 | 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:9477, 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:9478, 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:9479, 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:9480, 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:9481 |
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
