# 阶段 4：基于真实 Provider 的风险清理与重算

> 实际执行时间：2026-07-26  
> 评分版本：`provider-risk-v1`  
> 执行报告：`artifacts/risk_recalculation_report_20260726T030321Z.md`  
> 完整备份：`artifacts/risk_cleanup_backup_20260726T030321Z.json`  
> 最终幂等验证：`artifacts/risk_recalculation_report_20260726T032202Z.md`

## 1. 本阶段解决的问题

旧数据把合成或派生的风险值直接写入 `RiskFactor`、`riskScore`、`base_risk_score`、`supplier_risk` 等字段。它们没有真实 Provider、来源 URL 或 Evidence，却会被路径算法当成真实风险，其中部分接口还会把缺失值默认为 `0.5` 或 `50`。

阶段 4 完成以下修改：

1. 备份并删除明确无 Provider、无 Evidence、未审核且带合成来源标记的 `RiskFactor`。
2. 清空供应商、地点、路线和旧风险快照中的无来源通用风险字段。
3. 按海运、铁路、公路、空运分别选择适用风险维度。
4. 只使用处于有效期内且带真实 Provider 的 GDELT、Open-Meteo 信号。
5. 增加风险状态、数据完整度、缺失维度、Provider 和 Evidence 字段。
6. 风险缺失时返回 `null/unavailable`，排序时施加显式不确定性惩罚，不再伪造中等风险。

## 2. AuraDB 实际迁移结果

| 指标 | 迁移前 | 迁移后 |
|---|---:|---:|
| 节点 | 15,236 | 14,624 |
| 关系 | 23,772 | 22,497 |
| `RiskFactor` | 612 | 0 |
| 无 Provider `RiskFactor` | 612 | 0 |
| 含旧默认风险字段的节点 | 620 | 0 |
| `RouteSegment` | 613 | 613 |

本次实际删除 612 个 `RiskFactor` 和它们附属的 1,275 条关系；没有删除路线、路段、供应商、地点或实时 API 数据。另有 613 个路段完成重算，129 个其他节点完成旧风险字段清理。

实时数据保护基线在事务前后完全一致：

| 标签 | 迁移前 | 迁移后 |
|---|---:|---:|
| `NewsRiskEvent` | 11,946 | 11,946 |
| `NewsRiskZone` | 10 | 10 |
| `WeatherRiskSnapshot` | 3 | 3 |
| `Vessel` | 1 | 1 |
| `VesselObservation` | 1 | 1 |
| `Shipment` | 1 | 1 |

迁移后立即重复 dry-run，删除、重算和清空数量均为 0，证明脚本幂等。03:18 UTC 后，原 GDELT 路段信号达到 3 小时 TTL；03:21 UTC 增量重算将最后 5 条 `partial` 海运路段安全改为 `unavailable`。最终再次 dry-run 的三类计划数量仍全部为 0。

因此本文迁移表记录的是清理事务前后结果；**当前最终状态**是 613 个路段全部 `unavailable`，等待下一次 GDELT 或 Open-Meteo 定时更新写入新鲜信号。这是有效期控制的预期行为，不是数据丢失。

## 3. 安全删除规则

`scripts/recalculate_provider_risk.py` 只允许删除 `RiskFactor`，并且必须同时满足：

- 没有 `provider`；
- 没有 `source_url`；
- 没有连接 `Evidence`；
- 未处于 `approved`、`verified` 或 `reviewed` 状态；
- `source` 明确包含 `derived`、`standardization`、`synthetic`、`sample`、`mock` 等标记；
- 没有直接连接受保护的实时观测或证据节点。

执行前先写 JSON 备份。写事务结束前再次统计 GDELT、Open-Meteo、AIS 和 Shipment 标签；任何受保护标签数量下降都会触发事务回滚。

## 4. 分运输方式的风险维度

配置来自 `config/vehicle_strategy.yaml`。没有真实 Provider 的维度保留为缺失，不参与分数。

| 方式 | 风险维度 | 当前可接入 Provider |
|---|---|---|
| 海运 | 天气、海盗、港口拥堵、地缘政治、制裁、班期 | `Open-Meteo` 天气；`GDELT` 地缘政治 |
| 铁路 | 边境海关、地缘政治、基础设施、天气、班期、制裁 | `Open-Meteo` 天气；`GDELT` 地缘政治 |
| 公路 | 交通、边境海关、陆路治安、天气、监管、班期 | `Open-Meteo` 天气 |
| 空运 | 天气、空域冲突、机场容量、班期、制裁、装卸 | `Open-Meteo` 天气；`GDELT` 空域冲突 |

GDELT 新闻不会被直接映射成公路交通、海盗、港口拥堵或铁路基础设施风险。只有维度语义与 Provider 数据相符时才允许加入计算。

## 5. 计算规则

只对可用维度重新归一化：

```text
风险分 = Σ(有效维度分数 × 配置权重) / Σ(有效维度权重)
数据完整度 = Σ(有效维度权重) / Σ(该运输方式全部维度权重)
```

例如海运只有 GDELT 地缘政治数据时：

- 返回的风险分等于该 GDELT 信号分；
- `provider_risk_data_completeness=0.18`；
- 海盗、天气、拥堵、制裁和班期列入 `provider_risk_missing_factors`；
- 状态为 `partial`，不会因为重新归一化而被误解为完整评估。

