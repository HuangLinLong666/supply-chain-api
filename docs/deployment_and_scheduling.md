# Render、GitHub Actions 与定时更新部署指南

> 面向第一次部署后端的使用者  
> 推荐架构：Vercel 前端 + Render FastAPI + Neo4j AuraDB + GitHub Actions 定时任务

## 1. 最重要的结论

- 本地电脑不需要一直开机。
- Render Web Service 负责提供 HTTP API，不会因为你关闭本地 FastAPI 而停止。
- GDELT 和 Open-Meteo 工作流直接写入 AuraDB，不需要先请求 Render 的 HTTP 接口。
- Render API 每次查询都会读取同一个 AuraDB，所以定时任务写入后，前端下一次请求就能读到新数据。
- AISStream.io 是持续 WebSocket 流，必须有常驻 worker；每小时 GitHub Actions 不能替代真正准实时的 AIS worker。
- `.env` 只供本地使用；Render 和 GitHub 分别在各自控制台配置环境变量或 Secrets。

推荐数据流：

```text
Vercel 前端
  -> Render FastAPI
  -> Neo4j AuraDB

GitHub Actions 每小时
  -> GDELT / Open-Meteo
  -> Neo4j AuraDB

常驻 AIS worker（可选）
  -> AISStream.io WebSocket
  -> Neo4j AuraDB
```

## 2. 第一次部署前检查

### 第 1 步：确认代码已推送到 GitHub

```bash
git status
git add .
git commit -m "complete staged supply chain backend"
git push origin main
```

只有你确认改动正确后再提交。Codex 不会替你自动提交。

### 第 2 步：确认 AuraDB 凭据

你需要四项：

```text
AURA_NEO4J_URI
AURA_NEO4J_USERNAME
AURA_NEO4J_PASSWORD
AURA_NEO4J_DATABASE
```

本地先验证：

```bash
cd "/Users/vegeta/全球供应链管理/supply-chain-api"
source .venv/bin/activate
python scripts/verify_aura_connection.py
```

`AURA_NEO4J_USERNAME` 通常是 `neo4j`。`AURA_NEO4J_DATABASE` 不确定时可以先留空，让驱动使用实例默认数据库；不要因为旧教程而强制写 `neo4j`。

### 第 3 步：本地运行测试

```bash
pytest -q
```

## 3. Render Web Service 逐步配置

### 3.1 创建服务

1. 登录 Render Dashboard。
2. 点击 `New`。
3. 选择 `Web Service`。
4. 连接 GitHub 仓库 `supply-chain-api`。
5. Branch 选择 `main`。
6. Runtime 选择 `Python 3`。
7. 可以选择 Free Web Service；空闲后首次访问可能需要等待冷启动。

### 3.2 构建与启动命令

Build Command：

```text
pip install -r requirements.txt
```

Start Command：

```text
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

不要把端口固定成 `8000`。Render 会通过 `$PORT` 告诉应用应监听哪个端口。

仓库已有 `render.yaml`，也可以使用 Blueprint 创建同样的 Web Service。

### 3.3 Render 必填环境变量

在 `Environment` 中逐项添加：

| 名称 | 填什么 | 是否敏感 |
|---|---|---|
| `AURA_NEO4J_URI` | `neo4j+s://...databases.neo4j.io` | 是 |
| `AURA_NEO4J_USERNAME` | 通常为 `neo4j` | 是 |
| `AURA_NEO4J_PASSWORD` | Aura 数据库密码 | 是 |
| `AURA_NEO4J_DATABASE` | 已验证的数据库名；不确定可留空 | 是 |
| `API_CORS_ORIGINS` | 前端域名，多个值用英文逗号分隔 | 否 |

示例：

```text
API_CORS_ORIGINS=https://your-project.vercel.app,http://localhost:3000,http://localhost:5173
```

不要在域名结尾加 `/`，也不要填写具体页面路径。

### 3.4 Render 建议变量

| 名称 | 推荐值 | 原因 |
|---|---|---|
| `WEATHER_SCHEDULER_ENABLED` | `false` | 天气由 GitHub Actions 更新，避免 Render 进程重复调度 |
| `GDELT_ADMIN_TOKEN` | 长随机字符串或暂不配置 | 保护手工 GDELT 更新接口 |
| `WEATHER_ADMIN_TOKEN` | 长随机字符串或暂不配置 | 保护手工天气更新接口 |

