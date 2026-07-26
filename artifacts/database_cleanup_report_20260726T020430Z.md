# Neo4j 合成数据安全清理报告

> 生成时间（UTC）：`2026-07-26T02:04:30.453492+00:00`  
> 模式：`dry-run`  
> 数据库：`94a63264`  
> 清理版本：`synthetic-cleanup-v1`

## 1. 执行结论

- 本次仅执行 **dry-run**，数据库新增、修改、删除均为 **0**。
- 共匹配 **2184** 个节点。
- 硬保护节点 **1** 个。
- 因保护关系、保留边界或依赖传播而阻止 **2181** 个。
- 最终可删除候选 **2** 个。

## 2. 过滤条件

| 类别 | 值 |
|---|---|
| source | fabricated_for_testing, synthetic, sample, mock, standard_skeleton_reference, external_repos_reference_fields, tesla sec route skeleton |
| label | 未限制 |
| integration_id | 未限制 |
| 是否使用默认来源标记 | True |
| 匹配语义 | 不同过滤类别之间为 AND，同一类别内为 OR，字符串匹配不区分大小写 |

## 3. 安全规则

- `NewsRiskEvent`、`NewsRiskZone`、`WeatherRiskSnapshot`、`Vessel`、`VesselObservation`、`PortTrafficSnapshot` 和 `Shipment` 永不进入删除集合。
- GDELT、Open-Meteo、AISStream、官方注册表、官方班期和已审核数据受到来源保护。
- 与受保护节点相连的候选节点会被阻止，阻止状态会沿候选子图传播，避免只删除风险/成本子节点而破坏保留路线。
- 默认不允许候选节点删除与保留节点之间的边界关系；只有显式传入 `--allow-boundary-links` 才会放宽普通边界，但实时保护边界永不放宽。
- 执行删除必须显式提供至少一个 `--source`、`--label` 或 `--integration-id`，同时提供 `--execute --confirm DELETE_SYNTHETIC_ONLY`，并且不能超过 `--max-delete` 上限。

## 4. 匹配范围与阻止原因

### 4.1 全部匹配节点标签

| 标签 | 数量 |
|---|---|
| RouteSegment | 594 |
| RouteCostObservation | 586 |
| RouteDelayObservation | 586 |
| InventoryRecord | 48 |
| ProductionPlan | 36 |
| SalesOrder | 36 |
| DeliveryRecord | 36 |
| AutoPart | 31 |
| Component | 31 |
| Part | 30 |
| RiskEvent | 20 |
| RiskFactor | 20 |
| Country | 17 |
| PortObservation | 17 |
| CountryRiskObservation | 17 |
| Supplier | 16 |
| Port | 13 |
| Factory | 12 |
| TimePeriod | 12 |
| FactoryIssue | 12 |
| TransitHub | 11 |
| DestinationCountry | 10 |
| OverseasWarehouse | 10 |
| ChinaOrigin | 8 |
| ExportWarehouse | 8 |
| VehicleSystem | 8 |
| Feature | 8 |
| Warehouse | 6 |
| Route | 6 |
| Market | 6 |
| RailTerminal | 5 |
| VehicleModel | 5 |
| CarModel | 5 |
| Product | 5 |
| OEM | 5 |
| RiskMatrixCategory | 4 |
| TransportLocation | 3 |
| Customer | 3 |

### 4.2 全部匹配节点来源

| 来源 | 数量 |
|---|---|
| external_repos_reference_fields | 1695 |
| existing_neo4j_graph | 711 |
| synthetic_supplygraph_style | 190 |
| standard_skeleton_reference | 113 |
| synthetic_experiment | 99 |
| synthetic_cross_border_transport_risk | 80 |
| Tesla SEC route skeleton | 11 |
| risk_matrix_route_recommendation_system_v1 | 4 |
| fabricated_for_testing | 2 |
| 项目内置地点示例数据 | 2 |
| Open-Meteo Forecast API | 1 |
| SEC EDGAR | 1 |

### 4.3 硬保护原因

| 原因 | 数量 |
|---|---|
| protected_source:open-meteo | 1 |

### 4.4 阻止原因

