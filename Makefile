# =============================================================================
# Mini-Grid O&M Platform — 常用运维命令
# 用法: make <target>
# =============================================================================

SHELL := /bin/bash
COMPOSE := docker compose

.PHONY: help up down restart logs ps health backup-restore-test clean

help: ## 显示所有可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

up: ## 一键启动整个平台
	@test -f .env || (echo "未找到 .env，正在从模板复制..." && cp .env.example .env)
	$(COMPOSE) up -d --build
	@echo "启动完成。Grafana: http://localhost:$${GRAFANA_PORT:-3000}"

down: ## 停止并移除所有容器（保留数据卷）
	$(COMPOSE) down

restart: ## 重启所有服务
	$(COMPOSE) restart

logs: ## 实时查看所有服务日志
	$(COMPOSE) logs -f --tail=100

logs-collector: ## 查看采集服务日志
	$(COMPOSE) logs -f --tail=100 collector

ps: ## 查看服务运行状态
	$(COMPOSE) ps

health: ## 检查所有服务健康状态
	@$(COMPOSE) ps --format 'table {{.Name}}\t{{.Status}}'

backup-restore-test: ## 执行一次备份 + 恢复演练（对应季度恢复演练 KPI）
	@bash scripts/restore_drill.sh

clean: ## 停止并删除容器 + 数据卷（危险：会清空数据）
	$(COMPOSE) down -v
