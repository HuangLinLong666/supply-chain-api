# 阶段 5：GDELT 去重与路线天气进入推荐

## 1. 已解决的问题

阶段 5 不再把每篇转载新闻都当成一个独立风险，也不再只用港口端点天气冒充整条路线天气。现在的主链路是：

```text
GDELT 原始文章 -> 时间/URL 校验 -> 事件分类 -> 近重复聚类 -> 区域风险 TTL
Open-Meteo 预报 -> 路线几何/端点采样 -> 分运输方式评分 -> 路线天气 TTL
GDELT + Open-Meteo -> provider-risk-v1 -> 路径风险排序与推荐
```

所有缺失数据继续返回 `null/unavailable`，不会生成中性默认分。

## 2. GDELT 算法

### 2.1 原始数据保留

`NewsRiskEvent` 是 GDELT 原始文章证据。历史迁移没有删除任何原始事件；只增加规范 URL、UTC 时间状态、分类字段和事件簇关系。

### 2.2 去重与聚类

1. 删除 `utm_*` 等跟踪参数，得到 `canonical_url`。
2. 相同规范 URL 只进入一次评分。
3. 在 48 小时窗口内，对相同类别标题计算 token Jaccard 相似度。
4. 相似度达到 `0.82` 的文章进入同一个 `NewsRiskCluster`。
5. 一个事件簇无论有多少转载，只计算一次严重度。

Jaccard/集合相似度的设计依据可参考 Broder 的近重复文档研究；当前实现是透明、可测试的工程化简化版本，不声称等同于论文完整 MinHash 系统。

### 2.3 分类和时间

当前类别包括冲突、制裁、贸易政策、关税、劳工罢工、港口中断、运输事故、自然灾害和海关延误。无法分类的文章保留为 `other`，但不制造风险分。

- 无时间或无效时间：拒绝进入评分。
- 超过当前时间 5 分钟：按未来时间拒绝。
- 新鲜度半衰期：24 小时。
- 路段新闻 TTL：3 小时。
- 没有可分类事件簇：区域状态 `unavailable`，分数为 `null`。

域名数量只用于“来源多样性”置信度。没有独立媒体评级 Provider，所以 `source_credibility_status=unavailable`。

## 3. Open-Meteo 路线采样

### 3.1 采样优先级

1. 已验证 LineString：默认均匀采样 5 点。
2. `estimated_by_graph` 几何：仍采样 5 点，但方法为 `estimated_geometry_linestring`，置信度上限为 `0.55`。
3. 无几何：只采起终点，方法为 `endpoint_fallback`，置信度上限为 `0.35`。
4. 缺坐标：安全跳过，等待阶段 6 补地理数据。

ETA 超出 168 小时预报窗口时，该采样点变为不可用；系统绝不拿最后一个预报小时冒充未来数周的天气。

### 3.2 分运输方式维度

| 方式 | 主要天气维度 |
|---|---|
| 海运 | 浪高、阵风、持续风、能见度、降水、天气现象 |
| 空运 | 阵风、能见度、持续风、降水、天气现象 |
| 公路 | 降水、积雪、能见度、极端温度、天气现象、风 |
| 铁路 | 积雪、极端温度、风、阵风、降水、能见度、天气现象 |

路线风险采用：

```text
路线天气风险 = 60% × 有效采样点最大风险 + 40% × 有效采样点平均风险
```

采样覆盖率、指标完整度、ETA 是否可用和几何来源共同决定 `confidence` 与 `data_completeness`。

## 4. 数据库结构

```text
(NewsRiskEvent)-[:MEMBER_OF_EVENT_CLUSTER]->(NewsRiskCluster)
(NewsRiskCluster)-[:AFFECTS_ZONE]->(NewsRiskZone:GeoZone)
(RouteSegment)-[:EXPOSED_TO_NEWS_CLUSTER]->(NewsRiskCluster)

(RouteSegment)-[:HAS_ROUTE_WEATHER_SNAPSHOT]->(RouteWeatherRiskSnapshot:RiskObservation)
(RouteSegment)-[:HAS_RISK_OBSERVATION]->(RouteWeatherRiskSnapshot)
```

推荐读取 `RouteSegment.provider_risk_*`。查询时再次检查 `provider_risk_expires_at`；即使定时任务延迟，过期值也不会继续参与排序。

## 5. 当前 AuraDB 实测

