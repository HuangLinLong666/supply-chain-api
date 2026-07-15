# GDELT 全球新闻风险与动态避险：新手配置教程

本文档适合第一次配置 GitHub Actions、AuraDB 和定时任务的用户。请按顺序操作，不要跳步。

## 一、系统现在会做什么

每小时执行一次以下流程：

```text
GitHub Actions
  -> 调用 GDELT DOC 2.0 新闻 API
  -> 分别搜索红海、马六甲、印度洋、太平洋、中东等区域新闻
  -> 对战争、袭击、制裁、关税、罢工、拥堵、天气等新闻评分
  -> 将 NewsRiskEvent 和 NewsRiskZone 写入 AuraDB
  -> 找出经过风险区域的海运或空运 RouteSegment
  -> 生成三小时有效的动态风险
  -> FastAPI 推荐路线时自动避开 HIGH/CRITICAL 路段
```

目前配置区域位于 `config/gdelt_risk_zones.json`：

- 红海与苏伊士走廊；
- 马六甲海峡；
- 印度洋；
- 太平洋；
- 中东海运和空运区域；
- 霍尔木兹海峡；
- 南海；
- 上海港、新加坡港、鹿特丹港。

基础风险不会被覆盖。动态风险公式为：

```text
动态风险 = 1 - (1 - 基础风险) × (1 - 新闻风险)
```

新闻风险低于 `0.60` 时参与普通风险排序；达到 `0.60` 后，系统优先排除该路段并寻找替代路线。如果排除后起终点不再连通，系统会回退到原网络，并在响应中返回 `fallbackUsed=true`。

## 二、开始前需要准备什么

你需要：

1. 一个保存本项目的 GitHub 仓库；
2. 可以登录 GitHub 仓库设置页面；
3. AuraDB 的 URI、用户名、密码和数据库名；
4. 当前项目代码已经能在本地连接 AuraDB。

AuraDB 四项配置通常来自本地 `.env`：

```dotenv
AURA_NEO4J_URI=neo4j+s://xxxxxxxx.databases.neo4j.io
AURA_NEO4J_USERNAME=neo4j
AURA_NEO4J_PASSWORD=你的AuraDB密码
AURA_NEO4J_DATABASE=你的数据库名
```

不要把 `.env` 上传到 GitHub，也不要把密码写进工作流文件。

## 三、确认定时工作流文件存在

在 VS Code 左侧文件列表确认存在：

```text
.github/workflows/update-gdelt-risk.yml
```

其中：

```yaml
schedule:
  - cron: "7 * * * *"
```

表示每小时第 7 分钟触发。GitHub cron 使用 UTC，但这里每个小时都执行一次，因此不需要换算中国时区。

## 四、将代码推送到 GitHub

在 VS Code 终端进入后端目录：

```bash
cd "/Users/vegeta/全球供应链管理/supply-chain-api"
```

查看文件状态：

```bash
git status
```

确认无误后提交并推送。下面的提交说明可以自行修改：

```bash
git add .
git commit -m "Add hourly GDELT dynamic route risk"
git push
```

如果 `git push` 提示没有上游分支，按照终端给出的 `git push --set-upstream ...` 命令操作。

## 五、在 GitHub 添加 AuraDB Secrets

打开浏览器，进入你的 GitHub 后端仓库，然后按以下顺序点击：

1. 点击仓库顶部的 **Settings**；
2. 点击左侧 **Secrets and variables**；
3. 点击 **Actions**；
4. 点击 **New repository secret**；
5. 分别创建下面四个 Secret。

### Secret 1

```text
Name: AURA_NEO4J_URI
Secret: 复制本地 .env 中 AURA_NEO4J_URI 等号右边的完整内容
```

必须保留 `neo4j+s://`，不要添加引号和多余空格。

### Secret 2

```text
Name: AURA_NEO4J_USERNAME
Secret: 通常是 neo4j
```

### Secret 3

```text
Name: AURA_NEO4J_PASSWORD
Secret: 你的 AuraDB 数据库密码
```

### Secret 4

```text
Name: AURA_NEO4J_DATABASE
Secret: 本地 .env 中当前可以正常连接的数据库名
```

Secret 名称必须完全一致，包括大写字母和下划线。

## 六、第一次不要等一小时，手动运行

1. 打开 GitHub 仓库；
2. 点击顶部 **Actions**；
3. 在左侧选择 **Update GDELT route risk**；
4. 点击右侧 **Run workflow**；
5. 选择主分支；
6. 再点击绿色 **Run workflow**；
7. 等待页面出现一条新的运行记录；
8. 点击运行记录，再点击 `update` 查看日志。

成功时，`Fetch GDELT and update AuraDB` 步骤会输出类似：

```json
{
  "zonesUpdated": 10,
  "segmentsScanned": 573,
  "segmentsExposed": 80,
  "failures": []
}
```

