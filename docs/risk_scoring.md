# 路径推荐评分说明

> 当前版本：`route-recommendation-v1.3-three-factor`

## 0. 当前路线风险只使用三个因子

| 因子 | 权重 | Provider | 输入 |
|---|---:|---|---|
| 战争与武装冲突 `war` | 0.40 | GDELT | `conflict` 事件簇 |
| 自然灾害与极端天气 `natural_disaster` | 0.35 | GDELT、Open-Meteo | `natural_disaster` 事件簇和沿路线天气采样 |
| 关税与政策调整 `trade_policy` | 0.25 | GDELT | `sanction`、`trade_policy`、`tariff` 事件簇 |

单路段风险使用可用因子的加权平均：

```text
segmentRisk = Σ(availableFactorScore × factorWeight)
            / Σ(availableFactorWeight)
```

缺失因子保持 `null`，不会填入默认分；`dataCompleteness` 等于可用因子权重之和。路线风险是已知路段风险的算术平均。海盗、拥堵、班期、基础设施、供应商等数据当前不参与路线 `riskScore`。

## 1. 三类输入

推荐分只使用三类目标：

1. 风险：`riskScore`，范围 `0-100`，越低越好；
2. 成本：本次请求总成本除以货物数量，单位 `USD/车`，越低越好；
3. 时效：`durationP50Days`，越低越好。

风险必须来自仍在有效期内且带 Provider 的观测。缺失风险保持 `null`，不会转换为 50 分。

## 2. 固定锚点归一化

对每个值使用固定上下界：

```text
utility(value) = 100 × (worst - clip(value, best, worst)) / (worst - best)
```

当前边界：

| 目标 | best | worst |
|---|---:|---:|
| 风险 | 0 | 100 |
| 每车成本 USD | 0 | 50000 |
| P50 时效（天） | 0 | 120 |

这些值写在 `config/recommendation_scoring.yaml`，修改时必须同步提升 `scoring_version`。固定锚点避免候选集合变化导致同一路线分数漂移。

## 3. 缺失数据处理

某个目标缺失时：

- 子分返回 `null`；
- 该项加权贡献为 `0`；
- 该项置信度为 `0`；
- 不确定性惩罚单独增加；
- 返回 `missingData` 和解释文字。

这相当于“缺失数据不能证明路线优秀”，但不会把未知风险伪装成观测到的最高风险或中性风险。

## 4. 不确定性惩罚

```text
baseScore = riskWeight × riskUtility
          + costWeight × costUtility
          + durationWeight × durationUtility

dataCompleteness = Σ(weight × dimensionConfidence)

uncertaintyPenalty = 100 × penaltyWeight
                   × Σ(weight × (1 - dimensionConfidence))

finalScore = clip(baseScore - uncertaintyPenalty, 0, 100)
```

当前 `penaltyWeight=0.20`。风险置信度使用风险数据完整度；成本和时效使用其估算或 Provider 观测置信度。

## 5. 风险与硬约束

- `maxRiskScore` 在风险未知时判定为无法验证，候选被拒绝；
- `requireKnownRisk=true` 会拒绝所有 `riskScore=null` 的路线；
- 没有风险上限时，未知风险路线仍可返回，但会因风险贡献为 0 和不确定性扣分而降低排名；
- 当前仅允许战争、自然灾害、关税/政策三个因子加入 `riskScore`；其他数据即使存在，也只作为独立观察信息。

## 6. 稳定排序

先应用硬约束，再按以下字段稳定排序：

1. `finalScore` 降序；
2. 已知风险优先；
3. 风险升序；
4. 成本升序；
5. 时效升序；
6. 稳定 `routeId` 字符串升序。

因此输入、图数据和评分版本相同时，排序结果可重复。

## 7. 修改配置时的规则

修改以下任一内容后必须更新 `scoring_version` 并补测试：

- 固定归一化边界；
- 默认策略权重；
- 不确定性惩罚比例；
- 成本费率、容量或电动车附加假设；
- P90 倍数；
- 候选生成上限。

不要直接根据一次请求的候选最大成本或最长时效做归一化，否则不同请求之间的分数无法比较。

## 8. 前端如何解释分数

| 字段 | 越大还是越小越好 | 是否一定真实 |
|---|---|---|
| `riskScore` | 越小越好 | 只有 `riskStatus=available/partial` 且未过期时可用 |
| `cost` | 越小越好 | 多数为估算，需检查 `costEstimate.dataStatus/provider` |
| `durationDays` | 越小越好 | 多数为估算，需检查 `durationEstimate.dataStatus` |
| `finalScore` | 越大越好 | 是版本化推荐效用分，不是现实世界直接观测值 |
| `dataCompleteness` | 越大越完整 | 表示输入覆盖，不等于准确率 |
| `uncertaintyPenalty` | 越大表示不确定性越高 | 由缺失输入和置信度计算 |

页面应直接使用后端 `rank`，并同时展示 `finalScore` 与数据完整度。不能只显示一个综合分而隐藏大量缺失数据。

## 9. Provider 时效规则

- GDELT 路线新闻风险默认 3 小时有效；
- Open-Meteo 路线天气默认 2 小时有效；
- AIS 港口流量快照默认 90 分钟有效；
- API 查询时会再次检查 `expiresAt`；
- 已过期数据转为 `unavailable/stale`，不会继续进入风险分；
- AIS 拥堵、海盗、边境、基础设施、班期等维度不会加入当前三因子模型。

前端可通过 `GET /api/providers/status` 展示最后更新时间，并根据 `riskFactors[].observedAt/expiresAt` 标记单个风险因子的时效。

## 10. 修改算法后的版本管理

任何会改变同一输入排序的修改，都应：

1. 更新 `config/recommendation_scoring.yaml` 中的 `scoring_version`；
2. 增加或修改自动测试；
3. 在推荐快照中保留输入、权重、约束和版本；
4. 更新 `docs/route_recommendation.md`；
5. 不回写或伪装旧快照的历史分数。

数据 Provider 与估算边界详见 `docs/data_sources.md`。
