# 看门狗镜像
FROM python:3.11-slim

LABEL org.opencontainers.image.title="minigrid-watchdog"
LABEL org.opencontainers.image.description="Mini-Grid O&M 采集服务看门狗（自愈）"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY watchdog.py ./

CMD ["python", "-u", "watchdog.py"]
