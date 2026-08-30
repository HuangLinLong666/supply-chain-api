# SEC-01：凭据事件后安全复查

## 1. 状态与范围

- 复查日期：2026-08-30（Asia/Shanghai）。
- 仓库所有者决定：暂不重写 Git 历史，进入安全复查。
- 实时远端 `main`：`d310268771d2cbd38523810875b9cc16785e5551`。
- 本复查只检查公开 GitHub 安全元数据、当前跟踪文件、忽略规则、工作流结构、密文变量名称和部署配置声明方式。
- 未读取 `.env` 内容，未读取任何密文值，未连接数据库、Provider、Render 或 Vercel，也未修改 GitHub 设置。

## 2. 已确认的安全措施

| 控制项 | 结果 |
|---|---|
| `.env` Git 跟踪 | 当前 HEAD 不再跟踪 |
| `.env` 忽略规则 | `.gitignore` 第 1 行精确规则 `.env` |
| 本地 `.env` | 保留且被忽略；复查未读取其内容 |
| `.env.example` | 继续受跟踪，凭据字段为空或使用占位符；数据库用户名字段存在非空字面量但未记录其值 |
| 当前跟踪文件高置信秘密模式检查 | 未返回匹配文件；该检查不替代专业 Secret Scanning，也不覆盖历史 |
| Render 环境变量声明 | 发现的 5 个变量均使用平台密文声明方式，没有在报告中读取或记录值 |
| GitHub Actions 密文使用 | 两个工作流仅引用 AuraDB 相关 GitHub Secrets 名称，未检查或展示值 |
| 历史凭据 | 仓库所有者已确认实际使用的旧凭据均已轮换并撤销 |
| 协作者通知 | 已在 PR #1 发布停止跟踪和安全同步通知 |
| `main` 分支保护 | 仓库所有者已启用，GitHub 公共 API 已验证 `protected=true` |
| Secret Scanning | 仓库所有者已人工确认启用 |
| Push Protection | 仓库所有者已人工确认启用 |
| GitHub Actions 轮换后密文 | 仓库所有者已人工确认 |
| Render 轮换后密文 | 仓库所有者已人工确认 |

## 3. 主要发现

### 3.1 已缓解：`main` 分支保护

初次复查时 GitHub 公共分支元数据显示 `main` 的 `protected=false`。仓库所有者随后启用分支保护，复验结果为 `protected=true`。

建议在 GitHub 仓库设置中对 `main` 建立分支保护或 ruleset，至少要求：

- 所有变更通过 Pull Request；
- 至少 1 名审核者批准；
- 合并前解决全部 review conversation；
- 必需状态检查通过后才能合并；
- 禁止 force push 和分支删除；
- 需要时对管理员同样执行规则。

### 3.2 已缓解：两个定时工作流已手动禁用

初次复查时 GitHub API 报告两个工作流为 active。仓库所有者处理后，复验结果为：

- `Update GDELT route risk`：`disabled_manually`；
- `Update Open-Meteo route weather risk`：`disabled_manually`。

两个每小时计划触发器保留在工作流文件中，但工作流在 GitHub 平台层面已禁用，除非仓库所有者重新启用，否则不会按计划运行。未来重新启用前必须再次确认密文、数据库写入范围和运行审计。

### 3.3 已人工确认：Secret Scanning 与 Push Protection

仓库公共元数据中的 `security_and_analysis` 未提供状态，无法通过公开 API 独立复验。仓库所有者已在 GitHub Code security 设置页面人工确认 Secret Scanning 和 Push Protection 均已启用。

建议确认并启用：

- Secret Scanning；
- Push Protection；
- 针对支持 Provider 的秘密有效性检查；
- 安全警报通知接收人；
- 必要时的 Dependabot alerts 和 dependency graph。

### 3.4 中优先级：工作流没有显式最小化 `GITHUB_TOKEN` 权限

两个工作流均未声明顶层或 job 级 `permissions`。实际权限将依赖仓库或组织默认设置，不利于代码审计和最小权限控制。

建议在独立 PR 中加入显式只读默认权限，并只为确实需要写入 GitHub 的 job 增加最小写权限。同时在 GitHub Actions 设置中将默认 Workflow permissions 调整为只读。

### 3.5 已人工确认：部署和工作流轮换后密文

本复查没有读取真实值，也没有连接 Render 或 GitHub Secrets。仓库所有者已人工确认 GitHub Actions 和 Render 使用轮换后的密文。后续仍应持续检查：

- AuraDB 的四个 GitHub Actions Secrets 均已替换为轮换后的配置；
- Render 中的 AuraDB 变量使用轮换后的配置；
- CORS 变量不包含不必要的通配来源；
- 没有已弃用、重复或长期不使用的密文；
- 访问日志中没有旧凭据继续被使用的迹象。

## 4. 当前风险评级

| 风险 | 等级 | 理由 |
|---|---|---|
| 旧历史信息可见 | 中 | 公开历史仍保留旧 `.env`，但旧凭据已撤销 |
| `main` 分支保护 | 低（已缓解） | 已启用并通过公开 API 验证 |
| 定时工作流意外运行 | 低（已缓解） | 两个工作流均为 `disabled_manually` |
| GitHub Secret Scanning | 低（人工确认） | 仓库所有者已确认 Secret Scanning 与 Push Protection 启用 |
| 当前工作树再次提交 `.env` | 低 | 已停止跟踪且存在精确忽略规则 |
| 当前跟踪文件中的明显硬编码秘密 | 低但未完全排除 | 高置信模式文件名扫描无匹配，但不是完整扫描 |

## 5. 建议整改顺序

### P0：已完成

