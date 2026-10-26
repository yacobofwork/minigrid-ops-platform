"""
数据源抽象层
================================================================================
把"从哪读数据"与"怎么写数据库"解耦。默认使用 simulator（模拟器），
以便在没有真实设备时也能完整演示整条链路。

真实环境接入时，只需实现新的 Source 子类并在 build_source 中注册，
collector 主逻辑无需改动。这对应岗位中"采集平台对不同逆变器/电表厂商的适配"。
"""
from __future__ import annotations

import logging
import math
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

log = logging.getLogger("collector.sources")


@dataclass
class Reading:
    ts: float
    site_id: str
    device_id: str
    power_kw: float
    energy_kwh: float
    voltage_v: float
    current_a: float
    temp_c: float
    soc_pct: float
    fault_code: str | None = None


class Source(ABC):
    """数据源基类。"""

    def __init__(self, sites: list[str], poll_interval: float):
        self.sites = sites
        self.poll_interval = poll_interval

    @abstractmethod
    def read(self) -> list[Reading]:
        """返回一个采集周期内的所有站点读数。"""
        raise NotImplementedError


class SimulatorSource(Source):
    """
    模拟数据源 —— 按光伏电站现场真实设备构成生成数据：
    每个站点 4 类设备（对应微电站现场关注对象）：
      PV-01  光伏组件串      白天发电，温度与辐照正相关
      BAT-01 储能电池组      SOC 缓慢变化；白天充电、夜间放电
      GEN-01 柴油发电机      夜间或电池低电量时启动补载，否则停机
      TR-01  变压器          负责负载侧：电压 400V、电流随负载
    物理逻辑：负载 = 光伏优先供电 -> 电池充/放电平衡 -> 柴发兜底。
    极低概率注入故障码，便于演示告警链路。
    """

    DEVICES = ("PV-01", "BAT-01", "GEN-01", "TR-01")

    def __init__(self, sites: list[str], poll_interval: float):
        super().__init__(sites, poll_interval)
        self._start = time.time()
        self._energy: dict[str, float] = {f"{s}/PV-01": 0.0 for s in sites}
        self._soc: dict[str, float] = {s: random.uniform(55, 85) for s in sites}
        self._load: dict[str, float] = {s: random.uniform(8, 14) for s in sites}

    def _solar_factor(self, ts: float) -> float:
        """基于当地时刻的日照因子，0(夜间) ~ 1(正午)。"""
        hour = time.localtime(ts).tm_hour + time.localtime(ts).tm_min / 60.0
        if hour < 6 or hour > 18:
            return 0.0
        return max(0.0, math.sin(math.pi * (hour - 6) / 12.0))

    def _load_factor(self, ts: float) -> float:
        """负载日曲线：白天平稳，19-22 点晚高峰。"""
        hour = time.localtime(ts).tm_hour
        peak = 1.6 if 19 <= hour <= 22 else (1.0 if 6 <= hour <= 18 else 0.6)
        return peak

    def read(self) -> list[Reading]:
        now = time.time()
        readings: list[Reading] = []
        for site in self.sites:
            solar = self._solar_factor(now)
            capacity = 60.0 + 20.0 * (hash(site) % 3)      # 装机容量系数
            load = self._load[site] * self._load_factor(now) + random.uniform(-1, 1)
            load = max(1.0, load)

            fault = None
            if random.random() < 0.002:
                fault = "E-TEMP-001"
                log.warning("站点 %s 注入模拟故障码 %s", site, fault)

            # ---- PV-01 光伏组件 -------------------------------------------------
            pv_power = max(0.0, capacity * solar + random.uniform(-2, 2))
            pv_temp = 25.0 + 32.0 * solar + random.uniform(-2, 2)
            if fault:
                pv_temp += 15
            self._energy[f"{site}/PV-01"] += pv_power * self.poll_interval / 3600.0
            readings.append(Reading(
                ts=now, site_id=site, device_id="PV-01",
                power_kw=round(pv_power, 3),
                energy_kwh=round(self._energy[f"{site}/PV-01"], 3),
                voltage_v=round(600.0 + random.uniform(-10, 10), 1),   # 直流母线
                current_a=round(pv_power * 1000.0 / 600.0, 2),
                temp_c=round(pv_temp, 2), soc_pct=round(self._soc[site], 1),
                fault_code=fault,
            ))

            # ---- BAT-01 储能电池：充放电平衡 ------------------------------------
            surplus = pv_power - load                      # 正=多余给电池充电
            if surplus > 0.5:
                bat_power = min(surplus, 25.0)             # 充电上限 25kW
                soc_delta = 0.9
            elif surplus < -0.5:
                bat_power = max(surplus, -30.0)            # 放电上限 30kW
                soc_delta = -0.9
            else:
                bat_power, soc_delta = 0.0, 0.0
            if surplus < 0 and self._soc[site] < 25:
                bat_power = 0.0                            # 电量过低保护，转由柴发
            self._soc[site] = min(100.0, max(8.0, self._soc[site] + soc_delta * self.poll_interval / 60.0))
            readings.append(Reading(
                ts=now, site_id=site, device_id="BAT-01",
                power_kw=round(bat_power, 3), energy_kwh=0.0,
                voltage_v=round(48.0 + random.uniform(-0.5, 0.5), 1),
                current_a=round(bat_power * 1000.0 / 48.0, 2),
                temp_c=round(28.0 + abs(bat_power) * 0.15 + random.uniform(-1, 1), 2),
                soc_pct=round(self._soc[site], 1),
                fault_code=None,
            ))

            # ---- GEN-01 柴油发电机：夜间或低电量兜底 ----------------------------
            residual = load - pv_power - bat_power         # 尚未满足的负载
            gen_power = max(0.0, residual) if (solar < 0.05 or self._soc[site] < 25) else 0.0
            gen_power = min(gen_power, 40.0) + (random.uniform(0, 2) if gen_power > 0 else 0)
            readings.append(Reading(
                ts=now, site_id=site, device_id="GEN-01",
                power_kw=round(gen_power, 3), energy_kwh=0.0,
                voltage_v=round(400.0 + random.uniform(-5, 5), 1),
                current_a=round(gen_power * 1000.0 / (400.0 * math.sqrt(3)), 2),
                temp_c=round(70.0 + gen_power * 0.5 + random.uniform(-2, 2), 2) if gen_power > 0 else round(25.0, 1),
                soc_pct=round(self._soc[site], 1),
                fault_code=None,
            ))

            # ---- TR-01 变压器：负载侧 ------------------------------------------
            tr_power = max(0.5, load + random.uniform(-0.3, 0.3))
            readings.append(Reading(
                ts=now, site_id=site, device_id="TR-01",
                power_kw=round(tr_power, 3), energy_kwh=0.0,
                voltage_v=round(400.0 + random.uniform(-4, 4), 1),
                current_a=round(tr_power * 1000.0 / (400.0 * math.sqrt(3)), 2),
                temp_c=round(38.0 + tr_power * 0.5 + random.uniform(-1.5, 1.5), 2),
                soc_pct=round(self._soc[site], 1),
                fault_code=None,
            ))
        return readings


