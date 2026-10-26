#!/usr/bin/env bash
# =============================================================================
# Mini-Grid O&M Platform — 备份恢复演练 (Restore Drill)
# -----------------------------------------------------------------------------
# 用途：一键执行"备份 -> 恢复到临时库 -> 校验数据一致性 -> 输出耗时报告"。
#
# 这是岗位 JD 中"季度备份恢复演练，100% 成功"KPI 的可执行落地，
# 也是面试时最有说服力的动手证据：它证明恢复流程真的可用，而非纸面承诺。
#
# 用法：
#   本地（已有 docker compose 环境）:  make backup-restore-test
#   或直接:                          bash scripts/restore_drill.sh
# =============================================================================
set -euo pipefail

COMPOSE="${COMPOSE:-docker compose}"
TSDB_SERVICE="timescaledb"
DB_USER="${POSTGRES_USER:-ops}"
DB_NAME="${POSTGRES_DB:-minigrid}"
RESTORE_DB="${DB_NAME}_restore_test"

# 颜色
G='\033[0;32m'; R='\033[0;31m'; Y='\033[0;33m'; B='\033[0;36m'; N='\033[0m'

hr() { printf '%s\n' "------------------------------------------------------------"; }
info() { printf "${B}[信息]${N} %s\n" "$*"; }
ok()   { printf "${G}[通过]${N} %s\n" "$*"; }
warn() { printf "${Y}[警告]${N} %s\n" "$*"; }
err()  { printf "${R}[失败]${N} %s\n" "$*"; }

# 在 timescaledb 容器内执行 psql
psql_exec() {
    ${COMPOSE} exec -T -e PGPASSWORD="${POSTGRES_PASSWORD:-change_me_in_prod}" \
        "${TSDB_SERVICE}" psql -U "${DB_USER}" -d "$1" -tAc "$2"
}

echo
printf "${B}============================================================${N}\n"
printf "${B}   Mini-Grid O&M · 备份恢复演练                        ${N}\n"
printf "${B}   时间: %s${N}\n" "$(date '+%Y-%m-%d %H:%M:%S')"
printf "${B}============================================================${N}\n"
echo

# --- 前置检查 ----------------------------------------------------------------
info "检查数据库服务状态..."
if ! ${COMPOSE} ps "${TSDB_SERVICE}" | grep -q "Up"; then
    err "TimescaleDB 服务未运行，请先执行: make up"
    exit 1
fi
ok "TimescaleDB 运行中"

# --- 步骤 1：备份 ------------------------------------------------------------
hr
info "步骤 1/5：执行备份"
T0=$(date +%s)
BACKUP_FILE="/tmp/drill_${DB_NAME}_$(date +%Y%m%d_%H%M%S).dump"

${COMPOSE} exec -T -e PGPASSWORD="${POSTGRES_PASSWORD:-change_me_in_prod}" \
    "${TSDB_SERVICE}" pg_dump -U "${DB_USER}" -d "${DB_NAME}" -Fc -f "${BACKUP_FILE}"

T1=$(date +%s)
BACKUP_SEC=$((T1 - T0))
SIZE=$(${COMPOSE} exec -T "${TSDB_SERVICE}" du -h "${BACKUP_FILE}" | cut -f1)
ok "备份完成，耗时 ${BACKUP_SEC}s，大小 ${SIZE}"

# --- 步骤 2：记录源库数据量 --------------------------------------------------
hr
info "步骤 2/5：记录源库数据量"
SRC_COUNT=$(psql_exec "${DB_NAME}" "SELECT count(*) FROM telemetry;" | tr -d '[:space:]')
SRC_SITES=$(psql_exec "${DB_NAME}" "SELECT count(DISTINCT site_id) FROM telemetry;" | tr -d '[:space:]')
info "源库 telemetry 记录数: ${SRC_COUNT}，站点数: ${SRC_SITES}"

