#!/usr/bin/env python3
"""
docker_exporter.py — Docker 容器/引擎指标导出器（Prometheus 格式）
================================================================================
通过挂载的 /var/run/docker.sock 调用 Docker Engine API，采集：
  - 每个容器的 CPU %、内存使用/限制、网络收发
  - 容器运行状态 (up)
  - 引擎级信息：CPU 核数、总内存、镜像/容器数量、Docker 磁盘用量

暴露 :9105/metrics 供 Prometheus 抓取。仅用标准库，无第三方依赖。
对应需求：运维平台自身的 IT 层监控（机器/容器运行状况）。
"""
import json
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer

SOCKET_PATH = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "9105"))
CACHE_TTL = 10  # 秒，避免每次 scrape 都全量打 Docker API


def docker_api(path, method="GET"):
    """对 docker.sock 发起 HTTP 请求，返回解析后的 JSON。"""
    req = (
        f"{method} {path} HTTP/1.1\r\nHost: docker\r\nConnection: close\r\n\r\n"
    ).encode()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(30)
    try:
        s.connect(SOCKET_PATH)
        s.sendall(req)
        chunks = []
        while True:
            b = s.recv(65536)
            if not b:
                break
            chunks.append(b)
    finally:
        s.close()
    raw = b"".join(chunks)
    body = raw.split(b"\r\n\r\n", 1)[1]
    # chunked 编码处理
    if b"transfer-encoding: chunked" in raw.lower():
        out, i = [], 0
        while i < len(body):
            j = body.find(b"\r\n", i)
            if j < 0:
                break
            size = int(body[i:j], 16)
            if size == 0:
                break
            out.append(body[j + 2 : j + 2 + size])
            i = j + 2 + size + 2
        body = b"".join(out)
    return json.loads(body.decode("utf-8", "replace"))


def cpu_percent(stat: dict) -> float:
    """docker stats 同款算法：cpu_delta / system_delta * online_cpus。"""
    cpu = stat["cpu_stats"]["cpu_usage"]["total_usage"]
    pre = stat["precpu_stats"]["cpu_usage"]["total_usage"]
    sys_c = stat["cpu_stats"].get("system_cpu_usage", 0)
    pre_sys = stat["precpu_stats"].get("system_cpu_usage", 0)
    online = stat["cpu_stats"].get("online_cpus") or 1
    d_cpu, d_sys = cpu - pre, sys_c - pre_sys
    if d_sys <= 0 or d_cpu < 0:
        return 0.0
    return round(d_cpu / d_sys * online * 100.0, 2)


def collect() -> list[str]:
    lines = []
    containers = docker_api("/containers/json?all=1")
    running = [c for c in containers if c.get("State") == "running"]
    stopped = [c for c in containers if c.get("State") != "running"]

    for c in stopped:
        cid = c["Id"][:12]
        names = c["Names"][0].lstrip("/")
        img = c.get("Image", "").replace(",", ";")
        lines.append(f'docker_container_up{{name="{names}",image="{img}"}} 0')

    # 并发抓取运行中容器的 stats（串行在 Docker Desktop 上会超出抓取超时）
    def stat_lines(c):
        cid = c["Id"][:12]
        names = c["Names"][0].lstrip("/")
        img = c.get("Image", "").replace(",", ";")
        out = [f'docker_container_up{{name="{names}",image="{img}"}} 1']
        try:
            stat = docker_api(f"/containers/{cid}/stats?stream=false&one-shot=false")
        except Exception:
            return out
        mem = stat["memory_stats"].get("usage", 0)
        limit = stat["memory_stats"].get("limit", 0)
        out.append(f'docker_container_cpu_percent{{name="{names}"}} {cpu_percent(stat)}')
        out.append(f'docker_container_memory_usage_bytes{{name="{names}"}} {mem}')
        out.append(f'docker_container_memory_limit_bytes{{name="{names}"}} {limit}')
        net = stat.get("networks", {})
        rx = sum(v.get("rx_bytes", 0) for v in net.values())
        tx = sum(v.get("tx_bytes", 0) for v in net.values())
        out.append(f'docker_container_network_rx_bytes_total{{name="{names}"}} {rx}')
        out.append(f'docker_container_network_tx_bytes_total{{name="{names}"}} {tx}')
        return out

    with ThreadPoolExecutor(max_workers=8) as pool:
        for part in pool.map(stat_lines, running):
            lines.extend(part)
    info = docker_api("/info")
    lines.append(f"docker_engine_cpus {info.get('NCPU', 0)}")
    lines.append(f"docker_engine_memory_bytes {info.get('MemTotal', 0)}")
    lines.append(f"docker_engine_containers_running {info.get('ContainersRunning', 0)}")
    lines.append(f"docker_engine_containers_total {info.get('Containers', 0)}")
    lines.append(f"docker_engine_images {info.get('Images', 0)}")
    try:
        df = docker_api("/system/df?type=container&type=image&type=volume")
        sizes = sum(x.get("SizeRw", x.get("Size", 0)) for x in df.get("Containers", [])) \
            + sum(x.get("Size", 0) for x in df.get("Images", [])) \
            + sum(x.get("Size", 0) for x in df.get("Volumes", []) if x.get("Size"))
        lines.append(f"docker_engine_disk_usage_bytes {sizes}")
    except Exception:
        pass
    return lines


_cache = {"ts": 0.0, "lines": []}


def metrics() -> str:
    now = time.time()
    if now - _cache["ts"] > CACHE_TTL:
        try:
            _cache["lines"] = collect()
            _cache["ts"] = now
        except Exception as exc:  # noqa: BLE001
            _cache["lines"] = [f"# collect error: {exc}"]
    return "# TYPE docker_container_up gauge\n" + "\n".join(_cache["lines"]) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/metrics"):
            body = metrics().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):  # 静默访问日志
        pass


if __name__ == "__main__":
    print(f"docker exporter listening on :{LISTEN_PORT}")
    HTTPServer(("0.0.0.0", LISTEN_PORT), Handler).serve_forever()
