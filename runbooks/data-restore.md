# Runbook · 数据恢复

> **适用场景**：数据库损坏、误删数据、需要回滚到某个备份点。
> **对应职责**：JD 中"备份与恢复"的 R/A 责任，季度恢复演练的执行依据。

---

## 原则

1. **先保护现场**：恢复前确认问题范围，避免把唯一副本也破坏。
2. **先演练后操作**：用 `make backup-restore-test` 验证流程可用，再动生产。
3. **可回溯**：所有操作记入事件时间线。

---

## 一、快速验证备份链路（推荐先做）

```bash
make backup-restore-test
```

脚本会执行：备份 → 恢复到临时库 → 校验数据一致性 → 清理 → 输出报告。

**预期输出**：

```
  备份耗时      : X 秒
  恢复耗时      : Y 秒
  数据记录数    : N
  站点数        : M
  演练结果      : PASS（数据零丢失）
```

> ⚠️ **关键坑位**：TimescaleDB 的恢复必须用官方流程包裹：
> ```sql
> SELECT timescaledb_pre_restore();   -- 恢复前
> -- ... pg_restore ...
> SELECT timescaledb_post_restore();  -- 恢复后
> ```
> 跳过这两步会导致时序内部表无法重挂到 hypertable，恢复后主表查询返回 0 行。
> 本项目 `scripts/restore_drill.sh` 已内置正确处理。

---

## 二、查看可用备份

```bash
# 列出备份卷内的备份文件
docker compose exec backup ls -lh /backups/

# 查看备份日志（含每次备份的大小与结果）
docker compose exec backup cat /backups/backup.log
```

---

## 三、执行真实恢复

### 步骤 1：停止写入（避免恢复期间数据竞争）

```bash
docker compose stop collector
```

### 步骤 2：备份当前（可能已损坏的）库，作为最后手段

```bash
docker compose exec -T -e PGPASSWORD=change_me_in_prod timescaledb \
  pg_dump -U ops -d minigrid -Fc -f /tmp/pre_restore_safety.dump
docker cp mog-timescaledb:/tmp/pre_restore_safety.dump ./pre_restore_safety.dump
```

### 步骤 3：恢复到干净数据库

```bash
BACKUP=/backups/minigrid_YYYYMMDD_HHMMSS.dump    # 选择目标备份

docker compose exec -T -e PGPASSWORD=change_me_in_prod timescaledb bash -c "
  set -e
  psql -U ops -d postgres -c 'DROP DATABASE IF EXISTS minigrid;'
  psql -U ops -d postgres -c 'CREATE DATABASE minigrid;'
  psql -U ops -d minigrid -c 'CREATE EXTENSION IF NOT EXISTS timescaledb;'
  psql -U ops -d minigrid -c 'SELECT timescaledb_pre_restore();'
  pg_restore -U ops -d minigrid --no-owner --no-acl ${BACKUP}
  psql -U ops -d minigrid -c 'SELECT timescaledb_post_restore();'
"
```

### 步骤 4：校验

```bash
docker compose exec -T timescaledb psql -U ops -d minigrid -c "
SELECT site_id, count(*), min(ts), max(ts) FROM telemetry GROUP BY site_id;"
```

确认时间范围覆盖到故障前的最新数据。

### 步骤 5：恢复写入

```bash
docker compose start collector
docker compose logs -f collector      # 确认数据重新写入
```

---

## 四、恢复验证清单

- [ ] 数据条数与备份时点一致
- [ ] 时间范围无断档（或断档范围与故障窗口吻合）
- [ ] 连续聚合视图已恢复（`telemetry_hourly` / `telemetry_daily`）
- [ ] Grafana 看板正常显示
- [ ] 采集服务恢复写入

---

## 五、事后动作

1. 记录恢复点、丢失的数据窗口（若有）
2. 计算 RPO（数据丢失量）与 RTO（恢复耗时），与目标对比
3. 若 RPO 不满足要求，调整 `BACKUP_INTERVAL_HOURS`
4. 更新本 Runbook
