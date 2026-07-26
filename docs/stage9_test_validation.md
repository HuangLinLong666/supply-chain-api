# 阶段 9：测试与真实数据验证

> 评分版本：`route-recommendation-v1.2`  
> 验证脚本：`scripts/validate_stage9_data.py`  
> AuraDB 结果：`artifacts/stage9_data_validation_20260726T110331Z.json`

## 1. 本阶段完成内容

阶段 9 没有清理或导入数据库数据，重点是把总任务中的 15 条验收标准转换成可重复执行的自动测试，并使用真实 AuraDB 做只读验证。

本阶段还修复了测试发现的两个接口契约问题：

1. Provider 缺失但仍残留数值的风险，在进入推荐前强制变为 `riskScore=null`、`status=unavailable`，并写入 `missingData`；
2. 路线端点名称为空时按 `city → node id` 兜底，`coordinateSource` 和 `coordinateStatus` 为空时明确返回 `unavailable`。

## 2. 15 条验收测试矩阵

| # | 验收内容 | 主要自动测试 |
|---:|---|---|
| 1 | 不生成跨太平洋铁路 | `test_01_transpacific_rail_is_never_generated` |
| 2 | 不生成跨海洋公路 | `test_02_cross_ocean_road_is_invalidated_and_excluded` |
| 3 | 无 Provider 风险不评分 | `test_03_providerless_risk_is_removed_before_scoring` |
| 4 | 天气变化改变排序 | `test_04_weather_change_reorders_recommendations` |
| 5 | GDELT 高风险触发绕行 | `test_05_gdelt_high_risk_zone_triggers_reroute` |
| 6 | AIS 缺失不伪造拥堵 | `test_06_missing_ais_never_creates_congestion_risk` |
| 7 | 有效 AIS 影响海运排序 | `test_07_valid_ais_congestion_changes_sea_route_ranking` |
| 8 | 四种策略排名不同 | `test_08_all_four_strategies_produce_distinct_rankings` |
| 9 | 供应商不能用无关起点 | `test_09_supplier_cannot_use_unrelated_origin` |
| 10 | 节点有名称和坐标状态 | `test_10_every_returned_node_has_name_and_coordinate_status` |
| 11 | 风险因子有状态和来源 | `test_11_every_risk_factor_has_status_and_valid_source_semantics` |
| 12 | 清理重复执行安全 | `test_12_cleanup_reexecution_is_idempotent_and_preserves_realtime` |
| 13 | 导入使用稳定 MERGE 身份 | `test_13_realtime_import_writers_use_stable_merge_identities` |
| 14 | 旧 GET 接口仍存在 | `test_14_legacy_recommendation_api_remains_available` |
| 15 | 新 POST 符合 OpenAPI | `test_15_post_recommendation_api_matches_openapi_models` |

这些集中验收测试位于 `tests/test_stage9_acceptance.py`。原有测试仍继续验证天气采样、GDELT 聚类、AIS 消息解析、地理迁移、清理保护和推荐快照。

## 3. 如何运行测试

在项目根目录执行：

```bash
cd /Users/vegeta/全球供应链管理/supply-chain-api
.venv/bin/python -m pytest -q
```

当前结果：

```text
152 passed in 1.11s
```

项目没有配置 Ruff、Black、Mypy、Flake8 或 Pyright，因此本阶段没有虚构静态检查命令。额外执行了 `python -m py_compile` 和 `git diff --check`。

## 4. 如何运行 AuraDB 只读验证

确认 `.env` 已配置 AuraDB 后执行：

```bash
.venv/bin/python scripts/validate_stage9_data.py
```

也可以执行：

```bash
make validate-stage9
```

脚本默认验证：

```text
supplierId = SUP-CATL
origin = Shanghai
destination = Hamburg
```

如果要验证其他组合：

```bash
.venv/bin/python scripts/validate_stage9_data.py \
  --supplier-id SUP-CATL \
  --origin Shanghai \
  --destination Hamburg
```

脚本会拒绝包含 `CREATE/MERGE/SET/DELETE` 等关键词的 Cypher，只执行读查询，不会创建 `RecommendationSnapshot`。

## 5. 当前 AuraDB 结果

| 项目 | 结果 |
|---|---:|
| 节点 | 24,135 |
| 关系 | 47,866 |
| RouteSegment 重复业务 ID | 0 |
| 带风险分的 RouteSegment | 18 |
| 其中缺少 Provider | 0 |
| TransportLocation | 102 |
| 有名称和坐标状态 | 102 |
| 有经纬度 | 89 |
| Supplier | 29 |
| 有 SHIPS_FROM | 25 |
| Open-Meteo 有效风险分段 | 18 |
| GDELT 有效风险分段 | 0 |
| PortTrafficSnapshot | 0 |
| 只读推荐返回路线 | 5 |

综合结果是：`6 passed / 3 warnings / 0 failed`。

## 6. 三项警告是什么意思

### 6.1 四个供应商没有 SHIPS_FROM

这些供应商暂时不能用于路径推荐。接口会返回明确错误，不会把它们和任意城市组合。后续需要真实工厂或仓库来源才能补关系。

### 6.2 当前 GDELT 路线风险已过期

算法和绕行测试正常，但 AuraDB 当前没有未过期的 GDELT 路线风险。需要手动运行 GitHub Actions 的 `Update GDELT route risk`，或等待下一次定时任务。过期期间新闻风险为 unavailable。

### 6.3 当前没有真实 AIS 快照

数据库中 `PortTrafficSnapshot=0`。只有 Render AIS worker 收到真实位置消息并完成时间窗口聚合后，港口拥堵才会参与海运评分。当前返回 unavailable 是正确行为。

## 7. 数据库修改统计

- 新增节点：0；
- 修改节点：0；
- 删除节点：0；
- 新增关系：0；
- 修改关系：0；
- 删除关系：0。

阶段 9 生成的 JSON 是本地验证产物，不是 Neo4j 数据。阶段 8 的 3 个推荐快照和 12 条 `INCLUDED_IN` 关系保持不变。
