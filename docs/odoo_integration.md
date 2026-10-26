# Odoo 集成：监控告警 → 工单

把本地监控平台的告警（Grafana P1/P2、watchdog 事件）推送到 Odoo 生成工单。
支持两种部署形态：

| 形态 | 版本 | 工单落点 |
|---|---|---|
| 本地 Docker | Odoo 18 Community | `project.task`（Project 任务，阶段流转） |
| Odoo Online 试用 | Odoo 18 Enterprise | `helpdesk.ticket`（Helpdesk 工单，含 SLA） |

## 一、Odoo Online（企业版试用 15 天）

1. 注册试用：<https://www.odoo.com/zh_CN/trial>（用你的 Gmail）
2. 创建 Helpdesk 团队：Helpdesk → Configuration → Teams → 新建 `Minigrid O&M`
3. 生成 API Key：右上角头像 → My Profile → Account Security → **New API Key**（复制保存）
4. 记下你的站点 URL 与数据库名（形如 `https://xxx.odoo.com`，db 名可在 `/web/database/selector` 查看，通常与子域名相同）

## 二、本地社区版

无需 API Key 走外网，但 Odoo 18 建议同样使用 API Key（admin 登录 → Preferences → Account Security → New API Key）。确保已建项目 `Minigrid O&M`（脚本会自动创建）。

## 三、推送一条告警

```bash
# Odoo Online
python3 scripts/odoo_push.py \
    --url https://your-db.odoo.com \
    --db your-db \
    --email yacobofwork@gmail.com \
    --api-key XXXXXXXXXXXX \
    --title "Inverter INV-01 fault code E03" \
    --severity P1 \
    --description "Grafana alert: telemetry gap > 90s on minigrid-01"

# 本地社区版（默认 url=http://localhost:8069, db=saba_minigrid）
python3 scripts/odoo_push.py \
    --email admin --api-key XXXX \
    --title "P1 data outage: collector heartbeat lost" \
    --severity P1
```

也可用环境变量 `ODOO_URL / ODOO_DB / ODOO_EMAIL / ODOO_API_KEY` 免去重复传参。
仅依赖 Python 标准库（xmlrpc.client），宿主机直接 `python3` 即可运行，无需装包。

## 四、接入演示链路

### 全自动（推荐）：odoo-sync 服务

`docker compose` 已内置 `odoo-sync` 服务（共 8 个容器），自动轮询 Grafana 统一告警 API，
把所有 **firing** 告警实时推送为 Odoo 工单，无需人工执行脚本：

1. 在 `.env` 里配置（参考 `.env.example`）：
   ```
   ODOO_URL=https://saba-test.odoo.com   # 或本地 http://odoo:8069
   ODOO_DB=saba-test
   ODOO_EMAIL=yacobofwork@gmail.com
   ODOO_API_KEY=<你的API Key>
   ```
2. `docker compose up -d odoo-sync`（或整体重启）
3. 之后每次 P1/P2 告警触发 → 工单自动出现在 Helpdesk；告警恢复后再次触发会建新单
4. 未配置 ODOO_API_KEY 时该服务静默待机，不影响其他组件

### 手动模式：脚本推送

```
Grafana 告警 (P1/P2, 邮件通知)
        │
        ▼
scripts/odoo_push.py  ──XML-RPC──▶  Odoo 工单
                                     ├─ Enterprise: Helpdesk Ticket (SLA)
                                     └─ Community:  Project Task (阶段流转)
```

- **定时同步**（可选）：cron 每 5 分钟把未处理告警摘要推送为工单
  ```bash
  */5 * * * * ODOO_URL=... ODOO_API_KEY=... python3 /path/to/odoo_push.py --title "Hourly alarm digest" --severity P2
  ```
- **手动演练**：演示时先停 collector 触发 P1 告警，再跑脚本生成对应工单，展示
  "看板发现 → 邮件告警 → 工单闭环" 全流程。

## 注意事项

- Odoo Online 试用 15 天到期后数据只能导出，核心演示环境请保持本地社区版为准。
- API Key 等敏感信息放在 `.env`（已 gitignore），不要写进脚本或提交仓库。
