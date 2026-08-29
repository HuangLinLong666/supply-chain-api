# SEC-01：公开仓库 tracked `.env` 凭据事件安全收口

## 1. 文档状态

**部分完成：凭据轮换与停止跟踪准备完成，等待安全分支提交授权；历史清理另案处理。**

- 复核日期：2026-08-30（Asia/Shanghai）。
- 本轮只进行安全状态核查和本文档收口。
- 尚未创建安全分支，尚未暂存本文档，尚未提交或推送。
- 实时远端 `main` 已成功刷新并与预期基线一致；安全分支和提交计划已经冻结，等待精确授权。
- 本文档不包含、也没有通过核查命令读取任何 `.env` 内容或凭据真实值。

## 2. 事件范围

- `.env` 曾被 Git 跟踪。
- `.env` 的历史涉及 6 个提交。
- 本次只记录提交关系和元数据，不查看、不复制、不展示这些提交中的 `.env` 内容。

| 提交 SHA | 提交时间 | 提交说明 |
|---|---|---|
| `1a78b3aa2ea8a74395287ffe199dc810c291abff` | `2026-07-21T15:33:30+08:00` | `新增整车运输网络端口` |
| `5c5365dcb33fedd1e070558da89a55a514fee945` | `2026-07-10T12:36:44+08:00` | `Update .env` |
| `16dc057480655e9977c4c8e77b137b2091175ba1` | `2026-07-10T12:28:22+08:00` | `Update .env` |
| `624847f8377cc75502265793405e29df418acfe2` | `2026-07-10T12:26:00+08:00` | `Update Neo4j username in .env file` |
| `b3dd610600c767c93a96c621ce7f57c09ebb2f71` | `2026-07-10T12:24:20+08:00` | `Update .env` |
| `5b12d960140ed438025f549fa9f3c01dfa130710` | `2026-07-10T12:20:24+08:00` | `Create .env` |

## 3. 凭据处置结论

以下仅记录类别和状态，不记录任何密码、Token、URI、Cookie 或环境变量真实值。

| 类别 | 状态 |
|---|---|
| Neo4j/AuraDB | 已轮换 |
| 新密码只读验证 | 通过 |
| AISStream | 未使用 |
| NewsAPI | 未使用 |
| 航空 Provider | 未使用 |
| MarineTraffic | 未使用 |
| `GDELT_ADMIN_TOKEN` | 未使用 |
| `WEATHER_ADMIN_TOKEN` | 未使用 |
| GitHub Actions 密文引用 | 已轮换 |
| GitHub 定时工作流 | 已禁用 |
| Vercel Development 密文引用 | 已轮换 |
| v2 路线 API | 通过 |

仓库所有者已确认：所有实际使用的凭据均已轮换，旧值已撤销。

## 4. 当前缓解状态

- `.env` 已从当前 Git index 停止跟踪。
- 本地工作树中的 `.env` 文件仍然存在。
- `.gitignore` 第 1 行存在精确 `.env` 规则，本地文件继续被忽略。
- 当前 index 中唯一预期变化是删除 Git 跟踪路径 `.env`。
- 当前唯一预期未跟踪路径是 `docs/security/tracked-env-incident-preflight.md`。
- `.gitignore`、`.env.example`、业务代码、测试、部署配置和锁文件没有变化。
- 当前尚未创建安全分支、提交或推送。

必须明确区分以下状态：

1. **凭据轮换与本地停止跟踪准备完成。**
2. **安全分支提交尚未执行。**
3. **历史中的旧 `.env` 仍然存在，但其中实际使用的旧凭据已撤销。**
4. **历史重写是后续独立、需要协作者协调和单独授权的高风险任务。**

SEC-01 目前不能描述为已经从 Git 历史彻底清除。

## 5. 本轮硬门核查结果

预期基线：`747c0a879b11b81bc513b44abceb78c821f1e6a3`。

