# 数据来源、真实性与使用边界

> 核对日期：2026-07-26  
> 适用范围：当前 `supply-chain-api`、Neo4j AuraDB、GitHub Actions 和 AIS worker

本文回答三个最重要的问题：

1. 哪些值来自真实外部数据源；
2. 哪些值是项目算法计算或估算的；
3. 当前缺少哪些 Provider，前端应该怎样展示。

任何带 `estimated`、`partial`、`unavailable` 的结果都不应被前端包装成“已向承运商确认”。本项目提供的是供应链方案比较和风险辅助决策，不是航海、飞行、铁路调度或报关指令。

## 1. 先理解四种数据状态

| 状态 | 含义 | 前端建议 |
|---|---|---|
| `observed` / `available` | 存在可追溯 Provider 数据，且仍在有效期内 | 正常展示 Provider、观测时间和有效期 |
| `reference` | 来自注册表或开放地理数据，不是实时运营信息 | 可用于名称、代码和地图端点 |
| `estimated` / `partial` | 使用参考数据、几何或公式推算，部分输入缺失 | 显示“估算”或“数据不完整” |
| `unavailable` | 没有合格 Provider、数据已过期或无法验证 | 显示“暂无数据”，不要补 `0` 或 `50` |

还要区分两个概念：

- `RouteSegment.provider`：真实运输服务提供方，例如船公司、铁路运营商或航空公司。当前数据库中 `0/613` 条路段具备该字段。
- `RouteSegment.provider_risk_providers`：参与风险计算的数据 Provider，例如 `GDELT`、`Open-Meteo`、`AISStream.io`。它不代表承运商。

## 2. 当前已接入的实时或准实时 Provider

### 2.1 GDELT

| 项目 | 当前实现 |
|---|---|
| 用途 | 查询风险区相关的近期新闻，并映射到经过该区域的路线 |
| 外部接口 | GDELT DOC 2.0 API |
| 鉴权 | 当前接口不需要 API Key |
| 更新 | GitHub Actions 每小时第 7 分钟运行 |
| 路线有效期 | 默认 3 小时，`GDELT_RISK_TTL_HOURS=3` |
| 原始真实性 | 文章标题、URL、域名和 GDELT 返回时间属于外部观测元数据 |
| 项目计算 | 分类、近重复聚类、严重度、区域风险和路线暴露分数 |
| 当前限制 | GDELT 是新闻检索源，不是事故确认机构；域名数量只表示来源多样性，不表示媒体可信度 |

官方资料：

- <https://www.gdeltproject.org/data.html>
- <https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/>

数据库链路：

```text
NewsRiskEvent
  -> NewsRiskCluster
  -> NewsRiskZone / GeoZone
  -> RouteSegment
  -> provider_risk
```

查询接口：

```text
GET /api/risk/news
GET /api/risk/news/zones
GET /api/risk/news/clusters
```

### 2.2 Open-Meteo

| 项目 | 当前实现 |
|---|---|
| 用途 | 港口天气、海况和路线沿线天气风险 |
| 外部接口 | Forecast API、Marine API、Geocoding API |
| 鉴权 | 免费非商业接口无需 Key；商业部署应使用符合当前条款的方案 |
| 更新 | GitHub Actions 每小时第 23 分钟运行 |
| 路线有效期 | 默认 2 小时，`WEATHER_ROUTE_RISK_TTL_HOURS=2` |
| 原始真实性 | 数值天气预报模型的 API 输出 |
| 项目计算 | 各运输方式天气维度、路线采样、风险分、完整度和置信度 |
| 当前限制 | 这是预报而不是港口实测站数据；海况网格和估算路线不能用于航海 |

Open-Meteo 当前条款说明 API 数据按 CC BY 4.0 提供；免费 API 面向非商业用途并有调用额度，商业上线前必须重新核对条款：

- <https://open-meteo.com/en/terms>
- <https://open-meteo.com/en/docs>
- <https://open-meteo.com/en/docs/marine-weather-api>

查询接口：

```text
GET /api/ports/weather-risks
GET /api/ports/{port_id}/weather
GET /api/ports/{port_id}/weather/history
GET /api/routes/weather-risks
GET /api/routes/weather-risks/{segment_id}
```