1. `main` 已启用分支保护。
2. Secret Scanning 和 Push Protection 已人工确认启用。
3. 两个定时工作流已手动禁用。
4. GitHub Actions 和 Render 的轮换后密文已人工确认。

### P1：近期处理

1. 为 GitHub Actions 增加显式最小 `permissions`。
2. 将仓库默认 Workflow permissions 设置为只读。
3. 为密文轮换、工作流失败和数据库认证失败建立不含秘密的告警。
4. 定期检查 `.env.example`，确保凭据字段保持为空或占位符。

### P2：持续治理

1. 定期复查可见 fork、旧分支、标签和部署产物。
2. 建立凭据生命周期、最小权限、轮换频率和撤销流程。
3. 在新提交进入远端前运行不会泄露匹配值的秘密检测。
4. 如果政策或审计要求变化，重新评估独立历史清理批次。

## 6. 人工确认清单

仓库所有者只需反馈状态，不得提供真实值：

```text
main 分支保护：已启用（API 已验证）
Secret Scanning：已启用（仓库所有者人工确认）
Push Protection：已启用（仓库所有者人工确认）
GDELT 定时工作流：已禁用（API 已验证）
Open-Meteo 定时工作流：已禁用（API 已验证）
GitHub Actions 轮换后密文：已确认
Render 轮换后密文：已确认
GitHub Actions 默认权限只读：已设置（仓库所有者人工确认）
```

## 7. 本轮未执行事项

- 未修改 GitHub、Render、Vercel、数据库或 Provider 设置。
- 未读取或输出任何密码、Token、URI、Cookie 或环境变量真实值。
- 未修改业务代码、测试、工作流、部署配置或锁文件。
- 未提交、推送、部署、重写历史或触发工作流。

## 8. 安全文档提交计划

### 8.1 固定边界

- `batchId`：`SEC-01-SECURITY-DOCS-COMMIT-01`
- 基础引用：`origin/main`
- 基础提交：`d310268771d2cbd38523810875b9cc16785e5551`
- 新分支：`security/sec-01-post-incident-review`
- 提交主题：`docs(security): 记录凭据事件后复查结论`
- 唯一允许新增的 Git 跟踪路径：
  - `docs/security/env-history-cleanup-assessment.md`
  - `docs/security/post-incident-security-review.md`
- 工作树文件删除、删除 Git 跟踪路径、业务代码修改：全部为 0。
- 不允许 push、PR、部署、数据库访问或历史重写。

### 8.2 规范 JSON

以下 JSON 使用 UTF-8，对象 key 按字典序排序，无多余空格且无结尾换行；数组顺序固定。

```json
{"allowedCommitPaths":["docs/security/env-history-cleanup-assessment.md","docs/security/post-incident-security-review.md"],"allowedIndexChanges":[{"path":"docs/security/env-history-cleanup-assessment.md","status":"A"},{"path":"docs/security/post-incident-security-review.md","status":"A"}],"baseBranch":"origin/main","baseCommit":"d310268771d2cbd38523810875b9cc16785e5551","batchId":"SEC-01-SECURITY-DOCS-COMMIT-01","branchName":"security/sec-01-post-incident-review","commitSubject":"docs(security): 记录凭据事件后复查结论","databaseAccessAllowed":false,"deploymentAllowed":false,"expectedAddedTrackedPaths":["docs/security/env-history-cleanup-assessment.md","docs/security/post-incident-security-review.md"],"expectedBusinessCodeChanges":0,"expectedCurrentUntrackedPaths":["docs/security/env-history-cleanup-assessment.md","docs/security/post-incident-security-review.md"],"expectedRemoteMain":"d310268771d2cbd38523810875b9cc16785e5551","expectedRemovedTrackedPaths":[],"expectedWorkingTreeFileDeletions":0,"historyRewriteAllowed":false,"prAllowed":false,"pushAllowed":false,"rollbackAfterCommit":"停止并请求单独授权；不得自动删除本地提交或分支","rollbackBeforeCommit":"停止并请求用户决定；不得自动切换、reset 或清理；必须保留本地 .env 和两份文档","verificationCommands":{"afterCommit":["git status --short","git show --stat --oneline --summary HEAD","git show --name-status --format= HEAD","git branch --show-current","git ls-files .env","git check-ignore -v --no-index .env","test -f .env"],"beforeCommit":["git status --short","git diff --cached --name-status","git diff --cached --name-only","git branch --show-current","git rev-parse HEAD","git ls-files .env","git check-ignore -v --no-index .env","test -f .env"]}}
```

- 计划 SHA-256：`260e52474d79587ef35a5b5ee23b63cce7febf57a151629e09637d9e301a3096`

### 8.3 下一轮精确授权文本

```text
我授权执行安全批次 SEC-01-SECURITY-DOCS-COMMIT-01，其规范计划 SHA-256 为 260e52474d79587ef35a5b5ee23b63cce7febf57a151629e09637d9e301a3096。

只允许刷新 origin/main 并确认其为 d310268771d2cbd38523810875b9cc16785e5551，然后从该基础提交创建并切换到 security/sec-01-post-incident-review。必须保留本地 .env 和两份未跟踪安全文档。

只允许将 docs/security/env-history-cleanup-assessment.md 和 docs/security/post-incident-security-review.md 加入 index，并创建主题为“docs(security): 记录凭据事件后复查结论”的一个本地提交。提交变化必须精确只有新增这两个路径；工作树文件删除、删除 Git 跟踪路径和业务代码修改均为 0。

不得读取或展示 .env 内容，不得修改 .env、.gitignore、.env.example、业务代码、测试、工作流、部署配置或锁文件。不得 push、创建 PR、部署、连接数据库或外部业务服务、重写历史。完成本地提交和路径级验证后立即停止并报告。
```
