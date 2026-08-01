# 会议反馈四项更新落实情况

## 1. 计算模型透明化

状态：后端已完成，前端需要展示。

- 新增 `GET /api/methodology?strategy=...`，只返回用户当前所选风险、成本或综合策略对应的公式。
- `POST /api/routes/recommend` 继续返回 `scoreBreakdown`、`costEstimate`、`durationEstimate`、`whyRecommended` 和数据完整度。
- 当前采用可审计的版本化加权决策模型，不宣称已经通过行业历史样本完成权威统计校准。
- 后续如取得真实运价、延误和事故样本，可增加回测、参数拟合与误差指标，但不影响当前公式透明化要求。

前端配合：增加“计算方法”弹窗，根据当前选择调用 `min_risk`、`min_cost` 或 `balanced`；无需展示模型版本和归一化配置。

## 2. 地点代码格式统一

状态：后端已完成，前端需要遵守。

- 地点采用 `location-id-v2`：港口 `PORT-{UNLOCODE}`、机场 `AIR-{IATA}`、铁路 `RAIL-{COUNTRY}-{CODE}`、公路节点 `ROAD-{COUNTRY}-{CODE}`。
- 国家主键采用 ISO 3166-1 alpha-2 `countryCode`，显示名称使用 CLDR 字段。
- 旧 ID 只用于后端兼容，不应进入新前端缓存和 URL。

前端配合：下拉框显示 `name`，提交和缓存只使用接口返回的 `locationId`，不要自行根据中文或英文地名拼接代码。

## 3. 战争触发跨运输方式改道

状态：后端已完成核心逻辑，实际可切换范围取决于路线图中的可行替代边。

- `autoReroute=true` 只是启用开关，不会无条件避开海运。
- 只有原首选路线的三因子综合 `riskScore >= 60/100` 才触发；战争、自然灾害、关税/政策均可触发。
- 触发后移除高风险路段，跨 `sea/rail/road/air` 搜索替代路线，并以替代路线 `riskScore` 最低优先。
- 没有安全可行替代路线时返回原路线，`fallbackUsed=true`。
- `dynamicRouting.rerouted`、`avoidedZones`、`fallbackUsed` 和 `routes[].whyRecommended` 提供前端解释。

前端配合：明显展示运输方式变化、避开的风险区和回退状态；地图必须根据返回的新 `legs[]` 重画，不能沿用原海运折线。

## 4. 风险因子收敛为三类

状态：后端已完成，前端需要替换旧因子键。

当前路线 `riskScore` 仅使用：

1. `war`：战争与武装冲突，权重 0.40，Provider 为 GDELT；
2. `natural_disaster`：自然灾害与极端天气，权重 0.35，Provider 为 GDELT/Open-Meteo；
3. `trade_policy`：关税与政策调整，权重 0.25，Provider 为 GDELT。

海盗、AIS 拥堵、班期、基础设施等数据可以继续独立观察，但不进入当前路线风险分。缺失因子保持 `null`，只降低 `dataCompleteness`，不填充默认 50 分。

前端配合：风险卡片只绑定以上三个 `key`；旧 `weather`、`geopolitical`、`airspace_conflict`、`port_congestion` 等键不得继续作为路线综合风险因子展示。