| 原因 | 数量 |
|---|---|
| depends_on_blocked_candidate | 1417 |
| retained_boundary_link:AFFECTS | 591 |
| retained_boundary_link:USES_MODE | 586 |
| retained_boundary_link:HAS_RISK | 585 |
| retained_boundary_link:TO_NODE | 309 |
| retained_boundary_link:TO | 307 |
| retained_boundary_link:FROM_NODE | 246 |
| retained_boundary_link:FROM | 241 |
| protected_link:TO | 50 |
| protected_link:TO_NODE | 48 |
| retained_boundary_link:CAN_BE_TRANSPORTED_BY | 31 |
| protected_link:CAN_BE_TRANSPORTED_BY | 30 |
| retained_boundary_link:STORED_AT | 30 |
| retained_boundary_link:FOR_MODEL | 23 |
| retained_boundary_link:ORDERS_MODEL | 18 |
| retained_boundary_link:PRODUCES | 16 |
| retained_boundary_link:SHIPS_FROM | 15 |
| protected_link:SUPPLIES_TO_FACTORY | 12 |
| retained_boundary_link:SUPPLIES | 12 |
| protected_link:ALIAS_OF | 11 |
| protected_link:ROUTE_TO | 11 |
| protected_link:CONNECTED_TO | 10 |
| protected_link:LOCATED_IN | 8 |
| protected_link:FROM | 8 |
| retained_boundary_link:TRUCK_ROUTE | 7 |
| protected_link:HAS_OBSERVATION | 7 |
| protected_link:EXPOSED_TO_NEWS_RISK | 6 |
| protected_link:FROM_NODE | 6 |
| retained_boundary_link:HAS_DELAY_OBSERVATION | 6 |
| protected_link:ALIGNED_TO | 5 |
| retained_boundary_link:EXPORTED_TO | 5 |
| retained_boundary_link:AFFECTS_MODEL | 5 |
| retained_boundary_link:AIR_ROUTE | 4 |
| retained_boundary_link:ASSESSES_FACTORY | 4 |
| retained_boundary_link:ASSESSES_MARKET | 4 |
| retained_boundary_link:ASSESSES_PLAN | 4 |
| retained_boundary_link:ASSESSES_ORDER | 4 |
| retained_boundary_link:ASSESSES_DELIVERY | 4 |
| protected_link:TRUCK_ROUTE | 3 |
| retained_boundary_link:SUPPORTED_BY_SEC_CANDIDATE | 3 |
| retained_boundary_link:USES_SEC_SUPPLIER_CANDIDATE | 3 |
| retained_boundary_link:INGESTED | 2 |
| retained_boundary_link:ORIGIN | 2 |
| retained_boundary_link:SERVES_SEC_FACTORY | 2 |
| retained_boundary_link:ENDS_AT | 2 |
| retained_boundary_link:USES_INVENTORY | 2 |
| protected_link:USES_ROUTE | 1 |
| protected_link:SEA_ROUTE | 1 |
| retained_boundary_link:STARTS_FROM | 1 |
| retained_boundary_link:DISCLOSED_IN | 1 |
| retained_boundary_link:OPERATES_FACTORY | 1 |
| retained_boundary_link:SUPPORTS | 1 |
| retained_boundary_link:USES_FACTORY_ISSUE | 1 |

## 5. 最终删除候选分布

### 5.1 标签

| 标签 | 数量 |
|---|---|
| AutoPart | 1 |
| Component | 1 |
| RiskMatrixCategory | 1 |

### 5.2 来源

| 来源 | 数量 |
|---|---|
| existing_neo4j_graph | 1 |
| standard_skeleton_reference | 1 |
| risk_matrix_route_recommendation_system_v1 | 1 |
| synthetic_cross_border_transport_risk | 1 |

## 6. 实时数据保护基线

| 标签 | 清理前 | 清理后 |
|---|---|---|
| NewsRiskEvent | 11946 | 未执行 |
| NewsRiskZone | 10 | 未执行 |
| PortTrafficSnapshot | 0 | 未执行 |
| Shipment | 1 | 未执行 |
| Vessel | 1 | 未执行 |
| VesselObservation | 1 | 未执行 |
| WeatherRiskSnapshot | 3 | 未执行 |

## 7. 样例

### 7.1 硬保护节点

| elementId | 标签 | 业务标识 | 原因 |
|---|---|---|---|
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:19 | Port, TransportLocation | {"location_id": "CN-SHA", "unlocode": "CNSHA", "name": "Shanghai Port"} | protected_source:open-meteo |

### 7.2 被阻止候选

