#!/usr/bin/env python3
"""
odoo_push.py — 把监控告警推送到 Odoo（本地社区版 Project 或 Odoo Online 企业版 Helpdesk）

用法（在宿主机或任何能访问 Odoo 的机器上运行）:
    python3 scripts/odoo_push.py \
        --url https://xxx.odoo.com \
        --db xxx \
        --email yacobofwork@gmail.com \
        --api-key <API_KEY> \
        --title "Inverter INV-01 fault code E03" \
        --severity P1 \
        --description "Grafana alert: telemetry gap > 90s on minigrid-01"

亦可用环境变量: ODOO_URL / ODOO_DB / ODOO_EMAIL / ODOO_API_KEY

设计说明:
- 使用 Odoo 标准 XML-RPC API（社区版/在线企业版通用）
- 企业版: 若装有 helpdesk 模块则建 Helpdesk Ticket
- 社区版: 回退到 project.project 任务
- 供演示/接入 watchdog 或人工触发; 也可被 cron 周期调用同步告警摘要
"""
import argparse
import os
import sys
import xmlrpc.client


def connect(url, db, email, api_key):
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    info = common.version()
    uid = common.authenticate(db, email, api_key, {})
    if not uid:
        sys.exit("ERROR: 认证失败, 请检查 email / API key (Settings > Users > API Keys)")
    print(f"Connected: Odoo {info.get('server_version')} uid={uid}")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", allow_none=True)
    return uid, models


def create_ticket(uid, models, db, api_key, title, description, severity, team_name, project_name):
    # 1) 尝试企业版 Helpdesk
    helpdesk_team = models.execute_kw(
        db, uid, api_key, "helpdesk.team", "search_read",
        [[["name", "=", team_name]]], {"fields": ["id"], "limit": 1})
    if helpdesk_team:
        ticket_id = models.execute_kw(
            db, uid, api_key, "helpdesk.ticket", "create",
            [{"name": title, "team_id": helpdesk_team[0]["id"],
              "description": description,
              "priority": "3" if severity.upper() == "P1" else "1"}])
        print(f"Helpdesk ticket created: id={ticket_id}")
        return ticket_id

    # 2) 社区版回退: Project 任务
    project = models.execute_kw(
        db, uid, api_key, "project.project", "search_read",
        [[["name", "=", project_name]]], {"fields": ["id"], "limit": 1})
    if not project:
        project_id = models.execute_kw(
            db, uid, api_key, "project.project", "create",
            [{"name": project_name}])
        print(f"Project created: id={project_id}")
    else:
        project_id = project[0]["id"]
    task_id = models.execute_kw(
        db, uid, api_key, "project.task", "create",
        [{"name": title, "project_id": project_id,
          "description": description,
          "priority": "1" if severity.upper() == "P1" else "0"}])
    print(f"Project task created: id={task_id}")
    return task_id


def main():
    p = argparse.ArgumentParser(description="Push monitoring alert to Odoo")
    p.add_argument("--url", default=os.getenv("ODOO_URL", "http://localhost:8069"))
    p.add_argument("--db", default=os.getenv("ODOO_DB", "saba_minigrid"))
    p.add_argument("--email", default=os.getenv("ODOO_EMAIL"))
    p.add_argument("--api-key", default=os.getenv("ODOO_API_KEY"))
    p.add_argument("--title", required=True)
    p.add_argument("--severity", default="P2", choices=["P1", "P2"])
    p.add_argument("--description", default="")
    p.add_argument("--team", default="Minigrid O&M")
    p.add_argument("--project", default="Minigrid O&M")
    args = p.parse_args()

    if not args.email or not args.api_key:
        sys.exit("ERROR: 需要 --email / --api-key (或环境变量 ODOO_EMAIL / ODOO_API_KEY)")

    uid, models = connect(args.url, args.db, args.email, args.api_key)
    desc = args.description or f"Auto-generated from minigrid monitoring platform. Severity: {args.severity}"
    create_ticket(uid, models, args.db, args.api_key,
                  args.title, desc, args.severity, args.team, args.project)


if __name__ == "__main__":
    main()