管理员 Token 只供后端运维请求使用，不能写进 Vercel 的 `NEXT_PUBLIC_*`、`VITE_*` 或前端代码。

### 3.5 部署后验证

假设公网地址是：

```text
https://supply-chain-api-kyiy.onrender.com
```

依次访问：

```text
https://supply-chain-api-kyiy.onrender.com/
https://supply-chain-api-kyiy.onrender.com/health
https://supply-chain-api-kyiy.onrender.com/health/aura
https://supply-chain-api-kyiy.onrender.com/docs
```

预期：

- `/` 返回服务信息，不再是 404；
- `/favicon.ico` 返回 204，不再产生无意义 404 日志；
- `/health` 表示 FastAPI 进程运行；
- `/health/aura` 表示 AuraDB 凭据和网络可用；
- `/docs` 显示 Swagger API 文档。

## 4. GitHub Actions 每小时更新 GDELT

工作流文件：

```text
.github/workflows/update-gdelt-risk.yml
```

计划：

```yaml
cron: "7 * * * *"
```

含义是每个 UTC 小时的第 7 分钟尝试运行。GitHub 定时任务可能因平台负载延迟，不保证精确到分钟。

### 4.1 配置 Secrets

1. 打开 GitHub 仓库。
2. 点击 `Settings`。
3. 左侧点击 `Secrets and variables`。
4. 点击 `Actions`。
5. 点击 `New repository secret`。
6. 逐个创建：

```text
AURA_NEO4J_URI
AURA_NEO4J_USERNAME
AURA_NEO4J_PASSWORD
AURA_NEO4J_DATABASE
```

Secret 名称必须完全一致，大小写也必须一致。值使用与 Render 相同的 AuraDB 凭据。

### 4.2 手工运行一次

1. 打开 GitHub 仓库的 `Actions`。
2. 左侧选择 `Update GDELT route risk`。
3. 点击右侧 `Run workflow`。
4. Branch 选择 `main`。
5. 再点击绿色 `Run workflow`。
6. 等待运行记录出现并打开。
7. 展开 `Fetch GDELT and update AuraDB`。
8. 确认任务绿色通过，并检查输出没有 `failures`。

### 4.3 验证结果

```bash
curl "https://supply-chain-api-kyiy.onrender.com/api/risk/news/clusters?active_only=true"
curl "https://supply-chain-api-kyiy.onrender.com/api/risk/news/zones?active_only=true"
```

如果返回空列表：

1. 检查 Actions 是否实际成功；
2. 检查新闻是否已超过 `GDELT_RISK_TTL_HOURS`；
3. 检查风险区查询是否得到可分类新闻；
4. 查看 `/api/providers/status`；
5. 不要把空列表自动解释为零风险。

## 5. GitHub Actions 每小时更新 Open-Meteo

工作流文件：

```text
.github/workflows/update-weather-risk.yml
```

计划：

```yaml
cron: "23 * * * *"
```

每次依次执行：

```text
python scripts/update_port_weather.py
python scripts/update_route_weather.py
```

第一条更新港口天气快照，第二条沿路线采样并重算 Provider 风险。

### 5.1 配置

它使用与 GDELT 相同的四个 GitHub Secrets，不需要额外 Open-Meteo API Key。

### 5.2 手工运行

1. 打开 `Actions`。
2. 选择 `Update Open-Meteo route weather risk`。
3. 点击 `Run workflow`。
4. 等待两个更新步骤都变为绿色。

### 5.3 验证

```bash
curl "https://supply-chain-api-kyiy.onrender.com/api/ports/weather-risks"
curl "https://supply-chain-api-kyiy.onrender.com/api/routes/weather-risks?active_only=true"
```

天气路线 TTL 默认 2 小时。每小时更新为任务延迟和一次失败留出缓冲；API 查询仍会检查过期时间，过期数据不会继续当作实时风险。

## 6. AISStream.io 为什么需要独立 worker

AISStream.io 通过 WebSocket 持续推送消息。它不是“每小时请求一次就得到完整一小时船流”的普通 HTTP API。

FastAPI Web Service 与 AIS worker 是两个进程：

```text
Render Web Service: uvicorn app.main:app ...
AIS Worker:         python scripts/run_ais_consumer.py
```