# --- 步骤 3：恢复到临时库 ----------------------------------------------------
hr
info "步骤 3/5：恢复到临时库 ${RESTORE_DB}"
T2=$(date +%s)

psql_exec "postgres" "DROP DATABASE IF EXISTS ${RESTORE_DB};" >/dev/null
psql_exec "postgres" "CREATE DATABASE ${RESTORE_DB};" >/dev/null

# TimescaleDB 官方恢复流程：预建扩展后，用 pre_restore / post_restore 包裹 pg_restore。
# 若跳过这两个调用，时序内部表（_timescaledb_internal）会因元数据冲突而无法正确
# 重挂到 hypertable，导致恢复后主表查询为 0 行 —— 这正是演练要提前暴露的坑。
psql_exec "${RESTORE_DB}" "CREATE EXTENSION IF NOT EXISTS timescaledb;" >/dev/null 2>&1 || true
psql_exec "${RESTORE_DB}" "SELECT timescaledb_pre_restore();" >/dev/null 2>&1 || true

${COMPOSE} exec -T -e PGPASSWORD="${POSTGRES_PASSWORD:-change_me_in_prod}" \
    "${TSDB_SERVICE}" pg_restore -U "${DB_USER}" -d "${RESTORE_DB}" \
    --no-owner --no-acl "${BACKUP_FILE}" 2>/dev/null || warn "恢复过程有非致命警告（时序元数据）"

psql_exec "${RESTORE_DB}" "SELECT timescaledb_post_restore();" >/dev/null 2>&1 || true

T3=$(date +%s)
RESTORE_SEC=$((T3 - T2))
ok "恢复完成，耗时 ${RESTORE_SEC}s"

# --- 步骤 4：校验一致性 ------------------------------------------------------
hr
info "步骤 4/5：校验数据一致性"
DST_COUNT=$(psql_exec "${RESTORE_DB}" "SELECT count(*) FROM telemetry;" | tr -d '[:space:]')
DST_SITES=$(psql_exec "${RESTORE_DB}" "SELECT count(DISTINCT site_id) FROM telemetry;" | tr -d '[:space:]')
info "恢复库 telemetry 记录数: ${DST_COUNT}，站点数: ${DST_SITES}"

RESULT="PASS"
if [ "${SRC_COUNT}" = "${DST_COUNT}" ] && [ "${SRC_SITES}" = "${DST_SITES}" ]; then
    ok "数据一致性校验通过（记录数与站点数完全一致）"
else
    err "数据不一致！源库 ${SRC_COUNT}/${SRC_SITES} vs 恢复库 ${DST_COUNT}/${DST_SITES}"
    RESULT="FAIL"
fi

# --- 步骤 5：清理 ------------------------------------------------------------
hr
info "步骤 5/5：清理临时资源"
psql_exec "postgres" "DROP DATABASE IF EXISTS ${RESTORE_DB};" >/dev/null
${COMPOSE} exec -T "${TSDB_SERVICE}" rm -f "${BACKUP_FILE}"
ok "临时库与备份文件已清理"

# --- 报告 --------------------------------------------------------------------
echo
printf "${B}============================================================${N}\n"
printf "${B}   演练报告 (Restore Drill Report)                      ${N}\n"
printf "${B}============================================================${N}\n"
printf "  备份耗时      : ${BACKUP_SEC} 秒\n"
printf "  恢复耗时      : ${RESTORE_SEC} 秒\n"
printf "  备份大小      : ${SIZE}\n"
printf "  数据记录数    : ${SRC_COUNT}\n"
printf "  站点数        : ${SRC_SITES}\n"
if [ "${RESULT}" = "PASS" ]; then
    printf "  演练结果      : ${G}PASS（数据零丢失）${N}\n"
else
    printf "  演练结果      : ${R}FAIL${N}\n"
fi
printf "${B}============================================================${N}\n"

[ "${RESULT}" = "PASS" ] && exit 0 || exit 1
