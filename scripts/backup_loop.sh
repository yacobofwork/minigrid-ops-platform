#!/usr/bin/env bash
# =============================================================================
# Mini-Grid O&M Platform — 定时备份循环
# -----------------------------------------------------------------------------
# 由 docker compose 的 backup 服务常驻运行：
#   1. 每 BACKUP_INTERVAL_HOURS 小时对 TimescaleDB 做一次 pg_dump
#   2. 备份文件写入 /backups，并按 BACKUP_RETENTION_DAYS 轮转清理
#   3. 每次结果记入 backup.log，形成可审计记录
#
# 对应岗位职责：备份与恢复（R/A 责任）；季度恢复演练 KPI 的自动备份底座。
# =============================================================================
set -euo pipefail

DB_HOST="${DB_HOST:-timescaledb}"
DB_NAME="${DB_NAME:-minigrid}"
DB_USER="${DB_USER:-ops}"
INTERVAL_HOURS="${BACKUP_INTERVAL_HOURS:-6}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
BACKUP_DIR="/backups"
LOG_FILE="${BACKUP_DIR}/backup.log"

mkdir -p "${BACKUP_DIR}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

do_backup() {
    local stamp file size status
    stamp="$(date '+%Y%m%d_%H%M%S')"
    file="${BACKUP_DIR}/${DB_NAME}_${stamp}.dump"

    log "开始备份 -> ${file}"
    status="success"
    if pg_dump -h "${DB_HOST}" -U "${DB_USER}" -d "${DB_NAME}" -Fc -f "${file}"; then
        size="$(du -h "${file}" | cut -f1)"
        log "备份成功（大小 ${size}）"
    else
        status="failed"
        log "备份失败！"
    fi

    # 备份事件登记入库：供 Grafana「平台健康」看板展示备份状态与历史
    psql -h "${DB_HOST}" -U "${DB_USER}" -d "${DB_NAME}" -q <<SQL 2>/dev/null || log "警告: 备份事件登记数据库失败"
CREATE TABLE IF NOT EXISTS backup_log (
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    file_name   TEXT,
    file_size   TEXT,
    status      TEXT NOT NULL
);
INSERT INTO backup_log (file_name, file_size, status)
VALUES ('$(basename "${file}")', '${size:-NA}', '${status}');
SQL

    # 轮转：删除超过保留期的备份
    local deleted
    deleted="$(find "${BACKUP_DIR}" -name "${DB_NAME}_*.dump" \
        -type f -mtime "+${RETENTION_DAYS}" -print -delete | wc -l)"
    [ "${deleted}" -gt 0 ] && log "已清理 ${deleted} 个过期备份（>${RETENTION_DAYS} 天）"

    local total
    total="$(find "${BACKUP_DIR}" -name "${DB_NAME}_*.dump" -type f | wc -l)"
    log "当前保留备份数：${total}"
}

log "============================================================"
log "备份服务启动 | 间隔=${INTERVAL_HOURS}h | 保留=${RETENTION_DAYS}天"
log "============================================================"

# 启动后先等待数据库就绪，再执行首次备份
sleep 30

while true; do
    do_backup || true
    log "休眠 ${INTERVAL_HOURS} 小时后进行下一次备份"
    sleep "$(( INTERVAL_HOURS * 3600 ))"
done