| 核查项 | 不含秘密的结果 | 状态 |
|---|---|---|
| 当前分支 | `main` | 通过 |
| 本地 HEAD | 与预期基线一致 | 通过 |
| 本地缓存的 `origin/main` | 与预期基线一致 | 通过 |
| 实时远端 `main` | 成功刷新，与预期基线一致 | 通过 |
| `git status --short` | 仅 `D  .env` 和未跟踪安全文档 | 通过 |
| staged 路径状态 | 精确只有 `D .env` | 通过 |
| staged 路径名称 | 精确只有 `.env` | 通过 |
| `git ls-files .env` | 无输出 | 通过 |
| 本地 `.env` 文件 | 存在，未读取 | 通过 |
| `.env` 忽略来源 | `.gitignore` 第 1 行精确规则 `.env` | 通过 |
| 其他未解释变化 | 无 | 通过 |

全部硬门通过，可以冻结第 7 节的不可扩大计划；冻结不等于授权执行，本轮仍不得创建分支、暂存文档、提交或推送。

## 6. 实际核查命令

本轮使用了以下不会展示 `.env` 内容的 Git 或文件存在性检查：

```bash
git rev-parse HEAD
git status --short --untracked-files=all
git diff --cached --name-status
git diff --cached --name-only
git ls-files .env
git check-ignore -v --no-index .env
test -f .env
git show-ref
git fetch --no-tags origin main
git rev-parse origin/main
```

`git fetch --no-tags origin main` 已成功刷新实时远端引用，本地 HEAD、`origin/main` 和预期基线一致；其余结果符合第 5 节记录。没有运行普通 staged diff、补丁展示命令或任何 `.env` 内容读取命令。

## 7. 已冻结的安全分支和提交计划

### 7.1 固定边界

- `batchId`：`SEC-01-BRANCH-COMMIT-01`
- 基础提交：`747c0a879b11b81bc513b44abceb78c821f1e6a3`
- 远端 `main`：`747c0a879b11b81bc513b44abceb78c821f1e6a3`
- 新分支：`security/sec-01-stop-tracked-env`
- 提交主题：`security(repo): 停止跟踪本地环境文件`
- 允许的提交树变化：`D .env`、`A docs/security/tracked-env-incident-preflight.md`
- 新增 Git 跟踪路径：1；删除 Git 跟踪路径：1；工作树文件删除：0；业务代码修改：0。
- `.gitignore`、`.env.example`、业务代码、测试、配置和锁文件修改：0。
- push、PR、历史重写、部署和数据库访问：全部不允许。
- 不运行会展示 `.env` 删除内容的普通 staged diff、patch 或完整 `git show`。

### 7.2 规范 JSON

以下 JSON 使用 UTF-8，对象 key 按字典序排序，无多余空格且无结尾换行；数组顺序固定。任何字符变化都会使计划失效并要求重新计算摘要、重新授权。

```json
{"allowedCommitPaths":[".env","docs/security/tracked-env-incident-preflight.md"],"allowedIndexChanges":[{"path":".env","status":"D"},{"path":"docs/security/tracked-env-incident-preflight.md","status":"A"}],"batchId":"SEC-01-BRANCH-COMMIT-01","branchName":"security/sec-01-stop-tracked-env","commitSubject":"security(repo): 停止跟踪本地环境文件","databaseAccessAllowed":false,"deploymentAllowed":false,"expectedAddedTrackedPaths":["docs/security/tracked-env-incident-preflight.md"],"expectedBusinessCodeChanges":0,"expectedHead":"747c0a879b11b81bc513b44abceb78c821f1e6a3","expectedRemoteMain":"747c0a879b11b81bc513b44abceb78c821f1e6a3","expectedRemovedTrackedPaths":[".env"],"expectedWorkingTreeFileDeletions":0,"historyRewriteAllowed":false,"pushAllowed":false,"rollbackAfterCommit":"停止并请求单独授权；不得自动删除本地提交或未推送分支","rollbackBeforeCommit":"停止并请求用户决定；不得自动切回 main 或使用 reset --hard；必须保留本地 .env","verificationCommands":{"afterCommit":["git status --short","git show --stat --oneline --summary HEAD","git show --name-status --format= HEAD","git ls-files .env","git check-ignore -v --no-index .env","test -f .env","git branch --show-current"],"beforeCommit":["git status --short","git diff --cached --name-status","git diff --cached --name-only","git ls-files .env","git check-ignore -v --no-index .env","test -f .env"]}}
```

