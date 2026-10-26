"""
Mini-Grid O&M Platform — 数据采集服务
================================================================================
角色：扮演 JD 中的"边缘采集服务 / 数据采集平台"。
      从数据源（模拟器 / Modbus / MQTT）读取逆变器遥测，写入 TimescaleDB。

设计目标（对齐"高度自动化、低维护"）：
  - 断线自动重连：数据库 / 数据源断开后自行恢复，不需要人工介入
  - 优雅退出：收到 SIGTERM 时安全关闭连接
  - 心跳文件：定期写入心跳，供 watchdog 监测并自愈
  - 数据源可插拔：通过 SOURCE_DRIVER 切换 simulated / modbus / mqtt
  - 结构化日志：便于集中采集与排查

用法：
    python collector.py
（通常由 docker compose 拉起，无需手工执行）
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import psycopg2
import psycopg2.extras

from sources import build_source

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
HEARTBEAT_FILE = Path(os.getenv("HEARTBEAT_FILE", "/var/run/collector/heartbeat"))
DB_CONF = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "minigrid"),
    "user": os.getenv("DB_USER", "ops"),
    "password": os.getenv("DB_PASSWORD", "change_me_in_prod"),
}
SITES = [s.strip() for s in os.getenv("SITES", "SITE-01,SITE-02,SITE-03").split(",") if s.strip()]
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL_SEC", "5"))
SOURCE_DRIVER = os.getenv("SOURCE_DRIVER", "simulator")
HEARTBEAT_INTERVAL = float(os.getenv("HEARTBEAT_INTERVAL_SEC", "15"))

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)-7s [collector] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("collector")

_running = True


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #
@dataclass
class Reading:
    """一条遥测读数，对应 telemetry 表的一行。"""
    ts: float            # epoch 秒
    site_id: str
    device_id: str
    power_kw: float
    energy_kwh: float
    voltage_v: float
    current_a: float
    temp_c: float
    soc_pct: float
    fault_code: str | None = None


# --------------------------------------------------------------------------- #
# 数据库连接（带重连）
# --------------------------------------------------------------------------- #
class Database:
    """TimescaleDB 写入封装，自动重连。"""

    INSERT_SQL = """
        INSERT INTO telemetry
            (ts, site_id, device_id, power_kw, energy_kwh,
             voltage_v, current_a, temp_c, soc_pct, fault_code)
        VALUES %s
    """

    def __init__(self, conf: dict):
        self.conf = conf
        self.conn = None
        self._connect()

    def _connect(self) -> None:
        while _running:
            try:
                self.conn = psycopg2.connect(**self.conf)
                self.conn.autocommit = False
                log.info("已连接数据库 %s:%s/%s",
                         self.conf["host"], self.conf["port"], self.conf["dbname"])
                return
            except psycopg2.OperationalError as exc:
                log.warning("数据库连接失败，5 秒后重试: %s", exc)
                time.sleep(5)

    def _ensure_conn(self) -> None:
        """连接失效时重连。"""
        if self.conn is None or self.conn.closed:
            log.warning("数据库连接已断开，正在重连...")
            self._connect()

    def write_batch(self, readings: list[Reading]) -> None:
        if not readings:
            return
        rows = [
            (time.strftime("%Y-%m-%d %H:%M:%S%z", time.localtime(r.ts)),
             r.site_id, r.device_id, r.power_kw, r.energy_kwh,
             r.voltage_v, r.current_a, r.temp_c, r.soc_pct, r.fault_code)
            for r in readings
        ]
        for attempt in range(3):
            try:
                self._ensure_conn()
                with self.conn.cursor() as cur:
                    psycopg2.extras.execute_values(cur, self.INSERT_SQL, rows)
                self.conn.commit()
                return
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
                log.warning("写入失败(第 %d 次)，重连后重试: %s", attempt + 1, exc)
                try:
                    if self.conn:
                        self.conn.close()
                except Exception:  # noqa: BLE001
                    pass
                self.conn = None
                time.sleep(2)
        log.error("写入失败，已重试 3 次，本批 %d 条数据将被丢弃", len(rows))

    def close(self) -> None:
        if self.conn and not self.conn.closed:
            self.conn.close()
            log.info("数据库连接已关闭")


# --------------------------------------------------------------------------- #
# 心跳
# --------------------------------------------------------------------------- #
def write_heartbeat() -> None:
    """写入心跳时间戳，供 watchdog 判定采集服务是否健康。"""
    try:
        HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_FILE.write_text(str(time.time()), encoding="utf-8")
    except OSError as exc:
        log.warning("写心跳失败: %s", exc)


# --------------------------------------------------------------------------- #
# 主循环
# --------------------------------------------------------------------------- #
def run(source, db: Database) -> None:
    log.info("采集服务启动 | driver=%s | sites=%s | interval=%.0fs",
             SOURCE_DRIVER, ",".join(SITES), POLL_INTERVAL)
    last_heartbeat = 0.0
    total_written = 0

    while _running:
        cycle_start = time.time()
        try:
            readings: Iterable[Reading] = source.read()
            batch = list(readings)
            db.write_batch(batch)
            total_written += len(batch)
        except Exception as exc:  # noqa: BLE001 — 保证循环永不因单次异常退出
            log.exception("采集周期异常，将在下个周期继续: %s", exc)

        # 心跳
        if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
            write_heartbeat()
            last_heartbeat = time.time()
            log.info("已累计写入 %d 条读数", total_written)

        # 固定节拍
        elapsed = time.time() - cycle_start
        time.sleep(max(0.0, POLL_INTERVAL - elapsed))

    log.info("采集服务收到退出信号，正在关闭...")
    db.close()
    log.info("采集服务已停止，累计写入 %d 条读数", total_written)


def _handle_signal(signum, _frame) -> None:
    global _running
    log.info("收到信号 %s，准备优雅退出", signum)
    _running = False


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    db = Database(DB_CONF)
    source = build_source(SOURCE_DRIVER, SITES, POLL_INTERVAL)
    write_heartbeat()
    run(source, db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