| elementId | 标签 | 业务标识 | 原因 |
|---|---|---|---|
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:0 | ChinaOrigin | {"name": "Yiwu"} | depends_on_blocked_candidate:4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:11 |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:10 | ChinaOrigin | {"name": "Xi'an"} | depends_on_blocked_candidate:4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:18 |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:100 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:16_TRUCK_ROUTE_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:54_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:23", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:16_TRUCK_ROUTE_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:54_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:23"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:USES_MODE:TransportMode |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1000 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:352_EXPORTED_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:1307_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:194", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:352_EXPORTED_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:1307_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:194"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:TO:Destination |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1001 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:352_EXPORTED_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:1308_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:195", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:352_EXPORTED_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:1308_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:195"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:TO:Destination |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1002 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:353_EXPORTED_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:1309_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:195", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:353_EXPORTED_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:1309_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:195"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:TO:Destination |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1003 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:353_EXPORTED_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:1310_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:196", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:353_EXPORTED_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:1310_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:196"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:TO:Destination |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1004 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:354_EXPORTED_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:1311_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:196", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:354_EXPORTED_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:1311_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:196"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:TO:Destination |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1005 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:354_EXPORTED_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:1312_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:199", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:354_EXPORTED_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:1312_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:199"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:TO:Destination |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1006 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:804_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2405_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:678", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:804_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2405_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:678"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:USES_MODE:TransportMode |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1007 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:805_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2406_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:679", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:805_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2406_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:679"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:USES_MODE:TransportMode |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1008 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:806_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2407_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:680", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:806_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2407_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:680"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:USES_MODE:TransportMode |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1009 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:807_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2408_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:681", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:807_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2408_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:681"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:USES_MODE:TransportMode |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:101 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:16_TRUCK_ROUTE_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:55_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:24", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:16_TRUCK_ROUTE_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:55_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:24"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:USES_MODE:TransportMode |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1010 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:808_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2409_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:682", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:808_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2409_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:682"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:USES_MODE:TransportMode |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1011 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:809_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2410_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:683", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:809_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2410_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:683"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:USES_MODE:TransportMode |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1012 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:810_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2411_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:678", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:810_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2411_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:678"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:USES_MODE:TransportMode |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1013 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:811_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2412_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:679", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:811_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2412_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:679"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:USES_MODE:TransportMode |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1014 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:812_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2413_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:680", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:812_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2413_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:680"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:USES_MODE:TransportMode |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1015 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:813_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2414_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:681", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:813_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2414_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:681"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:USES_MODE:TransportMode |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1016 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:814_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2415_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:682", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:814_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2415_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:682"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:USES_MODE:TransportMode |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1017 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:815_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2416_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:683", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:815_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2416_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:683"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:USES_MODE:TransportMode |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1018 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:816_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2417_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:678", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:816_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2417_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:678"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:USES_MODE:TransportMode |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1019 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:817_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2418_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:679", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:817_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2418_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:679"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:USES_MODE:TransportMode |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:102 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:17_TRUCK_ROUTE_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:56_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:19", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:17_TRUCK_ROUTE_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:56_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:19"} | protected_link:TO:protected_source:open-meteo; protected_link:TO_NODE:protected_source:open-meteo; retained_boundary_link:AFFECTS:RiskFactor |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1020 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:818_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2419_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:680", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:818_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2419_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:680"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:USES_MODE:TransportMode |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1021 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:819_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2420_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:681", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:819_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2420_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:681"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:USES_MODE:TransportMode |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1022 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:820_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2421_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:682", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:820_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2421_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:682"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:USES_MODE:TransportMode |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1023 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:821_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2422_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:683", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:821_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2422_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:683"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:USES_MODE:TransportMode |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1024 | RouteSegment | {"segment_id": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:822_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2423_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:678", "segmentId": "4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:822_DELIVERS_TO_5:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:2423_4:ebf40b73-e2e1-4a8f-a9a3-b63be29de65a:678"} | retained_boundary_link:AFFECTS:RiskFactor; retained_boundary_link:HAS_RISK:RiskFactor; retained_boundary_link:USES_MODE:TransportMode |

### 7.3 可删除候选

| elementId | 标签 | 业务标识 | 匹配原因 |
|---|---|---|---|
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:1065 | AutoPart, Component | {"name": "EV Battery Pack"} | source:standard_skeleton_reference |
| 4:7d366176-8bfa-4487-95a3-b7b387cf8eb9:75 | RiskMatrixCategory | {"name": "High Cost - Low Risk"} | source:synthetic |

## 8. 备份与恢复信息

- 机器可读备份：`artifacts/database_cleanup_backup_20260726T020430Z.json`。
- 本报告：`artifacts/database_cleanup_report_20260726T020430Z.md`。
- 备份包含所有匹配节点及其 disposition，以及可删除候选附着的 **0** 条关系。
- 删除计划指纹（SHA-256）：`2da47fb92498a31dbc7ce8b85acc22426e1911ef33e919b6627334c11d1a9103`。
- 节点和关系属性中的密码、令牌、API Key、认证信息与数据库连接 URI 会在备份中替换为 `[REDACTED]`。
- 本阶段只设计和预览清理；恢复脚本将在确认实际清理方案后再实现，避免对错误候选产生二次写入。

## 9. 命令

默认安全预览：

```bash
python scripts/cleanup_synthetic_data.py --dry-run
```

按来源、标签和 integration_id 缩小范围：

```bash
python scripts/cleanup_synthetic_data.py --dry-run \
  --source standard_skeleton_reference \
  --label RouteSegment \
  --integration-id your-integration-id
```

实际执行示例（本报告未执行）：

```bash
python scripts/cleanup_synthetic_data.py --execute \
  --source standard_skeleton_reference \
  --confirm DELETE_SYNTHETIC_ONLY \
  --max-delete 100
```
