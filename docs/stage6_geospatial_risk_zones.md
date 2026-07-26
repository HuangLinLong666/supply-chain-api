# 阶段 6：坐标、路线几何与风险区映射

> 执行日期：2026-07-26（Asia/Shanghai）  
> 数据库：项目 `.env` 当前连接的 Neo4j AuraDB  
> 集成版本：`geospatial-routing-v1` / `geospatial-exposure-v1`

## 1. 本阶段解决什么问题

阶段 5 的 GDELT 风险区主要根据起终点国家和名称推断路线是否经过红海、马六甲海峡、印度洋等区域。这个方法可以作为应急回退，但不能证明路线真的经过某个区域。

阶段 6 增加五项能力：

1. 为 `TransportLocation` 保存坐标来源、许可证、采集时间、状态和置信度。
2. 为可验证的海运、公路路线建立 GeoJSON `LineString`。
3. 为 10 个 GDELT 风险区建立 GeoJSON `Polygon` / `MultiPolygon`。
4. 用 Shapely 计算路线与风险区的真实几何交叉，写入 `PASSES_THROUGH`。
5. 没有可信路线几何时保留 `inferred_from_endpoints`，但置信度固定为 `0.2`，不伪装成真实经过。

本阶段没有删除节点或关系；旧 ID、旧标签和旧关系均保留。

## 2. 外部数据源与许可证

以下来源于 2026-07-26 检查。上线商业系统前仍应由项目方复核最新许可证和服务条款。

