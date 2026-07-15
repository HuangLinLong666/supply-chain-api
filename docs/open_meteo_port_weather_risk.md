# Open-Meteo 全球港口准实时天气风险模块

## 1. 目标与架构

```text
Neo4j Port -> 坐标校验 -> Open-Meteo Forecast + Marine
-> 确定性风险评分 -> Port 当前状态 + WeatherRiskSnapshot
-> RouteSegment 独立天气风险 -> FastAPI
```

本模块复用现有 `Port` 和 `RouteSegment` 结构，不删除、不重建、不覆盖现有总风险。

## 2. Open-Meteo 接口

- Forecast API：温度、湿度、降水、能见度、风速、阵风和 WMO code。
- Marine API：浪高、浪向、周期、风浪和涌浪。Marine 失败不影响陆地天气写入。
- Geocoding API：仅用于缺失坐标候选查找；低置信度不自动写入。

`current` 是气象数值模型的当前状态，不是港口现场传感器秒级实测。Marine 沿岸精度有限，不可用于航海导航。

## 3. Neo4j 模型

`Port` 新增 `weather_risk_score/level/confidence/data_completeness/trend/summary/updated_at` 及当前气象字段。

```text
(Port)-[:HAS_WEATHER_SNAPSHOT]->(WeatherRiskSnapshot)
```

`snapshot_id=port_id|observed_at|scoring_version`，使用唯一约束和 `MERGE` 保证幂等。默认保留 30 天，用 `scripts/cleanup_weather_snapshots.py` 清理。

`RouteSegment` 独立保存 `origin_port_weather_risk`、`destination_port_weather_risk`、`route_weather_risk`、`route_weather_updated_at`，不覆盖 `riskScore`。

## 4. 风险公式

```text
wind 20% + gust 15% + precipitation 15% + visibility 15%
+ wave 25% + temperature 5% + weather_code 5%
```

0-24 LOW，25-49 MEDIUM，50-74 HIGH，75-100 CRITICAL。阈值与 WMO 映射位于 `config/weather_risk_rules.json`。缺失值不当作零风险，而是对有效维度重新归一化，同时降低 `data_completeness` 和 `confidence`。

## 5. 环境变量与运行

参见 `.env.example`。默认 60 分钟，批量 25，超时 20 秒，重试 3 次，指数退避。

```bash
pip install -r requirements.txt
python scripts/update_port_weather.py --dry-run
python scripts/update_port_weather.py --port-id CNSHA
uvicorn app.main:app --reload
pytest -q
```

`WEATHER_SCHEDULER_ENABLED=true` 启用 APScheduler，`max_instances=1` 防止重入。管理 API 需 `X-Weather-Admin-Token`。

## 6. HTTP API

- `GET /api/ports/weather-risks`
- `GET /api/ports/weather-risks/high`
- `GET /api/ports/{portId}/weather`
- `GET /api/ports/{portId}/weather/history`
- `POST /api/admin/weather/update`

## 7. 测试、验证与调整

Mock 测试不依赖真实 Open-Meteo；覆盖阈值、缺失值、预报窗口、429、超时和无效 JSON。Cypher 见 `cypher/validate_port_weather.cypher`。调整权重只修改 `config/weather_risk_rules.json`。

Aura 迁移时仅需替换 `AURA_NEO4J_*`；数据模型与 Cypher 保持不变。

## 8. 已完成与尚未完成

已完成：批量 Weather/Marine、重试、评分、幂等快照、路线传播、CLI、调度、API、清理和 mock 测试。

尚未完成：缺失坐标的自动高置信度写回和待人工确认文件；当前 18 个 `Port` 中已有坐标的港口可直接更新，缺失坐标者会安全跳过。工程阈值尚需真实港口运营数据校准。

## 9. 常见错误

- Marine 返回空：沿岸/陆地网格导致，陆地天气仍会保留。
- 429/5xx：客户端指数退避后重试。
- `INSUFFICIENT_DATA`：有效评分维度低于 40%。
- 管理 API 401：检查 `WEATHER_ADMIN_TOKEN` 和请求头。

## 10. 2026-07-12 实际验证结果

- AuraDB 港口：18；有坐标：17；缺失坐标：1（Tianjin Port / `CNTXG`）。
- 真实批量调用：Shanghai Port、Singapore Port、Rotterdam Port；Weather 1 次，Marine 1 次。
- 成功更新：3；失败：0；快照：3。第二次执行后快照仍为 3，幂等验证通过。
- 路线传播：Shanghai Port -> Singapore Port 海运段已写入 `route_weather_risk`。
- 实际结果：Shanghai 34.5 MEDIUM，Singapore 10.0 LOW，Rotterdam 7.8 LOW。该数值是本次模型预报快照，会随时间变化。
- Tianjin Port 已调用 Geocoding，返回城市候选而非经验证港口坐标，未写库；候选保存于 `docs/unresolved_port_coordinates.json`。
- 单元测试：22 passed，0 failed；FastAPI `/health`、天气列表、单港查询和管理鉴权已验证。
- 本机 `pip install -r requirements.txt` 因 Anaconda 用户目录权限无法写入 APScheduler；代码会安全禁用未安装的调度器。Render 构建时按 `requirements.txt` 安装后可启用自动调度。
