# 阶段 4：真实 Provider 风险清理与重算报告

> 生成时间（UTC）：`2026-07-26T03:05:11.610764+00:00`  
> 模式：`dry-run`  
> 评分版本：`provider-risk-v1`  
> 数据库：`94a63264`

## 1. 结论

- 执行状态：`dry_run`。
- 无 Provider 的合成 `RiskFactor` 删除候选：**0**。
- 需按运输方式重算的 `RouteSegment`：**0**。
- 需清空无来源通用风险字段的其他节点：**0**。
- 计划指纹：`ce075d1974ab7848917ee496afb8abbd3e8c91ecbf18c880dccdae231ceb100e`。

## 2. 安全边界

- 只允许删除经过来源标记、Provider、Evidence、审核状态和邻接实时数据五项检查的 `RiskFactor`。
- 不删除路线、路段、供应商、地点、GDELT、Open-Meteo、AIS、Shipment 或任何实时观测节点。
- 所有被删节点、关系、被清空字段和重算前属性都先写入 JSON 备份。
- 写事务结束前会复核实时标签数量；任一数量下降则整个事务回滚。
- 风险缺失写为 `null/unavailable`，不再写入 `0.5` 或 `50` 作为中性默认值。

## 3. 清理统计

| 项目 | 迁移前 | 迁移后 |
|---|---|---|
| 节点总数 | 14624 | 未执行 |
| 关系总数 | 22497 | 未执行 |
| RiskFactor | 0 | 未执行 |
| 无 Provider RiskFactor | 0 | 未执行 |
| 仍含旧风险字段节点 | 0 | 未执行 |

## 4. RiskFactor disposition

_无记录_

## 5. 分运输方式重算

_无记录_

- 海运：可使用 Open-Meteo 天气和 GDELT 地缘政治；没有 Provider 的海盗、拥堵、制裁、班期不参与。
- 铁路：可使用 Open-Meteo 天气和 GDELT 地缘政治；没有 Provider 的边境、基础设施、班期、制裁不参与。
- 公路：当前只接受 Open-Meteo 天气；GDELT 新闻不会被错误映射成道路交通或边境风险。
- 空运：可使用 Open-Meteo 天气和 GDELT 空域冲突；没有 Provider 的容量、班期、制裁、装卸不参与。
- `multimodal`、`delivery` 等尚未配置可信维度时返回不可用，不伪造风险。

## 6. 数据完整度

- `provider_risk_data_completeness` = 已有真实 Provider 的适用权重 / 当前运输方式全部权重。
- 只对可用维度重新归一化计算风险分；完整度单独返回，避免把缺失误当成低风险。
- `provider_risk_missing_factors` 会列出仍缺 Provider 的维度。

## 7. 实时数据保护基线

| 标签 | 迁移前 | 迁移后 |
|---|---|---|
| NewsRiskEvent | 11946 | 未执行 |
| NewsRiskZone | 10 | 未执行 |
| PortTrafficSnapshot | 0 | 未执行 |
| Shipment | 1 | 未执行 |
| Vessel | 1 | 未执行 |
| VesselObservation | 1 | 未执行 |
| WeatherRiskSnapshot | 3 | 未执行 |

## 8. Provider 风险观测

| Provider | 迁移前 | 迁移后 |
|---|---|---|
| GDELT | 11946 | 未执行 |
| unattributed | 40 | 未执行 |
| Open-Meteo | 3 | 未执行 |

## 9. 产物与命令

- 完整 JSON 备份：`artifacts/risk_cleanup_backup_20260726T030511Z.json`。
- 中文统计报告：`artifacts/risk_recalculation_report_20260726T030511Z.md`。

```bash
python scripts/recalculate_provider_risk.py --dry-run
python scripts/recalculate_provider_risk.py --execute --confirm CLEAN_AND_RECALCULATE_PROVIDER_RISK_V1
```
