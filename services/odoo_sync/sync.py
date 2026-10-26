#!/usr/bin/env python3
"""
odoo_sync.py — 自动告警工单同步服务

循环查询 Grafana Unified Alerting 的 firing 告警，自动推送为 Odoo 工单：
- 企业版(Odoo Online 试用): helpdesk.ticket（按团队名 Minigrid O&M）
- 社区版(本地): 回退 project.task（项目 Minigrid O&M）

去重：以 Grafana 告警 fingerprint 为键，firing 期间只建一次工单；
告警 resolved 后指纹从缓存移除，再次触发会建新单。

环境变量:
  GRAFANA_URL / GRAFANA_USER / GRAFANA_PASSWORD   Grafana 告警 API（内置 Basic Auth）
  ODOO_URL / ODOO_DB / ODOO_EMAIL / ODOO_API_KEY  Odoo 凭据
  ODOO_TEAM / ODOO_PROJECT                        团队/项目名（默认 Minigrid O&M）
  SYNC_INTERVAL_SEC                               轮询间隔（默认 60）
仅用标准库，无第三方依赖。
"""
import logging
import os
import time
import urllib.parse
import urllib.request
import base64
import xmlrpc.client

LOG = logging.getLogger("odoo-sync")

GRAFANA_URL = os.getenv("GRAFANA_URL", "http://grafana:3000").rstrip("/")
GRAFANA_AUTH = base64.b64encode(
    f"{os.getenv('GRAFANA_USER', 'admin')}:{os.getenv('GRAFANA_PASSWORD', 'admin')}".encode()
).decode()

ODOO_URL = os.getenv("ODOO_URL", "http://odoo:8069").rstrip("/")
ODOO_DB = os.getenv("ODOO_DB", "")
ODOO_EMAIL = os.getenv("ODOO_EMAIL", "")
ODOO_API_KEY = os.getenv("ODOO_API_KEY", "")
ODOO_TEAM = os.getenv("ODOO_TEAM", "Minigrid O&M")
ODOO_PROJECT = os.getenv("ODOO_PROJECT", "Minigrid O&M")
INTERVAL = int(os.getenv("SYNC_INTERVAL_SEC", "60"))

PRIO = {"P1": "3", "P2": "1", "critical": "3", "warning": "1"}


def http_get_alerts():
    req = urllib.request.Request(
        f"{GRAFANA_URL}/api/alertmanager/grafana/api/v2/alerts",
        headers={"Authorization": f"Basic {GRAFANA_AUTH}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        import json
        return json.loads(r.read().decode())


def odoo_connect():
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
    uid = common.authenticate(ODOO_DB, ODOO_EMAIL, ODOO_API_KEY, {})
    if not uid:
        raise RuntimeError("Odoo 认证失败，检查 ODOO_DB/EMAIL/API_KEY")
    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
    return uid, models


def odoo_create(uid, models, payload):
    # 企业版 Helpdesk 优先，社区版回退 Project
    team = models.execute_kw(ODOO_DB, uid, ODOO_API_KEY, "helpdesk.team", "search_read",
                             [[["name", "=", ODOO_TEAM]]], {"fields": ["id"], "limit": 1})
    if team:
        return models.execute_kw(ODOO_DB, uid, ODOO_API_KEY, "helpdesk.ticket", "create", [payload | {"team_id": team[0]["id"]}]), "helpdesk.ticket"
    proj = models.execute_kw(ODOO_DB, uid, ODOO_API_KEY, "project.project", "search_read",
                             [[["name", "=", ODOO_PROJECT]]], {"fields": ["id"], "limit": 1})
    if not proj:
        pid = models.execute_kw(ODOO_DB, uid, ODOO_API_KEY, "project.project", "create", [{"name": ODOO_PROJECT}])
    else:
        pid = proj[0]["id"]
    return models.execute_kw(ODOO_DB, uid, ODOO_API_KEY, "project.task", "create",
                             [payload | {"project_id": pid}]), "project.task"


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if not all([ODOO_DB, ODOO_EMAIL, ODOO_API_KEY]):
        LOG.warning("ODOO_DB/ODOO_EMAIL/ODOO_API_KEY 未配置，同步暂停（容器保持运行，配置后重启生效）")
        while True:
            time.sleep(3600)

    uid, models = odoo_connect()
    LOG.info("Odoo connected uid=%s, polling Grafana every %ss", uid, INTERVAL)
    pushed = {}  # fingerprint -> ticket id

    while True:
        try:
            alerts = http_get_alerts()
            for a in alerts:
                # Alertmanager v2 API: firing 告警的 state 是 "active"；
                # 兼容 "firing" 以防 Grafana 版本差异
                if a.get("status", {}).get("state") not in ("active", "firing"):
                    fp = a.get("fingerprint")
                    if fp in pushed:
                        pushed.pop(fp)
                    continue
                fp = a.get("fingerprint") or str(a.get("labels"))
                if fp in pushed:
                    continue
                labels = a.get("labels", {})
                name = labels.get("alertname", "Unnamed alert")
                # Grafana 内部提示（数据源查询无数据等）不是真实运维告警，不建单
                if name == "DatasourceNoData":
                    continue
                sev = labels.get("severity", "P2")
                desc = "Auto ticket from minigrid monitoring platform\n"
                desc += f"Severity: {sev.upper()}\n"
                for k, v in sorted(labels.items()):
                    desc += f"{k}: {v}\n"
                ann = a.get("annotations", {})
                if ann:
                    desc += "\n" + ann.get("description", "")
                try:
                    tid, kind = odoo_create(uid, models, {
                        "name": f"[{sev.upper()}] {name}",
                        "description": desc,
                        "priority": PRIO.get(sev.lower(), "1"),
                    })
                    pushed[fp] = tid
                    LOG.info("Created %s id=%s for %s (%s)", kind, tid, name, sev)
                except Exception as e:
                    LOG.error("Create ticket failed for %s: %s", name, e)
        except Exception as e:
            LOG.error("Sync error: %s", e)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