### 2.3 AISStream.io

| 项目 | 当前实现 |
|---|---|
| 用途 | 上海、新加坡、鹿特丹和苏伊士附近船舶流量与港口拥堵信号 |
| 外部接口 | `wss://stream.aisstream.io/v0/stream` WebSocket |
| 鉴权 | 必须配置后端 `AISSTREAM_API_KEY` |
| 更新 | 常驻 `scripts/run_ais_consumer.py` worker 持续消费；不是每小时 HTTP 任务 |
| 快照有效期 | 默认 90 分钟，`AIS_SNAPSHOT_TTL_MINUTES=90` |
| 原始真实性 | 收到的 AIS 位置和静态消息字段 |
| 项目计算 | 去重、每船最新状态、60 分钟港区聚合、拥堵风险和置信度 |
| 当前限制 | AISStream.io 官方标记为 Beta、无 SLA；覆盖受岸站、船载设备、消息类型和网络连接影响 |

AISStream.io 官方要求在后端使用 API Key，并明确不支持浏览器直接连接，以免暴露密钥：

- <https://aisstream.io/documentation>

可能得到的字段包括 MMSI、IMO、船名、船型、经纬度、速度、航向、目的地、吃水和导航状态，但这些字段来自不同 AIS 消息，某次消息不一定全部具备。

查询接口：

```text
GET /health/ais
GET /api/providers/status
GET /api/ais/targets
GET /api/ais/targets/{target_id}/traffic
GET /api/ports/{port_id}/traffic
GET /api/vessels/{mmsi}
```

当前 AuraDB 尚无可用 `PortTrafficSnapshot`，所以 AIS 拥堵风险会正确返回 `unavailable`。只有常驻 worker 收到真实消息并完成聚合后才会参与推荐。

## 3. 当前采用的静态参考数据

