# 采集服务镜像
FROM python:3.11-slim

LABEL org.opencontainers.image.title="minigrid-collector"
LABEL org.opencontainers.image.description="Mini-Grid O&M 数据采集服务"

WORKDIR /app

# 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 源码
COPY collector.py sources.py ./

# 非 root 运行
RUN useradd -r -u 10001 collector && mkdir -p /var/run/collector && chown collector /var/run/collector
USER collector

CMD ["python", "-u", "collector.py"]
