# Mini-Grid O&M Platform

> 一个面向新能源微电网 **运维（O&M）** 场景的可运行监控平台。
> 一条命令拉起完整数据链路：**采集 → 时序存储 → 可视化/告警 → 自愈 → 备份演练**。

[![Docker](https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![TimescaleDB](https://img.shields.io/badge/TimescaleDB-PostgreSQL%2016-FDB515)](https://www.timescale.com/)
[![Grafana](https://img.shields.io/badge/Grafana-11.1-F46800?logo=grafana&logoColor=white)](https://grafana.com/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## 这是什么

一个以真实岗位职责（O&M System Engineer）为蓝本构建的**可运行工程**，覆盖远程运维中心（ROC）的数字骨干：

| 能力 | 实现 | 对应运维职责 |
|---|---|---|
| 数据采集 | `collector` 服务，支持模拟器 / Modbus / MQTT | 监控与数据采集平台 |
| 时序存储 | TimescaleDB（hypertable + 连续聚合 + 压缩 + 保留策略） | 数据质量与存储治理 |
| 可视化与告警 | Grafana 自动 provision 数据源与看板 | Dashboards & Alarm Management |
| **双层告警** | Grafana 告警规则（OT 设备层 + IT 平台层）→ 邮件通知 | Alarm Management（P1 响应 KPI） |
| **工单闭环** | Odoo 18 LTS（Helpdesk / Inventory），告警对应工单 | 库存数据治理 / Helpdesk |
| 无人值守自愈 | `watchdog` 监测采集心跳并自动重启 | 24/7 平台可用性 |
| 备份与恢复 | 定时 `pg_dump` + 一键恢复演练脚本 | 备份与恢复（R/A 责任） |

**设计原则：低维护、无人值守。** 所有常驻服务配置了 `restart: unless-stopped` 与健康检查；数据库策略（压缩/聚合刷新/保留）全部在初始化时自动注册；Grafana 数据源与看板自动加载。拉起后无需人工干预即可长期运行。

---

## 架构

```
  ┌──────────────── 现场层 (OT) ────────────────┐
  │  逆变器 / 电表 / BMS                        │
  │     │ Modbus TCP·RTU / MQTT / OPC-UA        │
  │     ▼                                       │
  │  采集服务 (collector)  ← 本地心跳            │
  └─────────────────┬───────────────────────────┘
                    │  (真实环境经 WireGuard/IPsec VPN 回传)
                    ▼
  ┌──────────────── ROC (本次实现范围) ─────────────┐
  │                                                 │
  │   collector ──▶ TimescaleDB ◀── grafana         │
  │       ▲              ▲              │           │
  │       │              │              ▼           │
  │   watchdog      backup(定时)   看板 + 告警       │
  │   (自愈)        restore_drill  (告警→工单闭环)   │
  │                                                 │
  └─────────────────────────────────────────────────┘
```

数据流：**采集 → hypertable → 连续聚合 → Grafana 看板/告警 → 事件留痕 → RCA**

---

## 快速开始

### 前置条件

- Docker 20.10+ 与 Docker Compose v2
- 2 GB 以上可用内存

### 一键启动

```bash
git clone <your-repo-url>
cd minigrid-ops-platform
cp .env.example .env      # 按需修改密码
docker compose up -d
```

或使用 Makefile：

```bash
make up
```

### 访问

| 服务 | 地址 | 默认账号 |
|---|---|---|
| Grafana | http://localhost:3000 | `admin` / `change_me_in_prod` |
| Odoo（Helpdesk / 库存） | http://localhost:8069 | 首次访问创建数据库，勾选 Helpdesk、Inventory |

打开 Grafana → 左侧 **Mini-Grid O&M** 文件夹 → **Mini-Grid O&M · 电站监控总览**。

![电站监控总览](docs/dashboard.png)

共五块看板：`minigrid_overview`（电站监控总览）、`plant_overview`（电站整体运行 KPI）、`site_devices`（站点设备总览：光伏/储能/柴发/变压器）、`platform_health`（平台数据库层健康：写入速率/数据新鲜度/备份状态）、`host_health`（机器与容器健康：容器 CPU/内存/网络/存活，Prometheus + 自研 Docker exporter）。模拟器每站点模拟 4 类现场设备：PV-01 光伏组件、BAT-01 储能电池、GEN-01 柴油发电机、TR-01 变压器。 另含三块看板：`minigrid_overview`（电站监控总览）、`plant_overview`（电站整体运行 KPI 总览）、`platform_health`（运维平台自身健康：写入速率/数据新鲜度/备份状态/库大小）。

### 告警（对应 JD 两层告警职责）

告警规则以 provisioning 文件自动加载（`grafana/provisioning/alerting/`），分两层：

| 层 | 规则 | 级别 |
|---|---|---|
| OT 设备层 | 站点数据中断 > 2 分钟（监控丢失） | P1 / critical |
| OT 设备层 | 逆变器故障码（过温等） | P2 / warning |
| IT 平台层 | 遥测写入速率异常（平台/库不可用） | P2 / warning |

告警默认路由到邮件联系点（收件人由 `.env` 的 `ALERT_EMAIL_TO` 配置，默认 `you@example.com`）。

**启用邮件通知**：在 `.env` 中填入 SMTP 配置（公司邮箱 / SiteGround 邮箱 / Gmail 应用密码均可）：

```bash
SMTP_ENABLED=true
SMTP_HOST=mail.example.com:587
SMTP_USER=you@example.com
SMTP_PASSWORD=你的密码
SMTP_FROM=you@example.com
```

改完执行 `docker compose restart grafana` 生效。未配置 SMTP 时告警照常触发，仅邮件发送失败（Grafana 日志可见）。

### 验证数据在流动

```bash
# 查看采集服务日志
docker compose logs -f collector

# 查询最新遥测
docker compose exec -T timescaledb \
  psql -U ops -d minigrid -c \
  "SELECT site_id, count(*), round(max(power_kw)::numeric,2) AS max_kw FROM telemetry GROUP BY site_id;"
```

约 1 分钟内即可看到每个站点持续增长的记录数。

---

## 常用命令

```bash
make help              # 查看所有命令
make up                # 启动整个平台
make ps                # 查看服务状态
make health            # 检查健康状态
make logs              # 实时日志
make backup-restore-test   # 执行一次备份恢复演练
make down              # 停止（保留数据）
make clean             # 停止并清空数据（危险）
```

---

## 核心自动化

### 1. 无人值守自愈（watchdog）

`watchdog` 每 30 秒检查采集服务的心跳文件。若心跳超过 `HEARTBEAT_MAX_AGE_SEC`（默认 90 秒）未更新，连续两次即判定异常，通过 Docker API 自动重启采集服务，并推送 P1 告警。

**验证方法：**

```bash
docker stop mog-collector        # 模拟采集服务挂掉
# 等待约 2 分钟
docker logs mog-watchdog         # 可见"心跳过期 → 触发自动重启"日志
docker compose ps collector      # 已自动恢复 running (healthy)
```

若一小时内反复重启（≥5 次），watchdog 会升级为"疑似持续性故障，需人工介入"告警，避免重启风暴。

### 2. 定时备份与恢复演练

`backup` 服务每 `BACKUP_INTERVAL_HOURS` 小时执行一次 `pg_dump`，并按 `BACKUP_RETENTION_DAYS` 自动轮转清理，日志写入备份卷的 `backup.log`。

恢复演练一键执行：

```bash
make backup-restore-test
```

脚本流程：备份 → 记录源库数据量 → 恢复到临时库 → 校验一致性 → 清理 → 输出报告。

> **实战经验（已内置到脚本）**：TimescaleDB 恢复必须先调用 `timescaledb_pre_restore()`，恢复后调用 `timescaledb_post_restore()`。若跳过这两步，时序内部表无法重新挂载到 hypertable，恢复后主表查询为 0 行。本项目的演练脚本已按官方流程处理 —— 这个坑正是"演练"存在的意义。

---

## 项目结构

```
minigrid-ops-platform/
├── docker-compose.yml              # 服务编排（5 个服务 + 健康检查 + 自愈）
├── Makefile                        # 常用运维命令
├── .env.example                    # 环境变量模板
├── db/init/01_schema.sql           # 时序建模：hypertable/聚合/压缩/保留
├── services/
│   ├── collector/                  # 数据采集服务（采集 → 入库 + 心跳）
│   │   ├── collector.py
│   │   ├── sources.py              # 数据源抽象：simulator/modbus/mqtt
│   │   └── Dockerfile
│   └── watchdog/                   # 看门狗（监测心跳并自愈）
│       ├── watchdog.py
│       └── Dockerfile
├── grafana/
│   ├── provisioning/               # 数据源 + 看板自动加载
│   └── dashboards/                 # 预置监控看板 JSON
├── scripts/
│   ├── backup_loop.sh              # 定时备份循环
│   └── restore_drill.sh            # 备份恢复演练
└── runbooks/                       # 运维手册（见下）
```

---

## 运维手册（Runbooks）

对应岗位要求的"维护成文的运行手册"，本仓库附带：

| Runbook | 用途 |
|---|---|
| [服务器重建](runbooks/server-rebuild.md) | 服务器故障后从零恢复平台 |
| [VPN 恢复](runbooks/vpn-recovery.md) | 站点连接中断的排查与恢复 |
| [数据恢复](runbooks/data-restore.md) | 数据库损坏/误删后的恢复流程 |
| [事件响应](runbooks/incident-response.md) | P1 事件按 NIST SP 800-61 六阶段处置 |
| [平台接入真实设备](runbooks/connect-real-device.md) | 从模拟器切换到 Modbus/MQTT 真实设备 |

---

## 技术栈

- **TimescaleDB** (PostgreSQL 16) — 时序存储、连续聚合、压缩、保留策略
- **Grafana 11** — 可视化、告警、自动 provisioning
- **Python 3.11** — 采集服务与看门狗
- **Docker Compose** — 编排、健康检查、自愈

---

## 验证记录

本项目已在本机完整验证，以下为实测结果：

| 验证项 | 结果 |
|---|---|
| 一键启动 5 个服务 | ✅ 全部 `Up`，collector/grafana/timescaledb 为 `healthy` |
| 数据链路 | ✅ 3 个站点遥测持续写入 TimescaleDB |
| 时序建模 | ✅ hypertable + 2 个连续聚合 + 压缩/保留策略全部注册 |
| Grafana 自动配置 | ✅ 数据源与 8 面板看板自动加载，显示实时曲线 |
| 看门狗自愈 | ✅ 停止采集服务后 120s 内自动检测并重启恢复 |
| 备份恢复演练 | ✅ `PASS`，数据零丢失 |

---

## 后续可扩展方向

- [ ] 接入真实逆变器（`sources.py` 已预留 Modbus / MQTT 骨架）
- [ ] 告警对接 Odoo Helpdesk，实现告警 → 工单自动创建
- [ ] 增加 Prometheus + node_exporter 主机层监控
- [ ] 引入 Suricata / Wazuh 做 IDS 与日志审计
- [ ] 按 IEC 62443 分区+导管完善网络分段文档
- [ ] 迁移到自建服务器并补充 Ansible 部署剧本

## License

MIT — 见 [LICENSE](LICENSE)
