-- =============================================================================
-- Mini-Grid O&M Platform — 数据库初始化与时序建模
-- -----------------------------------------------------------------------------
-- 本脚本在容器首次启动时自动执行（挂载到 /docker-entrypoint-initdb.d/）。
-- 完成三件事：
--   1. 启用 TimescaleDB 扩展，建立 hypertable（自动按时间分区）
--   2. 建立连续聚合（continuous aggregate）实现自动降采样，看板查询提速
--   3. 配置压缩与保留策略，让存储可控、长期无人干预
--
-- 对应岗位职责：监视与数据采集平台的数据库建模与数据质量保障。
-- =============================================================================

-- 1. 时序扩展 ----------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- =============================================================================
-- 2. 原始遥测表：telemetry
--    每次采集写入一行，按 ts 时间分区
-- =============================================================================
CREATE TABLE IF NOT EXISTS telemetry (
    ts          TIMESTAMPTZ      NOT NULL,          -- 采集时间
    site_id     TEXT             NOT NULL,          -- 站点标识 (SITE-01...)
    device_id   TEXT             NOT NULL DEFAULT 'INV-01', -- 设备标识(逆变器/电表)
    power_kw    DOUBLE PRECISION,                   -- 实时功率 (kW)
    energy_kwh  DOUBLE PRECISION,                   -- 累计发电量 (kWh)
    voltage_v   DOUBLE PRECISION,                   -- 电压 (V)
    current_a   DOUBLE PRECISION,                   -- 电流 (A)
    temp_c      DOUBLE PRECISION,                   -- 设备温度 (℃)
    soc_pct     DOUBLE PRECISION,                   -- 电池 SOC (%)
    fault_code  TEXT                                -- 故障码，正常为 NULL
);

-- 转为 hypertable，按 ts 以 1 天为 chunk 分区
SELECT create_hypertable(
    'telemetry', 'ts',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- 常用查询索引：按站点+时间倒序（看板默认视角）
CREATE INDEX IF NOT EXISTS idx_telemetry_site_ts
    ON telemetry (site_id, ts DESC);

-- =============================================================================
-- 3. 压缩策略
--    将 7 天前的旧数据按 site_id 分段压缩，大幅降低存储占用、
--    同时保持可查询。长期无人值守运行的关键。
-- =============================================================================
ALTER TABLE telemetry SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'site_id, device_id',
    timescaledb.compress_orderby   = 'ts DESC'
);

-- 7 天后自动压缩（若已存在则忽略错误）
SELECT add_compression_policy('telemetry', INTERVAL '7 days', if_not_exists => TRUE);

-- =============================================================================
-- 4. 连续聚合：小时级降采样
--    看板查询"近 30 天趋势"时直接读聚合表，避免全表扫描。
--    对应岗位 KPI：看板响应、数据质量。
-- =============================================================================
CREATE MATERIALIZED VIEW IF NOT EXISTS telemetry_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', ts) AS bucket,
    site_id,
    AVG(power_kw)             AS avg_power_kw,
    MAX(power_kw)             AS max_power_kw,
    MIN(power_kw)             AS min_power_kw,
    MAX(energy_kwh) - MIN(energy_kwh) AS energy_delta_kwh,
    AVG(voltage_v)            AS avg_voltage_v,
    AVG(temp_c)               AS avg_temp_c,
    COUNT(*)                  AS sample_count
FROM telemetry
GROUP BY bucket, site_id
WITH NO DATA;

-- 自动刷新：每 30 分钟刷新一次，覆盖过去 3 小时的数据
SELECT add_continuous_aggregate_policy('telemetry_hourly',
    start_offset => INTERVAL '3 hours',
    end_offset   => INTERVAL '30 minutes',
    schedule_interval => INTERVAL '30 minutes',
    if_not_exists => TRUE
);

-- =============================================================================
-- 5. 连续聚合：日级降采样（用于月度/年度报表）
-- =============================================================================
CREATE MATERIALIZED VIEW IF NOT EXISTS telemetry_daily
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', ts)  AS bucket,
    site_id,
    AVG(power_kw)             AS avg_power_kw,
    MAX(power_kw)             AS peak_power_kw,
    MAX(energy_kwh) - MIN(energy_kwh) AS energy_delta_kwh,
    COUNT(*)                  AS sample_count
FROM telemetry
GROUP BY bucket, site_id
WITH NO DATA;

SELECT add_continuous_aggregate_policy('telemetry_daily',
    start_offset => INTERVAL '7 days',
    end_offset   => INTERVAL '1 minute',
    schedule_interval => INTERVAL '15 minutes',
    if_not_exists => TRUE
);

-- =============================================================================
-- 6. 保留策略（可选，默认保留 400 天原始数据）
--    超过该期限的原始数据自动删除；小时/日聚合长期保留。
--    这样存储不会无限增长，符合"长期无人干预"的设计目标。
-- =============================================================================
SELECT add_retention_policy('telemetry', INTERVAL '400 days', if_not_exists => TRUE);

-- =============================================================================
-- 7. 告警事件表：记录由告警/人工确认的运维事件
--    与 Odoo Helpdesk 工单相互对应，形成"告警 -> 工单"闭环的本地留痕。
-- =============================================================================
CREATE TABLE IF NOT EXISTS alarm_events (
    id           BIGSERIAL PRIMARY KEY,
    raised_at    TIMESTAMPTZ      NOT NULL DEFAULT now(),
    site_id      TEXT,
    rule_name    TEXT             NOT NULL,       -- 触发规则名
    severity     TEXT             NOT NULL DEFAULT 'P2', -- P1 / P2 / P3
    description  TEXT,
    acknowledged BOOLEAN          NOT NULL DEFAULT FALSE,
    ack_at       TIMESTAMPTZ,
    ticket_ref   TEXT                              -- Odoo 工单号
);

CREATE INDEX IF NOT EXISTS idx_alarm_raised_at ON alarm_events (raised_at DESC);

-- =============================================================================
-- 8. 库存对账视图：对应 JD "维护备件库存数据的准确性" 的本地报表视角
--    实际库存主数据在 Odoo，此处提供对账用的快照结构。
-- =============================================================================
CREATE TABLE IF NOT EXISTS spare_parts_snapshot (
    snapshot_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    part_code    TEXT        NOT NULL,
    part_name    TEXT,
    qty_system   INTEGER,               -- 系统账面数量
    qty_physical INTEGER,               -- 实物盘点数量
    site_id      TEXT
);

-- 对账差异视图：任何 qty_system != qty_physical 的记录都会被列出
CREATE OR REPLACE VIEW spare_parts_variance AS
SELECT snapshot_at, part_code, part_name, site_id,
       qty_system, qty_physical,
       (COALESCE(qty_system,0) - COALESCE(qty_physical,0)) AS variance
FROM spare_parts_snapshot
WHERE COALESCE(qty_system,0) <> COALESCE(qty_physical,0);

-- =============================================================================
-- 完成提示
-- =============================================================================
DO $$
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE ' Mini-Grid O&M 数据库初始化完成';
    RAISE NOTICE ' hypertable: telemetry';
    RAISE NOTICE ' 连续聚合: telemetry_hourly / telemetry_daily';
    RAISE NOTICE ' 压缩策略: 7 天前数据自动压缩';
    RAISE NOTICE ' 保留策略: 原始数据保留 400 天';
    RAISE NOTICE '========================================';
END $$;