- 计划 SHA-256：`4e870daa2f7d0e9a470932fc355ec0d13fe66808b024a78a7d704d21688d81b4`

### 7.3 提交前验证

```bash
git status --short
git diff --cached --name-status
git diff --cached --name-only
git ls-files .env
git check-ignore -v --no-index .env
test -f .env
```

### 7.4 提交后验证

```bash
git status --short
git show --stat --oneline --summary HEAD
git show --name-status --format= HEAD
git ls-files .env
git check-ignore -v --no-index .env
test -f .env
git branch --show-current
```

不得运行 `git show HEAD`、`git show --patch`、`git diff HEAD^ HEAD` 或任何可能展示 `.env` 删除内容的命令。

### 7.5 下一轮精确授权文本

```text
我授权执行安全批次 SEC-01-BRANCH-COMMIT-01，其规范计划 SHA-256 为 4e870daa2f7d0e9a470932fc355ec0d13fe66808b024a78a7d704d21688d81b4。

只允许从基础提交 747c0a879b11b81bc513b44abceb78c821f1e6a3 的当前 main 创建并切换到 security/sec-01-stop-tracked-env，保留已经暂存的 D .env，将 docs/security/tracked-env-incident-preflight.md 加入 index，并创建主题为“security(repo): 停止跟踪本地环境文件”的一个提交。

提交树变化必须精确只有 D .env 和 A docs/security/tracked-env-incident-preflight.md；本地 .env 文件必须继续存在且不得读取；工作树文件删除数量为 0；业务代码修改数量为 0；.gitignore、.env.example、业务代码、测试、配置和锁文件不得修改。

提交前后只允许使用文档第 7.3 和 7.4 节列出的路径级安全命令验证。不得运行会显示 .env 内容或删除补丁的命令。不得 push、创建 PR、重写历史、部署、连接数据库或外部业务服务。完成本地提交后立即停止并报告不含秘密的路径级结果。
```

## 8. 恢复原则

由于本轮没有创建分支、暂存文档或提交，不需要执行恢复操作。当前已经暂存的 `.env` 跟踪删除记录必须保持不变，且不得删除本地 `.env` 文件。

后续计划必须分别遵守：

1. 创建分支后、提交前如需退出，应停止并请求用户决定；不得自动切回 `main`，不得使用 `reset --hard`，不得丢弃本地 `.env`。
2. 提交后、推送前，安全分支仍只存在本地；删除未推送分支或提交必须另行授权，不得自动执行。
3. 推送不属于分支提交批次，必须单独授权。

## 9. 历史清理另案处理

停止跟踪只能保护未来提交，不能删除历史中的旧 `.env`。历史清理必须建立独立批次，并至少满足：

- 所有实际使用凭据已经轮换并撤销旧值；
- 完整仓库备份已经验证；
- 协作者暂停推送；
- 仓库所有者批准清理范围和维护窗口；
- 已列出分支、标签、PR、保护规则、fork、镜像和自动化影响；
- 执行后协作者重新克隆；
- force push 获得单独授权。

本轮不执行历史重写，不提供可误执行的一键强推命令。

## 10. 范围声明

- 本轮没有修改 `.env`、`.gitignore`、`.env.example`、业务代码、测试、部署配置或锁文件。
- 本轮没有创建分支、执行 `git add`、提交、推送、部署或访问数据库及 Provider。
- Freightos 或其他新功能开发不属于本次安全收口范围。
