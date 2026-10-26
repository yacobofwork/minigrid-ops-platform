# Runbook · VPN 恢复（站点连接中断）

> **适用场景**：某站点或全部站点与 ROC 失去连接，遥测中断。
> **对应告警**：站点的"数据新鲜度"指标持续攀升 / 该站点无数据上报。
> **判定为 P1 的条件**：多个站点同时失联，或超过 2 小时未恢复。

---

## 排查顺序（自下而上，逐层定位）

### 第 0 步：确认影响面

```bash
# 哪些站点没数据了？
docker compose exec -T timescaledb psql -U ops -d minigrid -c "
SELECT site_id,
       EXTRACT(EPOCH FROM (now() - max(ts))) AS seconds_since_last
FROM telemetry GROUP BY site_id ORDER BY seconds_since_last DESC;"
```

单个站点 → 现场侧问题；全部站点 → ROC 侧或 VPN 服务端问题。

### 第 1 步：确认 ROC 侧服务正常

```bash
docker compose ps                      # 编排层是否正常
docker compose logs --tail=50 collector
```

若采集服务本身异常，先走自愈流程（watchdog 应已介入），或：

```bash
docker compose restart collector
```

### 第 2 步：检查 VPN 隧道状态

在 ROC 服务器（WireGuard 示例）：

```bash
sudo wg show                    # 查看各 peer 的 latest handshake
```

| 现象 | 含义 | 处理 |
|---|---|---|
| 无该 peer | 配置丢失 | 恢复 peer 配置 |
| `latest handshake` 很久以前 | 隧道断了 | 检查对端与网络 |
| handshake 正常但仍无数据 | 隧道通、应用层问题 | 转第 3 步 |

### 第 3 步：连通性测试

```bash
# 从 ROC ping 站点网关（地址按实际网段）
ping -c 3 10.20.0.1

# 测试站点采集端口
nc -zv 10.20.0.1 502
```

### 第 4 步：现场侧确认

联系现场团队确认：
- Starlink 终端是否在线（卫星链路波动是常见原因）
- 站点路由器是否断电 / 重启中
- 是否有计划内维护

---

## 恢复动作

### 场景 A：VPN 服务端重启

```bash
sudo systemctl restart wg-quick@wg0
sudo wg show                    # 确认 peer 重新握手
```

### 场景 B：peer 配置丢失

```bash
# 用备份的配置恢复（密钥应在离线保险柜有备份）
sudo cp /backup/wg0.conf /etc/wireguard/wg0.conf
sudo wg-quick down wg0 && sudo wg-quick up wg0
```

### 场景 C：站点侧断电

等待现场恢复供电与网络，VPN 会自动重连（WireGuard 具备自动握手重试）。确认恢复：

```bash
sudo wg show                    # handshake 时间应为刚刚
```

---

## 恢复后验证

```bash
# 数据新鲜度回到正常水平（秒级）
docker compose exec -T timescaledb psql -U ops -d minigrid -c "
SELECT site_id, EXTRACT(EPOCH FROM (now() - max(ts))) AS seconds_since_last
FROM telemetry GROUP BY site_id;"
```

在 Grafana 看板确认对应站点曲线恢复。

---

## 事后动作

1. 记录中断起止时间、影响站点数、恢复手段
2. 若为 Starlink 波动，评估是否需要在边缘网关增加更长的本地缓冲
3. 更新 [incident-response.md](incident-response.md) 的时间线