### 6.1 先检查配置

在 worker 环境配置：

```text
AURA_NEO4J_URI
AURA_NEO4J_USERNAME
AURA_NEO4J_PASSWORD
AURA_NEO4J_DATABASE
AISSTREAM_API_KEY
```

本地只检查配置、不联网：

```bash
python scripts/run_ais_consumer.py --check-config
```

### 6.2 正式运行

```bash
python scripts/run_ais_consumer.py
```

不提供 `--duration-seconds` 时进程持续运行。停止进程后不再接收新消息，已保存快照超过 90 分钟后变为陈旧。

### 6.3 免费方案边界

- GitHub Actions 可以定时启动短时采样，但会丢失大量连续状态，不能称为准实时。
- Render 免费 Web Service 会休眠，也不应把长期 WebSocket 消费器塞进 Web 进程。
- 可靠方案是 Render Background Worker、其他常驻云主机、学校服务器或自有服务器。
- 如果当前不部署 worker，API 仍可运行；AIS 风险会明确返回 `unavailable`。

完整说明见 `docs/aisstream_port_traffic.md`。

## 7. 所有环境变量说明

### 7.1 Neo4j 与 Web API

| 变量 | 作用 | 默认/建议 |
|---|---|---|
| `AURA_NEO4J_URI` | AuraDB Bolt URI | 必填 |
| `AURA_NEO4J_USERNAME` | AuraDB 用户名 | 通常 `neo4j` |
| `AURA_NEO4J_PASSWORD` | AuraDB 密码 | 必填，保密 |
| `AURA_NEO4J_DATABASE` | 目标数据库 | 可留空使用默认库 |
| `NEO4J_URI/USER/PASSWORD/DATABASE` | 非 Aura 或旧模块兼容变量 | Aura 变量优先 |
| `API_CORS_ORIGINS` | 允许浏览器访问 API 的来源 | 逗号分隔，无尾斜杠 |
| `APP_ENV` | v1 模块环境标记 | `development` |
| `APP_PORT` | 本地旧模块端口提示 | `8000`；Render 实际使用 `$PORT` |

### 7.2 Open-Meteo

| 变量 | 作用 | 默认值 |
|---|---|---:|
| `OPEN_METEO_BASE_URL` | 常规天气 API | 官方 forecast URL |
| `OPEN_METEO_MARINE_BASE_URL` | 海况 API | 官方 marine URL |
| `OPEN_METEO_GEOCODING_URL` | 地理编码 API | 官方 geocoding URL |
| `WEATHER_UPDATE_INTERVAL_MINUTES` | 内置调度间隔 | 60 |
| `WEATHER_REQUEST_TIMEOUT_SECONDS` | 单次请求超时 | 20 |
| `WEATHER_MAX_RETRIES` | 最大重试 | 3 |
| `WEATHER_BATCH_SIZE` | 单批港口数量 | 25 |
| `WEATHER_MAX_CONCURRENCY` | 预留并发设置 | 5 |
| `WEATHER_CACHE_TTL_MINUTES` | 本地请求缓存 | 45 |
| `WEATHER_SNAPSHOT_RETENTION_DAYS` | 历史快照保留天数 | 30 |
| `WEATHER_ROUTE_SAMPLE_POINTS` | 每条路线默认采样点 | 5 |
| `WEATHER_ROUTE_FORECAST_HOURS` | 最大预报窗口 | 168 |
| `WEATHER_ROUTE_RISK_TTL_HOURS` | 路线天气有效期 | 2 |
| `WEATHER_SCHEDULER_ENABLED` | 是否在 FastAPI 进程内启动天气调度 | 推荐 `false` |
| `WEATHER_ADMIN_TOKEN` | 天气管理接口 Token | 生产建议设置长随机值 |

### 7.3 GDELT

