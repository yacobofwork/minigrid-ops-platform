# Runbook · 服务器重建

> **适用场景**：ROC 服务器彻底故障、需要从零重建整条平台。
> **目标恢复时间**：≤ 4 小时（含数据恢复）
> **最后验证**：见 `git log` 中本文件的最近一次演练记录

---

## 前置信息（发生故障前就应备好）

| 项目 | 存放位置 | 说明 |
|---|---|---|
| 代码仓库 | GitHub | 本仓库，含全部编排与脚本 |
| `.env` 生产配置 | 密码管理器 / 离线保险柜 | **不提交到 git**，必须独立保存 |
| 最新数据库备份 | 备份卷 / 对象存储 | `backup` 服务自动生成 |
| 域名与证书 | DNS 服务商 / 证书库 | 如需 HTTPS 访问 |

---

## 重建步骤

### 1. 准备主机

```bash
# 安装 Docker 与 Compose（Ubuntu 示例）
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"
newgrp docker
docker --version && docker compose version
```

### 2. 拉取代码

```bash
git clone <your-repo-url> minigrid-ops-platform
cd minigrid-ops-platform
```

### 3. 恢复生产配置

```bash
# 从密码管理器取回 .env（切勿使用 .env.example 的默认密码上线）
cp /secure/path/.env .env
chmod 600 .env
```

### 4. 启动基础服务（先不启动业务）

```bash
docker compose up -d timescaledb
docker compose ps timescaledb        # 等待 healthy
```

### 5. 恢复数据

见 [data-restore.md](data-restore.md)。**先恢复数据，再启动采集与 Grafana**，避免采集服务在空库上写入干扰恢复。

### 6. 启动其余服务

```bash
docker compose up -d
docker compose ps                    # 全部应为 Up / healthy
```

### 7. 验证

```bash
# 数据在写入
docker compose exec -T timescaledb \
  psql -U ops -d minigrid -c "SELECT count(*) FROM telemetry;"

# Grafana 可访问
curl -sf http://localhost:3000/api/health

# 走一遍恢复演练，确认备份链路正常
make backup-restore-test
```

---

## 验收清单

- [ ] 5 个容器全部 `Up`（collector/grafana/timescaledb 为 healthy）
- [ ] Grafana 看板可打开且有数据
- [ ] 采集服务日志持续输出
- [ ] `make backup-restore-test` 通过
- [ ] 更新本 Runbook 的"最后验证"日期

---

## 事后动作

1. 记录重建耗时，写入事件时间线
2. 分析根因（硬件 / 配置 / 人为），安排 RCA
3. 若暴露了 Runbook 缺口，立即更新本文件