完全没有可用 Provider 时：

```json
{
  "riskScore": null,
  "riskStatus": "unavailable",
  "riskDataCompleteness": 0.0
}
```

## 6. RouteSegment 新风险字段

| 字段 | 含义 |
|---|---|
| `provider_risk_score` | 0-1 风险分；不可用时不存在/返回 null |
| `provider_risk_score_100` | 0-100 风险分 |
| `provider_risk_level` | `low/medium/high/critical/unknown` |
| `provider_risk_status` | `available/partial/unavailable` |
| `provider_risk_data_completeness` | 0-1 数据完整度 |
| `provider_risk_confidence` | Provider 给出置信度时的加权置信度 |
| `provider_risk_missing_factors` | 缺少真实 Provider 的维度 |
| `provider_risk_providers` | 本次实际参与计算的 Provider |
| `provider_risk_evidence` | GDELT 区域、天气快照等证据 ID |
| `provider_risk_factors_json` | 每个维度的分数、权重、Provider、时效和证据 |
| `risk_input_hash` | 输入指纹，用于幂等重算 |
| `risk_scoring_version` | 当前为 `provider-risk-v1` |

旧接口兼容字段 `total_risk_score` 和 `dynamic_risk_score` 仅同步真实 Provider 结果；不可用时为 null。旧的 `riskScore`、`base_risk_score`、`costRiskScore` 和 `supplier_risk` 已从相应节点清除。

## 7. 前端接口变化

下列接口已经改为读取 `provider_risk_*`：

- `GET /api/risk/segments`
- `GET /api/suppliers`
- `GET /api/routes/recommend`
- `GET /api/routes/optimize`
- `GET /api/routes/recommendations`

路径响应新增或明确返回：

```text
riskScore
riskStatus
riskDataCompleteness
riskKnownLegs
riskMissingFactors
riskProviders
riskFactors[].provider
riskFactors[].evidence
legs[].riskStatus
legs[].riskDataCompleteness
```

排序时，未知风险使用显式最差不确定性惩罚；返回值仍为 null，不会把惩罚值伪装成真实风险分。

## 8. 定时更新如何持续重算

- GDELT 写入路段新闻覆盖后，使用 GDELT 地缘政治/空域冲突信号重新计算 Provider 风险。
- 路段不经过已配置 GDELT 区域时，不把“无暴露”伪造为通用零风险。
- Open-Meteo 写入港口天气后，记录 `weather_risk_provider`、天气快照 Evidence，并重算受影响海运路段。
- 超过有效期的 GDELT 或天气数据不会继续参与计算。

## 9. 使用命令

只读预览：

```bash
python scripts/recalculate_provider_risk.py --dry-run
```

实际执行必须提供确认口令：

```bash
python scripts/recalculate_provider_risk.py \
  --execute \
  --confirm CLEAN_AND_RECALCULATE_PROVIDER_RISK_V1
```

指定天气有效期和删除上限：

```bash
python scripts/recalculate_provider_risk.py \
  --dry-run \
  --weather-max-age-hours 6 \
  --max-delete 1000
```

## 10. Neo4j Browser 验证

查看各运输方式覆盖状态：

```cypher
MATCH (segment:RouteSegment)
RETURN coalesce(segment.canonical_mode,segment.mode,'unknown') AS mode,
       segment.provider_risk_status AS status,
       count(*) AS segments,
       avg(segment.provider_risk_data_completeness) AS averageCompleteness
ORDER BY mode,status;
```

查看真实 Provider 风险：

```cypher
MATCH (segment:RouteSegment)
WHERE segment.provider_risk_score IS NOT NULL
RETURN segment.segment_id,
       segment.canonical_mode,
       segment.provider_risk_score_100,
       segment.provider_risk_data_completeness,
       segment.provider_risk_providers,
       segment.provider_risk_missing_factors
ORDER BY segment.provider_risk_score DESC;
```

确认旧默认风险已清除：

```cypher
MATCH (factor:RiskFactor) RETURN count(factor) AS riskFactors;

MATCH (segment:RouteSegment)
WHERE segment.riskScore IS NOT NULL
   OR segment.base_risk_score IS NOT NULL
   OR segment.costRiskScore IS NOT NULL
RETURN count(segment) AS legacyRiskSegments;
```

## 11. 后续仍需补充的 Provider

| 缺口 | 推荐数据类型 |
|---|---|
| 海盗与海上安全 | IMB/官方海事安全事件或可合法使用的权威事件源 |
| 港口拥堵 | 港务局、码头运营商、AIS 聚合或可信物流可视化 Provider |
| 班期可靠性 | 船公司、航空公司、铁路运营方或商业班期 API |
| 制裁与禁运 | OFAC、EU、UN 等官方清单及可审计匹配流程 |
| 边境海关 | 官方口岸等待时间、海关公告或可信商业 Provider |
| 铁路基础设施 | 运营商中断公告、基础设施状态和真实运行记录 |
| 公路交通与治安 | 官方交通、事故和安全事件 Provider |
| 机场容量与装卸 | 机场/货站运行状态和航空货运 Provider |

在这些 Provider 接入前，相应维度会保持 unavailable，不会以经验常数填充。