| 变量 | 作用 | 默认值 |
|---|---|---:|
| `GDELT_DOC_API_URL` | GDELT DOC 2.0 地址 | 官方 URL |
| `GDELT_BASE_URL` | v1 模块兼容地址 | 官方 URL |
| `GDELT_TIMESPAN` | 查询新闻时间窗口 | `48h` |
| `GDELT_MAX_RECORDS` | 每区域最大记录数 | 100 |
| `GDELT_REQUEST_TIMEOUT_SECONDS` | 请求超时 | 30 |
| `GDELT_MAX_RETRIES` | 最大重试 | 3 |
| `GDELT_MIN_REQUEST_INTERVAL_SECONDS` | 区域请求最小间隔 | 6 |
| `GDELT_RISK_TTL_HOURS` | 路线新闻风险有效期 | 3 |
| `GDELT_RISK_ZONES_FILE` | 风险区配置文件 | `config/gdelt_risk_zones.json` |
| `GDELT_ADMIN_TOKEN` | GDELT 管理接口 Token | 生产建议设置长随机值 |

### 7.4 AISStream.io

| 变量 | 作用 | 默认值 |
|---|---|---:|
| `AISSTREAM_API_KEY` | AISStream.io 密钥 | worker 必填，保密 |
| `AISSTREAM_ENDPOINT` | WebSocket 地址 | 官方 v0 stream |
| `AIS_TARGETS_CONFIG` | 四个监测区域配置 | `config/ais_observation_targets.json` |
| `AIS_FLUSH_INTERVAL_SECONDS` | 写入聚合结果间隔 | 60 |
| `AIS_AGGREGATION_WINDOW_MINUTES` | 港区统计窗口 | 60 |
| `AIS_SNAPSHOT_TTL_MINUTES` | 快照有效期 | 90 |
| `AIS_PROVIDER_STALE_SECONDS` | 多久无消息视为陈旧 | 300 |
| `AIS_RECONNECT_INITIAL_SECONDS` | 初始重连等待 | 2 |
| `AIS_RECONNECT_MAX_SECONDS` | 最大重连等待 | 60 |
| `AIS_OPEN_TIMEOUT_SECONDS` | 建连超时 | 20 |
| `AIS_PING_INTERVAL_SECONDS` | 心跳间隔 | 20 |
| `AIS_PING_TIMEOUT_SECONDS` | 心跳超时 | 20 |
| `AIS_DEDUPE_TTL_SECONDS` | 消息去重缓存有效期 | 600 |
| `AIS_DEDUPE_MAX_ENTRIES` | 去重缓存上限 | 100000 |

### 7.5 未来 Provider 预留

以下变量目前只是 v1 Provider 适配预留，配置后不代表已经得到生产数据：

```text
NEWSAPI_KEY
MARINETRAFFIC_API_KEY
OPENSKY_USERNAME
OPENSKY_PASSWORD
AVIATION_EDGE_API_KEY
FLIGHTAWARE_API_KEY
CIRIUM_APP_ID
CIRIUM_APP_KEY
OFAC_SLS_BASE_URL
UN_SANCTIONS_BASE_URL
EU_SANCTIONS_BASE_URL
ENABLE_PROVIDER_MARINETRAFFIC
ENABLE_PROVIDER_OPENSKY
ENABLE_PROVIDER_AVIATION_EDGE
ENABLE_PROVIDER_FLIGHTAWARE
ENABLE_PROVIDER_CIRIUM
CAAC_AIRPORT_CSV
```

`DEFAULT_CURRENCY`、`DEFAULT_DISTANCE_UNIT`、`DEFAULT_RISK_STRATEGY` 和 `DEFAULT_RANKING_STRATEGY` 是 v1 模块默认配置。前端主推荐接口仍以请求体的 `strategy`、`weights` 和 `constraints` 为准。

## 8. 常用脚本分别做什么