实际数量会随数据库和新闻变化，不要求与示例完全相同。关键是 `zonesUpdated` 大于 0，且 `failures` 为空。

## 七、验证 AuraDB 是否收到新闻风险

打开 Neo4j Aura Console 的 Query 工具，先运行：

```cypher
MATCH (z:NewsRiskZone)
RETURN z.name, z.current_risk_score, z.current_risk_level,
       z.article_count, z.updated_at, z.expires_at
ORDER BY z.current_risk_score DESC;
```

再查看新闻：

```cypher
MATCH (e:NewsRiskEvent)-[:AFFECTS_ZONE]->(z:NewsRiskZone)
RETURN z.name, e.title, e.severity, e.url, e.seen_at
ORDER BY e.seen_at DESC
LIMIT 30;
```

最后查看受影响路段：

```cypher
MATCH (s:RouteSegment)-[:EXPOSED_TO_NEWS_RISK]->(z:NewsRiskZone)
RETURN s.fromNodeName, s.toNodeName, s.mode,
       z.name, z.current_risk_score,
       s.news_risk_score, s.dynamic_risk_score,
       s.news_risk_expires_at
ORDER BY s.dynamic_risk_score DESC;
```

## 八、验证 Render API

代码推送后等待 Render 自动部署完成，然后访问：

```text
https://你的Render域名/api/risk/news/zones
```

查看新闻列表：

```text
https://你的Render域名/api/risk/news
```

调用推荐路线：

```text
https://你的Render域名/api/routes/recommend?supplier=CATL&origin=Shanghai&destination=Hamburg&auto_reroute=true
```

响应中的动态改道信息：

```json
{
  "dynamicRouting": {
    "rerouted": true,
    "avoidedZones": ["malacca-strait", "red-sea"],
    "fallbackUsed": false
  }
}
```

- `rerouted=true`：找到并使用了不经过高风险区域的替代路径；
- `avoidedZones`：本次避开的区域；
- `fallbackUsed=true`：图数据库没有可达替代路径，只能返回原网络中的路线。

## 九、为什么有风险新闻却没有绕行

动态算法只能在数据库已经存在的路线中选择。比如数据库只有“新加坡港 -> 鹿特丹港”这一条边，没有好望角、铁路或空运替代边，那么算法无法凭空创建真实路线。

要实现真正绕行，数据库至少需要类似：

```text
方案 A：新加坡 -> 红海/苏伊士 -> 鹿特丹
方案 B：新加坡 -> 好望角 -> 鹿特丹
方案 C：新加坡 -> 中欧铁路节点 -> 汉堡
方案 D：新加坡机场 -> 欧洲机场
```

系统会排除风险达到 `0.60` 的方案 A，再在 B、C、D 中选择综合风险最低的可达路线。

## 十、修改或新增风险区域

编辑 `config/gdelt_risk_zones.json`，增加一项：

```json
{
  "id": "panama-canal",
  "name": "巴拿马运河",
  "type": "maritime_corridor",
  "query": "\"Panama Canal\" (shipping OR trade) (closure OR drought OR disruption OR tariff OR strike)",
  "aliases": ["panama canal", "巴拿马运河"]
}
```

只新增 JSON 能让系统采集新闻并识别名称直接包含该区域的节点。若还需要根据起终点国家自动推断该走廊，需要在 `gdelt/exposure.py` 的 `inferred_exposure()` 中增加区域规则和测试。

## 十一、常见错误

### `Missing AURA_NEO4J_URI`

GitHub Secrets 没有创建、名称拼错，或者工作流运行的不是包含 Secrets 的仓库。

### `Authentication failed`

用户名、密码错误，或者 AuraDB 密码已经重置但 GitHub Secret 仍是旧密码。更新 Secret 后重新运行工作流。

### `DatabaseNotFound`

`AURA_NEO4J_DATABASE` 填错。使用本地已经验证成功的数据库名，不要想当然填写 `neo4j`。

### GDELT `429` 或每 5 秒一次提示

公开接口有限流。采集器默认每个请求等待 6 秒并自动重试。不要把 `GDELT_MIN_REQUEST_INTERVAL_SECONDS` 调到 5 以下。

### GitHub 定时任务没有准点运行

GitHub cron 可能延迟几分钟。先用 `Run workflow` 验证配置是否正确；定时工作流只会运行默认分支上的工作流文件。

### `fallbackUsed=true`

这不是程序错误，表示删除高风险路段后起点和终点不连通，需要给 Neo4j 增加替代路线。

## 十二、本地只做测试，不需要持续运行

你不需要保持本地 FastAPI 或脚本开启。以下命令只用于临时检查：

```bash
python scripts/update_gdelt_risk.py --dry-run
pytest -q
```

真正的每小时更新由 GitHub Actions 完成，数据直接写入 AuraDB；Render API 下次查询时会读取 AuraDB 最新风险。
