# Runbook · 接入真实设备（从模拟器切换到 Modbus / MQTT）

> **适用场景**：把采集服务从内置模拟器切换到真实逆变器 / 电表数据源。
> **当前状态**：`sources.py` 已预留 `ModbusSource` / `MqttSource` 骨架，按下面步骤补全即可。

---

## 一、确认协议与点表

先拿到目标设备的厂商手册，确认：

| 项目 | 需要确认什么 |
|---|---|
| 协议 | Modbus TCP / Modbus RTU / MQTT / OPC-UA / 厂商 API |
| 连接参数 | IP、端口、从站地址(unit id)、MQTT broker 与 topic |
| 寄存器映射 | 每个指标对应的寄存器地址、数据类型、缩放系数 |
| 单位 | kW / W，V，A，℃ —— 需与 `telemetry` 表字段一致 |

---

## 二、启用依赖

编辑 `services/collector/requirements.txt`，取消对应注释：

```
pymodbus==3.6.8      # Modbus
paho-mqtt==2.1.0     # MQTT
```

---

## 三、实现数据源

### Modbus 示例

在 `services/collector/sources.py` 中补全 `ModbusSource.read()`：

```python
from pymodbus.client import ModbusTcpClient

def read(self) -> list[Reading]:
    readings = []
    with ModbusTcpClient(self.host, port=self.port) as client:
        for site in self.sites:
            rr = client.read_holding_registers(0x0000, count=8, slave=self.unit)
            r = rr.registers
            readings.append(Reading(
                ts=time.time(), site_id=site, device_id="INV-01",
                power_kw=r[0] / 10.0,          # 按手册缩放
                energy_kwh=r[1] / 100.0,
                voltage_v=r[2] / 10.0,
                current_a=r[3] / 100.0,
                temp_c=r[4] / 10.0,
                soc_pct=r[5],
            ))
    return readings
```

### MQTT 示例

订阅站点 topic，在回调中解析 JSON 负载并缓存为最新读数，`read()` 返回缓存。

---

## 四、切换配置

编辑 `.env`：

```bash
SOURCE_DRIVER=modbus          # 或 mqtt
# Modbus 需要的额外参数可在 compose 的 collector 环境中补充
```

重启采集服务：

```bash
docker compose up -d --build collector
docker compose logs -f collector
```

---

## 五、验证

```bash
# 新数据是否写入且数值合理（不是全 0 或异常量级）
docker compose exec -T timescaledb psql -U ops -d minigrid -c "
SELECT site_id, round(avg(power_kw)::numeric,2) AS avg_kw,
       round(avg(voltage_v)::numeric,1) AS avg_v,
       round(avg(temp_c)::numeric,1) AS avg_temp
FROM telemetry WHERE ts > now() - interval '5 minutes'
GROUP BY site_id;"
```

在 Grafana 看板确认曲线形态符合实际（例如白天有功率、夜间接近 0）。

---

## 六、注意事项

- **不要在恢复演练时切换数据源**，避免混淆问题来源
- 缩放系数错误是最常见问题：先核对单位，再看数值量级
- 现场网络波动时，采集服务已内建重连；但真实设备可能还需**本地缓冲**（断网暂存、恢复补传），这是下一步增强点
- 接入后更新本 Runbook 的设备清单与点表
