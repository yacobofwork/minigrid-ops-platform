"""
Mini-Grid O&M Platform — 看门狗 (Watchdog)
================================================================================
角色：无人值守自愈。监测采集服务心跳文件，若超时未更新则通过 Docker API
      重启目标容器；可选地推送告警 Webhook。

这直接对应岗位"高度自动化、低维护"的目标：
  - 采集服务卡死 / 崩溃但未退出时，watchdog 能发现并自愈
  - 自愈动作写入日志，形成可审计的事件流
  - 恢复后发送告警通知，符合 P1 事件响应要求

运行方式（由 docker compose 拉起）：
    挂载 /var/run/docker.sock 与心跳卷，读取环境变量后进入循环。
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
import urllib.request
from pathlib import Path

import docker

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
TARGET_CONTAINER = os.getenv("TARGET_CONTAINER", "mog-collector")
HEARTBEAT_FILE = Path(os.getenv("HEARTBEAT_FILE", "/var/run/collector/heartbeat"))
HEARTBEAT_MAX_AGE = float(os.getenv("HEARTBEAT_MAX_AGE_SEC", "90"))
CHECK_INTERVAL = float(os.getenv("CHECK_INTERVAL_SEC", "30"))
WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "").strip()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)-7s [watchdog] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("watchdog")

_running = True
_restart_timestamps: list[float] = []


def notify(message: str, severity: str = "P1") -> None:
    """向 Webhook 推送告警（未配置则仅记日志）。"""
    log.warning("告警[%s] %s", severity, message)
    if not WEBHOOK_URL:
        return
    payload = json.dumps({
        "severity": severity,
        "source": "minigrid-watchdog",
        "message": message,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            WEBHOOK_URL, data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info("Webhook 已发送，HTTP %s", resp.status)
    except Exception as exc:  # noqa: BLE001
        log.error("Webhook 发送失败: %s", exc)


def heartbeat_age() -> float | None:
    """返回心跳文件的年龄（秒）；文件不存在返回 None。"""
    try:
        return time.time() - HEARTBEAT_FILE.stat().st_mtime
    except OSError:
        return None


def restart_target(client: docker.DockerClient) -> bool:
    """通过 Docker API 重启目标容器。"""
    global _restart_timestamps
    try:
        container = client.containers.get(TARGET_CONTAINER)
        container.restart(timeout=10)
        now = time.time()
        _restart_timestamps.append(now)
        # 只保留最近一小时的重启记录，用于检测"重启风暴"
        _restart_timestamps = [t for t in _restart_timestamps if now - t < 3600]
        log.warning("已重启容器 %s（近 1 小时第 %d 次）",
                    TARGET_CONTAINER, len(_restart_timestamps))
        if len(_restart_timestamps) >= 5:
            notify(f"容器 {TARGET_CONTAINER} 近 1 小时内已重启 "
                   f"{len(_restart_timestamps)} 次，疑似持续性故障，需人工介入", "P1")
        return True
    except docker.errors.NotFound:
        notify(f"目标容器 {TARGET_CONTAINER} 不存在，无法自愈", "P1")
        return False
    except Exception as exc:  # noqa: BLE001
        log.error("重启容器失败: %s", exc)
        return False


def run() -> None:
    log.info("看门狗启动 | target=%s | 心跳上限=%.0fs | 检查间隔=%.0fs",
             TARGET_CONTAINER, HEARTBEAT_MAX_AGE, CHECK_INTERVAL)
    client = docker.from_env()
    unhealthy_streak = 0

    while _running:
        age = heartbeat_age()

        if age is None:
            log.warning("心跳文件不存在（等待采集服务首次写入）")
            unhealthy_streak = 0
        elif age > HEARTBEAT_MAX_AGE:
            unhealthy_streak += 1
            log.warning("心跳过期：%.0fs 未更新（阈值 %.0fs），连续异常 %d 次",
                        age, HEARTBEAT_MAX_AGE, unhealthy_streak)
            # 连续两次判定为异常才重启，避免瞬时抖动导致误动作
            if unhealthy_streak >= 2:
                notify(f"采集服务 {TARGET_CONTAINER} 心跳停止 "
                       f"{age:.0f}s，触发自动重启", "P1")
                if restart_target(client):
                    unhealthy_streak = 0
                    # 重启后给容器启动时间
                    time.sleep(20)
        else:
            if unhealthy_streak:
                log.info("心跳恢复正常（年龄 %.0fs）", age)
            unhealthy_streak = 0

        time.sleep(CHECK_INTERVAL)

    log.info("看门狗收到退出信号，已停止")


def _handle_signal(signum, _frame) -> None:
    global _running
    log.info("收到信号 %s，准备退出", signum)
    _running = False


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    run()
    sys.exit(0)
