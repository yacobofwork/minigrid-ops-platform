# 部署与长期运维指南

> 本文档说明如何把本项目推送到 GitHub，并作为**低维护、无人值守**的工程长期运行。

---

## 一、推送到 GitHub

### 1. 在 GitHub 创建空仓库

不要勾选 README / .gitignore / LICENSE（仓库里已有）。

### 2. 关联并推送

```bash
cd minigrid-ops-platform

# 换成你自己的仓库地址
git remote add origin git@github.com:<your-username>/minigrid-ops-platform.git

git branch -M main
git push -u origin main
```

> 若用 HTTPS：`git remote add origin https://github.com/<your-username>/minigrid-ops-platform.git`

### 3. 推送前检查（重要）

```bash
# 确认 .env 未被跟踪（必须输出 0）
git ls-files | grep -c '^\.env$'

# 确认无密码硬编码
git grep -n "change_me_in_prod" -- ':!.env.example' ':!README.md' ':!docs' || echo "无泄漏"
```

`.gitignore` 已排除 `.env`、`backups/`、`__pycache__/` 等敏感与临时文件。

---

## 二、在服务器上长期运行

### 推荐部署方式

| 场景 | 建议 |
|---|---|
| 单机长期运行 | `docker compose up -d`，所有服务 `restart: unless-stopped` |
| 希望开机自启 | 把 compose 注册为 systemd 服务（见下） |
| 多环境 | 用不同的 `.env` 文件区分 |

### 注册为 systemd 服务（开机自启）

```bash
sudo tee /etc/systemd/system/minigrid-ops.service >/dev/null <<'EOF'
[Unit]
Description=Mini-Grid O&M Platform
Requires=docker.service
After=docker.service

[Service]
Restart=always
WorkingDirectory=/opt/minigrid-ops-platform
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now minigrid-ops.service
```

---

## 三、无人值守保障机制（本项目已内置）

### 1. 容器层自愈

所有常驻服务配置 `restart: unless-stopped`，进程崩溃后由 Docker 自动拉起。

### 2. 应用层自愈

`watchdog` 监测采集服务心跳，卡死或崩溃时主动重启：

```bash
docker logs mog-watchdog --tail=20
```

### 3. 数据层自愈

数据库策略在初始化时自动注册，无需人工维护：

| 策略 | 周期 | 作用 |
|---|---|---|
| 连续聚合刷新 | 30 分钟 / 1 小时 | 看板查询提速 |
| 数据压缩 | 每 12 小时检查 | 降低存储占用 |
| 数据保留 | 每天 | 清理超期原始数据 |

### 4. 备份层

`backup` 服务每 6 小时自动备份并轮转，无需干预。

---

## 四、日常巡检清单（建议每日 5 分钟）

```bash
# 1. 服务健康
docker compose ps

# 2. 采集是否在写入
docker compose exec -T timescaledb psql -U ops -d minigrid -c \
  "SELECT site_id, max(ts) FROM telemetry GROUP BY site_id;"

# 3. 看门狗是否有异常告警
docker logs mog-watchdog --since 24h | grep -i warn || echo "24h 内无异常"

# 4. 备份是否成功
docker compose exec backup tail -5 /backups/backup.log
```

---

## 五、定期动作

| 周期 | 动作 | 命令 |
|---|---|---|
| 每周 | 检查磁盘占用 | `docker system df` |
| 每月 | 执行恢复演练 | `make backup-restore-test` |
| 每季度 | 完整恢复演练 + 记录 RTO/RPO | 见 `runbooks/data-restore.md` |
| 每年 | 凭证轮换 + 安全扫描 | 见 `runbooks/incident-response.md` |

---

## 六、告警通知接入（可选）

在 `.env` 配置 webhook，watchdog 检测到异常时会推送 P1 告警：

```bash
ALERT_WEBHOOK_URL=https://hooks.slack.com/services/xxx
```

兼容 Slack / PagerDuty / 自建接收端（发送 JSON：`severity`、`source`、`message`、`timestamp`）。

---

## 七、GitHub Actions 自动构建（可选增强）

如需自动验证，可加入 `.github/workflows/ci.yml`：

```yaml
name: CI
on: [push, pull_request]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: 校验 compose 配置
        run: docker compose config --quiet
      - name: 启动并冒烟测试
        run: |
          cp .env.example .env
          docker compose up -d
          sleep 60
          docker compose ps
```
