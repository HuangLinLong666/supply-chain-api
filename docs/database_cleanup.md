# Neo4j 数据安全清理指南

> 适用对象：第一次维护 AuraDB 的项目成员  
> 核心原则：先审计、再 dry-run、保存备份、限定范围、最后才执行

本项目的数据库包含历史模拟数据、参考骨架、实时 GDELT/Open-Meteo 数据和未来 AIS 数据。不能使用下面这种全库删除：

```cypher
MATCH (node) DETACH DELETE node;
```

它会同时删除实时证据、路线、供应商和约束关联，无法安全恢复。

## 1. 阶段 2 实际完成了什么

阶段 2 新增 `scripts/cleanup_synthetic_data.py`，用于识别明确带合成、样例或测试来源标记的数据。

已经生成三次只读报告：

```text
artifacts/database_cleanup_report_20260725T122151Z.md
artifacts/database_cleanup_report_20260726T020430Z.md
artifacts/database_cleanup_report_20260726T020527Z.md
```

最近一次计划结果：

| 项目 | 数量 |
|---|---:|
| 匹配节点 | 2,184 |
| 硬保护节点 | 1 |
| 因关系边界或依赖传播被阻止 | 2,181 |
| 最终可删除候选 | 2 |
| 实际删除 | 0 |

阶段 2 只完成安全识别和备份设计，没有执行删除。这是刻意的：大量历史路线虽标记为 synthetic，但仍连接实时风险区、天气或旧接口依赖，直接删除会破坏当前业务。

## 2. 哪些数据受到硬保护

以下节点永不进入通用合成数据删除集合：

```text
NewsRiskEvent
NewsRiskZone
WeatherRiskSnapshot
Vessel
VesselObservation
PortTrafficSnapshot
Shipment
```

此外，来源明确属于下列类型的数据受到保护：

```text
GDELT
Open-Meteo
AISStream.io
官方注册表
官方班期
已审核数据
```

与受保护节点相连的候选也可能被阻止。脚本会沿候选子图传播阻止状态，避免只删一个风险或成本子节点后留下断裂路线。

## 3. `dry-run` 是什么

`dry-run` 表示：

- 读取数据库；
- 计算匹配、保护和可删除集合；
- 生成 JSON 备份与 Markdown 报告；
- 不新增、不修改、不删除 Neo4j 数据。

默认命令就是安全预览：

```bash
cd "/Users/vegeta/全球供应链管理/supply-chain-api"
source .venv/bin/activate
python scripts/cleanup_synthetic_data.py --dry-run
```

即使省略 `--dry-run`，脚本当前也默认采用预览模式；文档仍建议显式写出参数，避免误解。

## 4. 推荐的安全清理步骤

### 第 1 步：确认连接的是哪个数据库

```bash
python scripts/verify_aura_connection.py
```

检查输出的数据库名。`AURA_NEO4J_DATABASE` 为空时，驱动使用服务端默认数据库；如果你的 Aura 实例没有名为 `neo4j` 的数据库，不要手工强制填写 `neo4j`。

### 第 2 步：生成当前只读审计

```bash
python scripts/audit_current_backend.py
```

重点查看：

```text
docs/current_backend_audit.md
artifacts/database_inventory.json
```

### 第 3 步：按最小范围预览

不要第一次就用默认来源列表执行删除。先按来源、标签或 integration ID 缩小范围：

```bash
python scripts/cleanup_synthetic_data.py --dry-run \
  --source standard_skeleton_reference \
  --label RouteSegment
```

或：

```bash
python scripts/cleanup_synthetic_data.py --dry-run \
  --integration-id your-integration-id
```

`--source`、`--label`、`--integration-id` 可以重复填写，也可以使用逗号分隔值。不同类别之间按 AND 组合，同一类别内部按 OR 组合。

### 第 4 步：逐项审查报告

在生成的 `artifacts/database_cleanup_report_*.md` 中确认：

1. 候选的业务 ID 和标签确实属于要删除的模块；
2. `protected` 与 `blocked` 数量合理；
3. 没有 GDELT、Open-Meteo、AIS 或 Shipment；
4. 没有前端当前使用的 Route、RouteSegment、Supplier 或 TransportLocation；
5. `deletable` 数量没有超过预期。

### 第 5 步：保存备份到仓库外

报告对应的机器可读备份位于：

```text
artifacts/database_cleanup_backup_*.json
```

在真正执行前，把备份复制到仓库外的受控位置。当前项目没有自动恢复脚本；JSON 备份主要用于审计和人工重建，不等同于 AuraDB 完整备份。

生产环境还应先使用 Neo4j Aura 提供的备份/快照能力，并验证恢复流程。

