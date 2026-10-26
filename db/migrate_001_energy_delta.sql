-- =============================================================================
-- 迁移 001：修复电量聚合口径（gauge vs counter 混淆）
-- 背景：energy_kwh 是累计计数器（只增不减），对它 SUM 会得到天文数字。
-- 修复：SUM(energy_kwh) -> MAX(energy_kwh) - MIN(energy_kwh)（每窗口增量）
-- 适用：已有 telemetry_hourly / telemetry_daily 的存量数据库（init 脚本不重跑）。
-- 执行方式：docker exec -it mog-timescaledb psql -U postgres -d minigrid -f - < 本文件
--   或在 psql 里逐段执行。
-- =============================================================================

-- 1) 重建小时聚合 ---------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS telemetry_hourly;
CREATE MATERIALIZED VIEW telemetry_hourly
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

SELECT remove_continuous_aggregate_policy('telemetry_hourly', if_exists => TRUE);
SELECT add_continuous_aggregate_policy('telemetry_hourly',
    start_offset => INTERVAL '3 hours',
    end_offset   => INTERVAL '30 minutes',
    schedule_interval => INTERVAL '30 minutes',
    if_not_exists => TRUE
);

-- 2) 重建日级聚合 ---------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS telemetry_daily;
CREATE MATERIALIZED VIEW telemetry_daily
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

SELECT remove_continuous_aggregate_policy('telemetry_daily', if_exists => TRUE);
SELECT add_continuous_aggregate_policy('telemetry_daily',
    start_offset => INTERVAL '7 days',
    end_offset   => INTERVAL '1 minute',
    schedule_interval => INTERVAL '15 minutes',
    if_not_exists => TRUE
);

-- 3) 立即回填历史数据 -----------------------------------------------------------
CALL refresh_continuous_aggregate('telemetry_hourly', NULL, NULL);
CALL refresh_continuous_aggregate('telemetry_daily', NULL, NULL);

-- 4) 验证（结果应为个位/十位数 kWh 量级，而非 MWh）：
-- SELECT * FROM telemetry_hourly ORDER BY bucket DESC LIMIT 5;