| 项目 | 数量 |
|---|---:|
| 保留的 GDELT 原始事件 | 11,946 |
| 已分类原始事件 | 11,946 |
| 新闻事件簇 | 8,975 |
| 事件到事件簇关系 | 15,062 |
| 路线天气快照 | 9 |
| 当前有 Open-Meteo Provider 风险的路线 | 9 |

路线天气扫描 120 条可识别运输段，跳过 101 条合成路线、8 条失效估算路线和 2 条地理不可行路线。剩余 9 条均为估算几何，所以状态是 `partial`，没有伪装成运营商验证轨迹。

迁移重复执行结果：事件更新 `0`、事件簇新增 `0`、成员关系新增 `0`，证明核心回填幂等。

## 6. 本地命令

只看计划，不写数据库：

```bash
python scripts/migrate_gdelt_events.py
python scripts/update_route_weather.py --dry-run
```

执行 GDELT 历史回填：

```bash
python scripts/migrate_gdelt_events.py \
  --execute \
  --confirm MIGRATE_GDELT_EVENTS_V3
```

执行路线天气：

```bash
python scripts/update_route_weather.py
python scripts/update_route_weather.py --segment-id leg_cn_sha_de_ham_sea_1
```

单独排查一个 GDELT 区域：

```bash
python scripts/update_gdelt_risk.py --dry-run --zone-id port-shanghai
```

## 7. 前端 API

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/risk/news` | 原始新闻、类别和所属事件簇 |
| GET | `/api/risk/news/zones` | 区域聚合、TTL、文章/事件簇数量 |
| GET | `/api/risk/news/clusters` | 去重后的新闻事件簇 |
| GET | `/api/routes/weather-risks` | 路线天气风险列表 |
| GET | `/api/routes/weather-risks/{segment_id}` | 路线采样点、预报时刻和因子明细 |
| GET | `/api/routes/recommend` | 使用新鲜 Provider 风险排序候选路径 |

## 8. GitHub 每小时配置

### 第一步：提交两个工作流

确认仓库中存在：

```text
.github/workflows/update-gdelt-risk.yml
.github/workflows/update-weather-risk.yml
```

### 第二步：配置四个 Secrets

GitHub 仓库打开 `Settings -> Secrets and variables -> Actions -> New repository secret`，逐个添加：

```text
AURA_NEO4J_URI
AURA_NEO4J_USERNAME
AURA_NEO4J_PASSWORD
AURA_NEO4J_DATABASE
```

`AURA_NEO4J_DATABASE` 必须使用已经通过 `scripts/verify_aura_connection.py` 验证的数据库名。

### 第三步：手动验证

打开 `Actions`：

1. 选择 `Update GDELT route risk`，点击 `Run workflow`。
2. 确认每个 `gdelt_zone_fetch_complete` 后没有 `failures`。
3. 选择 `Update Open-Meteo route weather risk`，点击 `Run workflow`。
4. 确认 `segmentsWritten` 大于 0 且 `errors` 为空。

### 第四步：验证 Render

工作流直接写 AuraDB，不依赖本地电脑和 Render 进程是否正在执行采集。Render 被访问唤醒后会读取同一个 AuraDB：

```text
https://你的Render域名/api/risk/news/clusters?active_only=true
https://你的Render域名/api/routes/weather-risks?active_only=true
```

## 9. 外部接口依据与限制

- GDELT DOC 2.0 提供 ArticleList JSON、时间窗口和记录数参数；使用时应保留 GDELT 归属链接。
- Open-Meteo Forecast 支持多坐标和逐小时变量；Marine API 提供逐小时浪高等海况，但沿岸网格不能用于航海导航。
- Open-Meteo 免费接口受其非商业使用和调用额度条款约束；商业部署前需要核对当前许可和订阅方案。

参考：

- <https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/>
- <https://www.gdeltproject.org/about.html>
- <https://open-meteo.com/en/docs>
- <https://open-meteo.com/en/docs/marine-weather-api>
- <https://open-meteo.com/en/terms>
- <https://open-meteo.com/en/license>
- <https://www.cs.princeton.edu/courses/archive/spr05/cos598E/bib/broder97resemblance.pdf>

## 10. 当前已知限制

- 本机对 GDELT 的 TLS 握手超时，属于当前代理/出口问题；Open-Meteo 与 AuraDB 已真实验证成功。推送后应在 GitHub Actions 再验证 GDELT 在线刷新。
- 当前路线几何是图估算结果，阶段 6 仍需补充坐标来源、真实 geometry 和 `PASSES_THROUGH` 空间关系。
- 新闻来源可信度没有独立 Provider，因此只报告来源多样性，不报告媒体可信度分。