| 命令 | 是否写 AuraDB | 用途 |
|---|---|---|
| `python scripts/verify_aura_connection.py` | 只读 | 检查 URI、用户名、密码和数据库名 |
| `python scripts/audit_current_backend.py` | 只读 | 生成当前数据库与 API 审计 |
| `python scripts/update_gdelt_risk.py --dry-run` | 否 | 预览新闻抓取与风险映射 |
| `python scripts/update_gdelt_risk.py` | 是 | 更新 GDELT 新闻、区域和路线风险 |
| `python scripts/update_port_weather.py --dry-run` | 否 | 拉取并计算天气，但不写数据库 |
| `python scripts/update_port_weather.py --port-id CNSHA` | 是 | 只更新指定港口；参数可重复 |
| `python scripts/update_route_weather.py --dry-run` | 否 | 预览路线天气采样 |
| `python scripts/update_route_weather.py` | 是 | 更新路线天气并重算风险 |
| `python scripts/run_ais_consumer.py --check-config` | 否 | 检查 AIS Key 和订阅区域，不联网 |
| `python scripts/run_ais_consumer.py --dry-run --duration-seconds 120` | 否 | 短时接收 AIS，只在内存聚合 |
| `python scripts/run_ais_consumer.py` | 是 | 常驻消费 AIS 并写最新状态/聚合快照 |
| `python scripts/cleanup_synthetic_data.py --dry-run` | 否 | 安全识别合成数据并生成备份报告 |
| `python scripts/recalculate_provider_risk.py --dry-run` | 否 | 预览无来源风险清理和重算 |
| `python scripts/validate_stage9_data.py` | 只读 | 对真实 AuraDB 执行验收检查 |
| `uvicorn app.main:app --reload` | API 查询为主 | 本地启动 FastAPI；`--reload` 只用于开发 |
| `pytest -q` | 不应访问生产库 | 运行自动测试 |

迁移脚本默认先预览；真正执行必须带专用确认口令。详见 `docs/database_cleanup.md` 和各阶段文档。

## 9. 前端怎样配置

Vercel 环境变量：

```text
NEXT_PUBLIC_API_BASE_URL=https://supply-chain-api-kyiy.onrender.com
```

如果是 Vite：

```text
VITE_API_BASE_URL=https://supply-chain-api-kyiy.onrender.com
```

前端只保存 API Base URL。以下内容绝不能进入前端：

```text
AURA_NEO4J_PASSWORD
AISSTREAM_API_KEY
GDELT_ADMIN_TOKEN
WEATHER_ADMIN_TOKEN
任何第三方 Provider API Key
```

## 10. 推荐的上线验证顺序

```bash
curl "https://supply-chain-api-kyiy.onrender.com/health"
curl "https://supply-chain-api-kyiy.onrender.com/health/aura"
curl "https://supply-chain-api-kyiy.onrender.com/api/providers/status"
curl "https://supply-chain-api-kyiy.onrender.com/api/suppliers"
curl "https://supply-chain-api-kyiy.onrender.com/api/cities"
```

然后使用 `POST /api/routes/recommend` 完成端到端验证。请求示例见 `docs/route_recommendation.md`。

## 11. 常见问题

### Render 日志出现 `GET / 404`

旧版本没有根路由时，浏览器访问域名会得到 404。当前代码已经提供 `/`，部署最新 commit 后应返回服务信息。如果仍是 404，说明 Render 尚未部署当前分支或构建失败。

### GitHub Actions 成功，但 Render 没有“重启”

不需要重启。工作流直接写 AuraDB；Render 下一次 API 查询会读取新数据。路线图有 60 秒进程缓存，最迟等待约一分钟再查询。

### 本地关闭后数据是否停止

- GDELT/Open-Meteo：不会，GitHub Actions 继续运行。
- Render API：不会，Render 独立运行。
- 本地 AIS consumer：会停止；必须部署常驻 worker 才能持续更新 AIS。

### 为什么 GitHub 每小时任务没有准点执行

GitHub cron 使用 UTC，且平台不保证精确启动时间。业务代码依靠 TTL 判断数据是否仍有效；应监控最后成功时间，而不是只看计划表达式。

### 管理更新接口能否让前端调用

不建议。以下接口是运维接口，应要求 Token，并由 GitHub Actions、受控后端或管理员工具调用：

```text
POST /api/admin/gdelt/update
POST /api/admin/weather/update
POST /api/admin/weather/routes/update
```

普通前端只调用 GET 查询和 `POST /api/routes/recommend`。

## 12. 最终检查清单

- [ ] Render 已部署 `main` 最新 commit。
- [ ] `/health/aura` 返回 connected。
- [ ] `API_CORS_ORIGINS` 包含准确 Vercel 域名。
- [ ] GitHub 四个 Aura Secrets 已配置。
- [ ] 两个 Actions 均已手工运行成功。
- [ ] `/api/providers/status` 能显示 Provider 状态和最后更新时间。
- [ ] GDELT、天气过期时前端显示 unavailable，而不是零风险。
- [ ] AIS Key 只存在于常驻 worker。
- [ ] Vercel 只配置后端 Base URL，没有任何数据库或 Provider 密钥。