### 第 6 步：使用双重确认执行

只有确认报告正确后才执行：

```bash
python scripts/cleanup_synthetic_data.py --execute \
  --source standard_skeleton_reference \
  --label RouteSegment \
  --confirm DELETE_SYNTHETIC_ONLY \
  --max-delete 100
```

保护措施：

- 必须同时传 `--execute`；
- 必须提供准确确认口令 `DELETE_SYNTHETIC_ONLY`；
- 超过 `--max-delete` 立即拒绝执行；
- 默认不允许删除候选与保留节点之间的边界关系；
- 实时数据保护边界不能被 `--allow-boundary-links` 放宽。

### 第 7 步：执行后立即复核

```bash
python scripts/audit_current_backend.py
python scripts/cleanup_synthetic_data.py --dry-run \
  --source standard_skeleton_reference \
  --label RouteSegment
pytest -q
```

重复 dry-run 应不再出现已经删除的候选，同时实时节点数量不应下降。

## 5. 阶段 4 的风险清理不是通用清理

阶段 4 使用独立脚本：

```bash
python scripts/recalculate_provider_risk.py --dry-run
```

它只删除同时满足全部条件的旧 `RiskFactor`：

- 没有 Provider；
- 没有来源 URL；
- 没有 Evidence；
- 未审核；
- 来源明确是 synthetic、sample、mock、derived 等；
- 没有连接受保护实时数据。

已执行结果：

| 项目 | 迁移前 | 迁移后 |
|---|---:|---:|
| 节点 | 15,236 | 14,624 |
| 关系 | 23,772 | 22,497 |
| `RiskFactor` | 612 | 0 |
| `RouteSegment` | 613 | 613 |

实际删除 612 个无来源 `RiskFactor` 和 1,275 条附属关系；没有删除路线、路段、供应商、地点或实时 API 数据。迁移备份和报告：

```text
artifacts/risk_cleanup_backup_20260726T030321Z.json
artifacts/risk_recalculation_report_20260726T030321Z.md
```

再次 dry-run 已得到零变更，证明该迁移幂等。

如果未来确实要再次执行，仍必须使用确认口令：

```bash
python scripts/recalculate_provider_risk.py \
  --execute \
  --confirm CLEAN_AND_RECALCULATE_PROVIDER_RISK_V1
```

## 6. 软删除与硬删除怎样选择

优先软删除：

```text
active=false
status=deprecated
excluded=true
feasibility_status=invalid_cross_ocean
```

适合以下情况：

- 记录仍需审计；
- 旧前端或历史快照可能引用该 ID；
- 只是当前不应进入推荐；
- 以后可能补充真实 Provider 后恢复。

只有同时满足“明确无价值、无受保护关系、已有备份、已人工确认”时才考虑硬删除。

## 7. 常见危险操作

不要执行：

```cypher
MATCH (node) DETACH DELETE node;
```

不要为了修唯一约束直接删除已有港口。地点采集应使用稳定主键 `MERGE`，并通过规范代码和别名处理 `CNSHA`、`CNSHG` 等兼容问题。

不要仅按 `source CONTAINS 'synthetic'` 执行手写删除。某些 synthetic 路线仍可能连接实时新闻和天气证据，必须让保护关系检查参与决策。

不要把 `--allow-boundary-links` 当作跳过全部保护。它只放宽普通边界，不能放宽实时数据保护；初学者通常不应使用该参数。

## 8. 发生误删时怎么办

1. 立即停止所有 GitHub Actions、AIS worker 和写入脚本。
2. 保存执行日志、清理报告和 JSON 备份，不要覆盖。
3. 优先从 AuraDB 官方备份/快照恢复到新实例。
4. 用只读审计比较恢复前后的标签、关系、约束和业务 ID。
5. 如果只能根据 JSON 重建，先写专门的恢复脚本并在测试数据库验证；不要在生产库手工批量 `CREATE`。
6. 恢复时使用 `MERGE` 和稳定主键，避免产生第二套重复实体。

## 9. 最小安全检查清单

- [ ] 已确认 `.env` 指向正确 AuraDB 和数据库名。
- [ ] 已运行 `scripts/audit_current_backend.py`。
- [ ] 已使用最小 source/label/integration ID 执行 dry-run。
- [ ] 已人工查看每个可删除候选。
- [ ] 已确认实时节点和前端依赖不在候选中。
- [ ] 已保存 JSON 与 AuraDB 备份。
- [ ] 已设置合理 `--max-delete`。
- [ ] 已记录确认人、时间、命令和报告文件。
- [ ] 执行后已重新审计并运行测试。