| 数据源 | 用途 | 许可证或条款摘要 | 当前采用方式 |
|---|---|---|---|
| [UNECE UN/LOCODE](https://unlocode.unece.org/publications/) | 港口和运输地点代码、名称、参考坐标 | 官方目录免费发布；镜像数据的再分发许可仍需在发布前复核 | 规范港口代码和参考坐标 |
| [OurAirports](https://ourairports.com/data/) | IATA 机场坐标 | Public Domain，无准确性保证 | 机场精确代码匹配 |
| [GeoNames](https://www.geonames.org/export/) | 城市中心点回退 | CC BY，允许商业使用但必须署名；免费服务有额度 | 仅低置信度回退 |
| [Natural Earth](https://www.naturalearthdata.com/about/terms-of-use/) | 海域、国家陆地掩膜 | Public Domain | 小比例尺风险区和陆地检查 |
| [Marine Regions](https://www.marineregions.org/) | 部分海峡与运河公开范围 | 当前项目按 CC BY 使用；不可当作法定边界或航海资料 | 风险区边界参考 |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) | 公路网络底图 | ODbL 1.0，需署名并遵守衍生数据库义务 | 通过 OSRM 生成估算公路几何 |
| [OSRM](https://github.com/Project-OSRM/osrm-backend) | 公路路径计算引擎 | BSD-2-Clause；公共 Demo 无生产 SLA | 初始化时可选调用 |
| [searoute-py](https://github.com/genthalili/searoute-py) | 海运网络估算 | Apache-2.0；网络并非实际船期或船舶轨迹 | 地图展示与区域相交估算 |

静态参考数据不会自动每小时更新。要升级版本，应重新下载合法来源文件，再运行：

```bash
python scripts/build_geospatial_reference.py \
  --unlocode-csv /path/to/unlocode.csv \
  --ourairports-csv /path/to/airports.csv \
  --geonames-zip /path/to/cities15000.zip \
  --natural-earth-marine-zip /path/to/marine.zip \
  --natural-earth-countries-zip /path/to/countries.zip

python scripts/migrate_geospatial_data.py --dry-run
```

## 4. 哪些结果属于项目估算

### 4.1 路线几何

- 海运：`searoute-py` 开放海运网络的最短路径估算；不是船公司航线或 AIS 历史轨迹。
- 公路：OSRM + OpenStreetMap 的道路路径；不是承运商订单路线。
- 空运：端点 WGS84 大圆线；不是航路、航班或空域许可。
- 铁路：当前没有可信铁路网络 Provider；没有几何时只保留端点推断。
- `geometry_is_navigational=false`：任何路线几何都不能用于实际导航。

### 4.2 费用

当前多数费用来自：

```text
距离 × 配置费率 × 货物数量或体积/重量修正 + 已配置附加项
```

因此它是比较候选方案的预算估算，不是承运商报价。响应应同时查看：

```text
costEstimate.dataStatus
costEstimate.provider
costEstimate.confidence
costEstimate.assumptions
costEstimate.missingComponents
estimatedFields
```

### 4.3 时效

当前多数时效来自距离、方式速度、等待/换装/清关假设与已有延误观测。`durationP50Days` 和 `durationP90Days` 是模型估算，不是承诺到达时间。

### 4.4 综合风险和推荐分

- `riskScore`：只聚合仍在有效期内且有 Provider/Evidence 的风险维度。
- `riskDataCompleteness`：真实可用风险维度所占权重比例。
- `finalScore`：风险、费用、时效归一化结果再加不确定性惩罚，数值越低越优。
- 缺失 Provider 时返回 `null/unavailable`，排序内部可以惩罚未知值，但不会伪造风险分。

## 5. 尚未接入、不能假装真实的数据

| 缺口 | 当前行为 | 推荐后续数据 |
|---|---|---|
| 船公司/航空/铁路实际报价 | 使用公式估算，Provider 为空 | 船公司、货代、航空货运或铁路运营方报价 API |
| 班期与舱位 | `unavailable` | 船期、航班、铁路班列和舱位 API |
| 码头费、港杂费、燃油附加费 | 缺失组件列入 `missingComponents` | 港口、码头、承运商费率表或商业报价源 |
| 关税、制裁和报关规则 | 不用经验常数补分 | OFAC、EU、UN、海关和贸易主管部门官方数据 |
| 海盗与海事安全 | `unavailable` | IMB、UKMTO 或可合法使用的权威安全事件源 |
| 边境等待和铁路中断 | `unavailable` | 口岸、海关、铁路运营商和基础设施公告 |
| 公路实时交通和事故 | `unavailable` | 官方交通、事故或商业路况 Provider |
| 机场容量和货站装卸 | `unavailable` | 机场、货站、航空货运状态 Provider |
| 真实供应商出货设施链 | 部分供应商只能使用旧入口映射 | 供应商主数据、工厂/仓库地址和审核过的接驳段 |

`.env.example` 中的 `MARINETRAFFIC_API_KEY`、`OPENSKY_*`、`FLIGHTAWARE_*` 等只是未来 Provider 适配预留。配置变量存在不等于代码已生产接入，也不等于数据库中已有真实数据。

## 6. 前端如何判断能否可信展示

推荐卡片至少读取：

```text
riskStatus
riskDataCompleteness
riskProviders
riskFactors[].status
riskFactors[].provider
riskFactors[].observedAt
riskFactors[].expiresAt
costEstimate.dataStatus
costEstimate.provider
durationEstimate.dataStatus
estimatedFields
missingData
```

建议规则：

1. `status=unavailable`：显示“暂无可信数据”，不要画绿色低风险进度条。
2. `status=partial`：显示“数据不完整”，并列出缺失维度。
3. `expiresAt` 已过期：按不可用处理。
4. `provider=null`：显示“模型估算”，不要显示承运商 Logo。
5. 坐标 `coordinateStatus=estimated`：可以画地图，但要说明是城市中心点估算。
6. `geometryIsNavigational=false`：地图仅作方案示意。

## 7. 上线前的数据合规检查

1. 重新打开每个 Provider 的官方条款并记录检查日期。
2. 确认商业用途、调用额度、署名、缓存和再分发要求。
3. 在前端或法律页面展示 Open-Meteo、GeoNames、OpenStreetMap 等必要署名。
4. 不缓存或公开第三方条款禁止长期保存的原始数据。
5. 不把 API Key、Aura 密码或管理员 Token 放进前端变量。
6. 对承运商报价、船期和供应商主数据建立单独的数据合同与审计记录。

本文是工程说明，不构成法律意见；许可证和服务条款可能变化，商业发布前应由项目负责人再次核对。