| 数据源 | 本项目用途 | 许可证/限制 | 是否采用 |
|---|---|---|---|
| [UNECE UN/LOCODE](https://unlocode.unece.org/publications/) | 港口及运输地点注册表、规范代码、坐标 | 本次下载使用 `datasets/un-locode` 镜像，声明 ODC PDDL 1.0；正式发布应保留 UNECE 归属 | 是 |
| [OurAirports](https://ourairports.com/data/) | 按 IATA 精确匹配机场坐标 | Public Domain | 是 |
| [GeoNames](https://download.geonames.org/export/dump/) | 没有精确注册表坐标时的城市中心点回退 | CC BY 4.0，必须署名 | 是，低置信度 |
| [Natural Earth](https://www.naturalearthdata.com/downloads/) | 海域多边形、国家陆地掩膜 | Public Domain；小比例尺，不适合导航 | 是 |
| [Marine Regions](https://www.marineregions.org/) | 霍尔木兹海峡、苏伊士运河公开边界范围 | CC BY 4.0；不用于法律边界或导航 | 是，仅边界框/复合范围 |
| [OSRM](https://github.com/Project-OSRM/osrm-backend) + [OpenStreetMap](https://www.openstreetmap.org/copyright) | 公路网络路径 | OSRM BSD-2-Clause；OSM 数据 ODbL 1.0；公共 Demo 不保证 SLA | 是，一次性初始化；生产建议自建 |
| [searoute-py](https://github.com/genthalili/searoute-py) | 开源海运网络估算 | Apache-2.0；仅用于合理可视化，不用于航海 | 是，估算 |
| WGS84 Great Circle | 空运端点大圆线 | 计算结果，不代表真实航路或飞行计划 | 代码已支持；当前 10 条空运数据均为 synthetic，因此未写真实几何 |
| [Nominatim 公共服务](https://operations.osmfoundation.org/policies/nominatim/) | 原计划用于地理编码 | 公共服务限制批量和高频请求，要求缓存且通常不超过每秒 1 请求 | 否 |

## 3. 坐标模型

每个 `TransportLocation` 现在可包含：

```text
latitude
longitude
coordinate_source
coordinate_source_url
coordinate_license
coordinate_collected_at
coordinate_confidence
coordinate_status
coordinate_record_id
coordinate_hash
canonical_unlocode
identity_status
geospatial_version
```

`coordinate_status` 的含义：

| 状态 | 含义 | 当前数量 |
|---|---|---:|
| `reference` | 精确匹配 UN/LOCODE 或 OurAirports | 33 |
| `estimated` | GeoNames 城市中心点，只用于低精度展示和路径端点回退 | 56 |
| `unavailable` | 没有足够证据，不写假坐标 | 13 |

当前坐标来源分布：

| 来源 | 数量 |
|---|---:|
| GeoNames `cities15000` 城市中心点 | 56 |
| UN/LOCODE 2025-1 | 17 |
| OurAirports `airports.csv` | 16 |
| 无可验证来源 | 13 |

仍未补坐标的 13 个节点为 10 个名为 `National Gateway` 的国家级合成仓库，以及：

- `ALASHANKOU_RAILWAY_PORT`
- `FAC-TYT-JP`
- `SEC-TSLA-GIGAFACTORY-BERLIN`

这些节点会明确返回 `unavailable`，不会再用图邻居坐标冒充真实位置。

### 3.1 兼容旧港口代码

数据库已有 4 个港口 ID 与官方 UN/LOCODE 含义不一致。为避免破坏前端和旧关系，本阶段保留原 `location_id` / `unlocode`，只增加规范别名：

| 旧代码 | 保存的 `canonical_unlocode` | 说明 |
|---|---|---|
| `CNSHA` | `CNSHG` | 上海港 |
| `CNNAN` | `CNNSA` | 南沙港 |
| `CNNGB` | `CNNBG` | 宁波港 |
| `CNSZX` | `CNSZP` | 深圳港 |

这些节点的 `identity_status` 为 `canonical_code_corrected_alias_preserved`。

## 4. 路线几何与可行性

`RouteSegment` 新增：

```text
geometry_geojson
geometry_source
geometry_source_url
geometry_license
geometry_status
geometry_confidence
geometry_distance_km
geometry_generated_at
geometry_method
geometry_is_navigational = false
feasibility_status
feasibility_reason
geometry_hash
geospatial_version
```

当前 613 条路段的结果：

| `geometry_status` | 数量 | 处理方式 |
|---|---:|---|
| `estimated_open_sea_network` | 5 | searoute-py 开源海运网络，置信度 `0.65` |
| `estimated_road_network` | 5 | OSRM + OSM，道路折线简化到约 `0.005°` 容差，置信度 `0.7` |
| `estimated_endpoint_fallback` | 5 | 没有可信铁路 Provider，只保留端点推断，不写 LineString |
| `invalid_cross_ocean` | 4 | 纯公路/铁路跨海不成立，推荐图会过滤 |
| `unavailable_synthetic` | 594 | 合成路线不提升为真实几何 |

被过滤的 4 条不合理路线：

```text
leg_cn_sha_krpus_rail_1
leg_cn_sha_krpus_road_1
leg_cn_sha_uslax_rail_1
leg_cn_sha_uslax_road_1
```

同国短途沿海路线不会仅因端点直线跨过海湾而误判。例如宁波到上海会先允许道路网络绕行；没有铁路 Provider 时仍标记为 `unverified`，不会声称存在已验证铁路。

## 5. 风险区几何

10 个 `GeoZone` 均已建立有效几何：

- 红海与苏伊士走廊
- 马六甲海峡
- 印度洋
- 太平洋
- 中东
- 霍尔木兹海峡
- 南海
- 上海港 50 km 运营缓冲区
- 新加坡港 50 km 运营缓冲区
- 鹿特丹港 50 km 运营缓冲区

风险区字段包括：

```text
geometry_geojson
geometry_source
geometry_source_url
geometry_license
geometry_collected_at
geometry_status
geometry_confidence
applicable_modes
geometry_hash
```

港口 50 km 缓冲区是项目运营范围，不是行政或法定港界；霍尔木兹海峡使用 Gazetteer 公布边界范围生成的低置信度矩形。

## 6. `PASSES_THROUGH` 关系

结构：

```text
(segment:RouteSegment)-[exposure:PASSES_THROUGH]->(zone:GeoZone)
```

关系字段：

```text
integration_id = "geospatial-exposure-v1"
active
exposure_method
intersection_distance_km
route_distance_km
exposure_ratio
confidence
geometry_status
exposure_hash
calculated_at
geospatial_version
```

数据库保留 69 条本集成关系记录，其中 65 条有效、4 条无效跨洋路线关系软停用：

| 方法 | 数量 | 置信度 |
|---|---:|---|
| `geometry_intersection` | 16 | `0.51` 至 `0.65` |
| `inferred_from_endpoints` | 49 | 固定 `0.2` |

4 条 `invalid_cross_ocean` 路线不会保留有效空间暴露关系，也不会进入 GDELT 或天气更新任务。

上海到汉堡海运路段当前几何命中 7 个区域：印度洋、马六甲海峡、中东、上海港、新加坡港、红海/苏伊士、南海。GDELT 每小时刷新时会优先读取这些关系，不再重新依赖国家端点猜测。

## 7. 对风险推荐链路的影响

```text
RouteSegment geometry
  -> Shapely 与 GeoZone 相交
  -> PASSES_THROUGH
  -> GitHub Actions 每小时更新对应 GeoZone 的 GDELT 风险
  -> RouteSegment.news_risk_score / provider_risk_score
  -> /api/routes/recommend 过滤高风险区并重新排序
```

- 有几何：只使用 `geometry_intersection` 结果。
- 无几何：保留 `inferred_from_endpoints`，同时返回低置信度。
- `invalid_cross_ocean`：不会进入 `/api/routes/recommend` 和 `/api/routes/optimize` 的候选图。
- Open-Meteo：优先沿 `geometry_geojson` 采样；估算几何仍只产生 `partial` 风险状态。
- 阶段 6 本身不是定时任务。坐标和静态几何只在迁移脚本运行时更新；GDELT 和天气仍由 GitHub Actions 按计划更新，与你本地是否启动 FastAPI 无关。

## 8. 前端新增或扩展接口

| 接口 | 用途 |
|---|---|
| `GET /api/geography/locations` | 坐标、来源、许可证、状态、置信度、规范 UN/LOCODE |
| `GET /api/geography/zones` | 风险区 GeoJSON、来源与当前 GDELT 风险 |
| `GET /api/geography/segments/{segment_id}` | 单条路线 GeoJSON、可行性与全部风险区交叉证据 |

`GET /api/routes/recommend` 的每个 `leg` 现在增加：

```text
geometry
geometrySource
geometryStatus
geometryConfidence
feasibilityStatus
spatialExposures
from/to.coordinateStatus
from/to.coordinateConfidence
```

示例：

```bash
curl "http://localhost:8000/api/geography/locations?status=reference"
curl "http://localhost:8000/api/geography/zones?include_geometry=true"
curl "http://localhost:8000/api/geography/segments/leg_cn_sha_de_ham_sea_1"
```

## 9. 新手执行步骤

### 9.1 安装依赖

```bash
cd /你的路径/supply-chain-api
source .venv/bin/activate
pip install -r requirements.txt
```

新增依赖为 `shapely`、`pyshp` 和 `searoute`。

### 9.2 仅在需要重建参考目录时下载源数据

仓库已经包含生成后的 `config/geospatial_reference.json`，普通部署不需要重复下载。只有升级源数据时才执行：

```bash
mkdir -p /tmp/stage6_sources
git clone https://github.com/datasets/un-locode.git /tmp/stage6_sources/un-locode
curl -L https://davidmegginson.github.io/ourairports-data/airports.csv \
  -o /tmp/stage6_sources/airports.csv
curl -L https://download.geonames.org/export/dump/cities15000.zip \
  -o /tmp/stage6_sources/cities15000.zip
curl -L https://naciscdn.org/naturalearth/10m/physical/ne_10m_geography_marine_polys.zip \
  -o /tmp/stage6_sources/marine.zip
curl -L https://naciscdn.org/naturalearth/110m/cultural/ne_110m_admin_0_countries.zip \
  -o /tmp/stage6_sources/countries.zip
```

然后生成紧凑参考目录：

```bash
python scripts/build_geospatial_reference.py \
  --unlocode-csv /tmp/stage6_sources/un-locode/data/code-list.csv \
  --ourairports-csv /tmp/stage6_sources/airports.csv \
  --geonames-zip /tmp/stage6_sources/cities15000.zip \
  --natural-earth-marine-zip /tmp/stage6_sources/marine.zip \
  --natural-earth-countries-zip /tmp/stage6_sources/countries.zip
```

预期摘要：102 个地点、89 个有坐标、13 个 unavailable、10 个风险区。

### 9.3 先 dry-run

```bash
python scripts/migrate_geospatial_data.py --dry-run
```

新数据库第一次希望获取 OSRM 公路网络时使用：

```bash
python scripts/migrate_geospatial_data.py --dry-run --enable-osrm
```

公共 OSRM 可能超时。脚本不会因此伪造公路几何；已有的 OSRM 几何在刷新失败时会保留，首次失败则降级为 `estimated_endpoint_fallback`。

### 9.4 确认后执行

```bash
python scripts/migrate_geospatial_data.py \
  --execute \
  --confirm APPLY_GEOSPATIAL_STAGE6 \
  --enable-osrm
```

### 9.5 再次验证幂等性

```bash
python scripts/migrate_geospatial_data.py --dry-run
```

本次实际最终结果为：地点更新 `0`、路段更新 `0`、风险区更新 `0`、关系写入 `0`、软停用 `0`。

## 10. Neo4j Browser 验证

坐标状态：

```cypher
MATCH (location:TransportLocation)
RETURN location.coordinate_status AS status, count(*) AS count
ORDER BY status;
```

路线几何状态：

```cypher
MATCH (segment:RouteSegment)
RETURN segment.geometry_status AS status, count(*) AS count
ORDER BY status;
```

查看上海到汉堡海运经过的区域：

```cypher
MATCH path=(segment:RouteSegment {segment_id:'leg_cn_sha_de_ham_sea_1'})
  -[exposure:PASSES_THROUGH]->(zone:GeoZone)
RETURN path, exposure.exposure_method, exposure.exposure_ratio, exposure.confidence;
```

查看被阻止的跨洋公路/铁路：

```cypher
MATCH (segment:RouteSegment {feasibility_status:'invalid_cross_ocean'})
RETURN segment.segment_id, segment.mode, segment.feasibility_reason
ORDER BY segment.segment_id;
```

## 11. 执行产物与数据库变化

- 首次完整执行：`artifacts/geospatial_migration_20260726T064112Z.json` / `.md`
- 道路几何压缩执行：`artifacts/geospatial_migration_20260726T065938Z.json` / `.md`
- 无效线路关系软停用：`artifacts/geospatial_migration_20260726T071627Z.json` / `.md`
- 最终幂等审计：`artifacts/geospatial_migration_20260726T071712Z.json` / `.md`
- 新增节点：0
- 删除节点：0
- `PASSES_THROUGH` 记录：69，其中有效 65、软停用 4
- 删除关系：0
- 首次更新地点：102
- 首次更新路段：613
- 首次更新风险区：10

`config/geospatial_reference.json` 是可复现的紧凑参考快照，不包含 AuraDB 密码或任何 API 密钥。
