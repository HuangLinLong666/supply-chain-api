# 阶段 6 地理数据迁移报告

- 模式：`execute`
- 状态：`completed`
- 生成时间：`2026-07-26T07:16:27.731820+00:00`
- 完整 JSON：`artifacts/geospatial_migration_20260726T071627Z.json`
- 节点删除：`0`；关系删除：`0`。旧关系仅在本集成范围内软停用。

## 计划统计

- 地点属性更新：0。
- 路段几何/可行性更新：0。
- 风险区更新：0。
- 目标 `PASSES_THROUGH`：65。
- `PASSES_THROUGH` 写入：0；软停用：4。

## 置信度边界

- `geometry_intersection` 表示路线几何与风险区几何真实相交，但几何本身仍可能是估算。
- `inferred_from_endpoints` 仅是低置信度回退，不能当作实际经过证明。
- searoute-py、Great Circle 与小比例尺 Natural Earth 均不可用于导航。
- 跨洋公路/铁路会标记 `invalid_cross_ocean`，不会进入推荐图。

## 执行命令

```bash
python scripts/migrate_geospatial_data.py --dry-run
python scripts/migrate_geospatial_data.py --execute --confirm APPLY_GEOSPATIAL_STAGE6 --enable-osrm
```