class ModbusSource(Source):
    """
    Modbus TCP 数据源骨架。
    真实部署时用 pymodbus 读取逆变器寄存器，此处给出结构占位，
    说明"如何接入真实设备"而无需真实硬件即可保留代码骨架。
    """

    def __init__(self, sites: list[str], poll_interval: float,
                 host: str = "0.0.0.0", port: int = 502, unit: int = 1):
        super().__init__(sites, poll_interval)
        self.host, self.port, self.unit = host, port, unit

    def read(self) -> list[Reading]:
        # 真实实现示意：
        #   from pymodbus.client import ModbusTcpClient
        #   client = ModbusTcpClient(self.host, port=self.port)
        #   rr = client.read_holding_registers(0x0000, count=8, slave=self.unit)
        #   power = rr.registers[0] / 10.0   # 按厂商手册换算
        raise NotImplementedError(
            "Modbus 数据源为接入骨架，请在真实项目中按逆变器寄存器手册实现"
        )


class MqttSource(Source):
    """
    MQTT 数据源骨架。
    真实部署时用 paho-mqtt 订阅站点 topic（如 minigrid/<site>/telemetry）。
    """

    def __init__(self, sites: list[str], poll_interval: float,
                 broker: str = "localhost", topic_prefix: str = "minigrid"):
        super().__init__(sites, poll_interval)
        self.broker, self.topic_prefix = broker, topic_prefix

    def read(self) -> list[Reading]:
        raise NotImplementedError(
            "MQTT 数据源为接入骨架，请在真实项目中订阅站点 topic 并解析 JSON 负载"
        )


_DRIVERS = {
    "simulator": SimulatorSource,
    "modbus": ModbusSource,
    "mqtt": MqttSource,
}


def build_source(driver: str, sites: list[str], poll_interval: float) -> Source:
    cls = _DRIVERS.get(driver)
    if cls is None:
        log.warning("未知数据源驱动 '%s'，回退到 simulator", driver)
        cls = SimulatorSource
    return cls(sites, poll_interval)
