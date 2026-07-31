# 地点 ID 统一命名规范（location-id-v2）

## 1. 结论


### 1.1 国家代码与国家名称的唯一规则

国家身份不使用 `America`、`USA`、`美国` 等可变名称作为主键，统一使用 **ISO 3166-1 alpha-2** 两位代码。

```text
US = United States = 美国
CN = China = 中国
GB = United Kingdom = 英国
DE = Germany = 德国
```

选择这个标准的原因：

- ISO 3166-1 alpha-2 是国际通用的国家/地区稳定代码。
- UN/LOCODE 明确使用 ISO 3166-1 alpha-2 作为前两位，与本项目港口和运输地点体系直接兼容。
- 界面显示名使用 Unicode CLDR 当前语言的常用名称，而不是容易变化的法律全称。

后端固定保存：

| 字段 | 规则 | 美国示例 |
|---|---|---|
| `country_code` | ISO 3166-1 alpha-2 业务主键 | `US` |
| `country` | CLDR 英文常用名 | `United States` |
| `country_name_en` | 与 `country` 一致 | `United States` |
| `country_name_zh` | CLDR 简体中文常用名 | `美国` |
| `country_aliases` | 历史输入和兼容别名 | `US`、`USA`、`America`、`美国` 等 |
| `country_naming_version` | 当前规则版本 | `iso3166-1-alpha2-cldr-v1` |

`America` 本身可能指美洲，不是推荐的国家输入。后端仅为兼容项目旧数据将它解析为 `US`；`North America` 等洲/大区名称不会被猜测成某个国家。新数据必须直接提供 `US`。

标准依据：[ISO 3166 Country Codes](https://www.iso.org/iso-3166-country-codes.html)、[UNECE Recommendation 16](https://unlocode.unece.org/recommendation16/)、[Unicode CLDR Territory Names](https://cldr.unicode.org/translation/displaynames/countryregion-territory-names)。后端通过锁定的 `Babel 2.18` 读取 CLDR 显示名。

## 2. 各类型格式

| 地点类型 | 格式 | 示例 |
|---|---|---|
| 港口 | `PORT-{UNLOCODE}` | `PORT-CNSHG` |
| 机场 | `AIR-{IATA}`，无 IATA 时使用 ICAO | `AIR-PVG` |
| 铁路站 | `RAIL-{ISO2}-{STATION_CODE}` | `RAIL-CN-ALASHANKOU` |
| 公路场站 | `ROAD-{ISO2}-{TERMINAL_CODE}` | `ROAD-DE-HAMBURG-01` |
| 工厂 | `FAC-{ISO2}-{COMPANY}-{SITE}` | `FAC-CN-CATL-ND` |
| 仓库 | `WH-{ISO2}-{OWNER_OR_SITE}` | `WH-CN-SHANGHAI` |

港口优先使用 `canonical_unlocode`，不再把旧项目缩写当作官方代码。机场优先使用 IATA；不同类型通过前缀避免 `CAN` 同时表示机场和仓库等冲突。

## 3. 前端字段约定

地点接口统一返回：

```json
{
  "id": "PORT-CNSHG",
  "locationId": "PORT-CNSHG",
  "locationType": "port",
  "name": "上海港",
  "city": "Shanghai",
  "country": "China",
  "countryCode": "CN",
  "countryNameZh": "中国",
  "countryAliases": ["CN", "China", "中国"],
  "countryNamingVersion": "iso3166-1-alpha2-cldr-v1",
  "canonicalUnlocode": "CNSHG",
  "aliases": ["CN-SHA", "CNSHA", "CNSHG"],
  "locationIdVersion": "location-id-v2"
}
```

前端选择器必须使用：

```javascript
{ value: location.locationId, label: location.name }
```

推荐请求必须提交稳定 ID：

```json
{
  "origin": "PORT-CNSHG",
  "destination": "PORT-DEHAM"
}
```

前端不得根据地点名称自行拼接 ID。旧 ID 只用于兼容已有链接、缓存和历史请求。

`GET /api/suppliers/{supplier_id}/origins` 会把旧 `SHIPS_FROM` 指向的 `EntryPoint/ChinaOrigin` 解析为具有出发路段的规范 `TransportLocation`，响应中的 `origins[].id` 和 `origins[].locationId` 都是 v2 ID，并通过 `resolutionStatus=resolved_from_legacy_supplier_origin` 明确这是兼容解析结果。

## 4. 当前主要转换

| 旧 ID | 新 ID |
|---|---|
| `CN-SHA`、`CNSHA`、`CNSHG` | `PORT-CNSHG` |
| `SGSIN` | `PORT-SGSIN` |
| `NLRTM` | `PORT-NLRTM` |
| `DE-HAM` | `PORT-DEHAM` |
| `US-LAX` | `PORT-USLAX` |
| `CN-PVG` | `AIR-PVG` |
| `DE-FRA` | `AIR-FRA` |
| `ALASHANKOU_RAILWAY_PORT` | `RAIL-CN-ALASHANKOU` |
| `FAC-CATL-ND` | `FAC-CN-CATL-ND` |
| `DE-AWH` | `WH-DE-AWH` |

完整的本次数据库映射保存在 `artifacts/location_id_v2_plan.json`。

## 5. 数据库字段

迁移后每个 `TransportLocation` 保存：

```text
location_id                 当前统一主键
canonical_location_id       与 location_id 相同，明确表达规范身份
location_aliases            旧ID、UN/LOCODE、IATA、ICAO等兼容别名
previous_location_id        本次迁移前的主键
location_id_version         location-id-v2
location_id_migrated_at     实际迁移时间
location_kind               port/airport/rail_terminal/road_terminal/factory/warehouse
country_code                ISO 3166-1 alpha-2 稳定代码
country                     CLDR 英文常用名
country_name_en             CLDR 英文常用名
country_name_zh             CLDR 简体中文常用名
country_aliases             历史名称和多语言别名
country_naming_version      iso3166-1-alpha2-cldr-v1
```

`Route`、`RouteLeg` 和 `RouteSegment` 中独立保存的 `origin_id`、`destination_id` 会同步更新。节点之间的 `FROM_NODE`、`TO_NODE`、`SHIPS_FROM` 等关系直接连接节点，不会因属性换键断开。

推荐图和地点选择接口只接纳 `TransportLocation`。旧数据中错误连接到 `RouteSegment` 的零部件、订单、产品和供应商不会被赋予伪造地点 ID，也不会继续出现在地点下拉框或正式推荐候选中。

历史 `RecommendationSnapshot.response_json` 不改写，因为它是当时推荐结果的审计记录。

## 6. 兼容规则

以下输入在过渡期仍然可以解析：

```text
新 location_id
location_aliases 中的旧 ID
UN/LOCODE
IATA / ICAO
地点名称或城市名称搜索
```

响应始终返回新 `locationId`。前端收到响应后应更新本地缓存，不应继续保存旧 ID。

国家查询可兼容 `US`、`United States`、`USA`、`America`、`美国` 等别名，但响应始终返回 `countryCode=US`、`country=United States`、`countryNameZh=美国`。

