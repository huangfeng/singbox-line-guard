# 详细设计 — sing-box 线路自动优选守护

## 1. 架构
```
[88.10 Ubuntu]  守护进程 (Python3 stdlib, cron 每 2 分钟)
      │  HTTP
      ▼
[88.4 sing-box] Clash API :9090
      ├── GET  /proxies                   列线路 + history
      ├── GET  /proxies/<tag>/delay       判活
      ├── GET  /connections               真实流量速率/失败（零负载信号）
      └── PUT  /proxies/<selector>        切换
```

## 2. 组件
| 组件 | 职责 |
|---|---|
| `probe` | 采集器：连通性、吞吐、失败率 |
| `score` | 评分器：归一化 + 加权 |
| `policy` | 决策器：迟滞、限频、覆盖保护、回归保护 |
| `actuator` | 执行器：调 API 切换、落盘状态 |
| `guard` | 主循环（cron 入口） |

## 3. 线路分类（UDP 优先的硬编码来源）
```python
UDP_POOL  = ["cc-bond", "cc-hy2", "cc-tuic", "rn-bond", "rn-hy2", "rn-tuic"]
TCP_POOL  = ["cc-reality", "cc-xhttp", "rn-reality", "rn-xhttp"]   # 仅兜底
TAIL_POOL = ["warp-direct"]                                        # 最后兜底
AUTO_POOL = UDP_POOL                # 自动切换候选池
```

## 4. 探测基座（不干扰生产流量）⭐
在 88.4 配置新增**只服务探测**的 selector 与路由规则：
```json
{"type":"selector","tag":"sel-probe","outbounds":[全部线路],"default":"cc-hy2"}
```
```json
{"route":{"rules":[{"domain":["probe.huangs.online"],"outbound":"sel-probe"}]}}
```
- 执行器把 `sel-probe` 指向候选线路 → 经 `7890` 下载固定分片 → 计时
- 业务规则不引用 `sel-probe`，**生产流量零影响**
- 探测域名命中该规则，因此结果真实反映该线路端到端吞吐

## 5. 评分模型
```
score = 0.60*norm(throughput) + 0.20*(1-norm(latency)) + 0.20*success_rate
```
- `norm()` 为本轮 min-max 归一化（同轮内可比）
- **延迟为实测值**：`/proxies/<tag>/delay`（timeout 8s）；失败则回退常量 1000/5000 ms
  - ⚠️ 该延迟是 TCP 语义握手耗时，对 UDP 系（hy2/tuic）系统性偏高 → 故权重仅 0.20，主指标为吞吐
- 吞吐取 **1 次 5MB 分片**（低采样换大分片：分片过小会因 TCP/QUIC 慢启动低估吞吐，且各协议失真程度不同，会导致排名错误）
- `success_rate` 来自判活结果与 `/connections` 失败计数

## 6. 决策状态机
```
采集(5MB×1) → 过滤不可用 → 对 AUTO_POOL(UDP) 评分 → 取最高分 challenger
  IF challenger == incumbent: 保持
  IF challenger > incumbent*1.2 连续 2 轮: 切换并记录
  ELSE: 保持（迟滞）

IF UDP 池全部不可用:                          # C) FR13
  探测 TCP_POOL + TAIL_POOL → 同一评分模型择优 → 切换
  IF 也全部不可用: 判定本地网络故障 → 保持现状 + 告警
```
| 保护 | 规则 |
|---|---|
| 限频 | 切换间隔 ≥ 300s |
| 手动覆盖 | `selector 当前值 != 上次写入值` → 跳过 |
| 全局故障 | 全线失败 → 不发切换指令，仅告警 |
| 回归保护 | 切换后 60s 失败率上升 → 回退 + 拉黑 30 分钟 |

## 7. 切换范围
主 selector `proxy` + 11 个分组 `sel-*`；用户手切过的分组永不改写（FR7）；`sel-probe` 由守护独占。

## 8. 数据与日志
```
/var/lib/singbox-guard/state.json     # incumbent、写入历史、黑名单、切换时间
/var/lib/singbox-guard/probe.jsonl    # 每轮全线路原始指标
/var/log/singbox-guard.log            # 决策日志（含理由）
```

## 9. 探测成本约束（NFR7）

| 轮次类型 | 线路数 | 流量 |
|---|---|---|
| 全量轮（每 3 轮 / 30 分钟） | 6 | 30 MB |
| 精简轮（其余） | 3 | 15 MB |

按 cron 每 10 分钟估算：**约 1.5-2.5 GB/天**。

> 反面教训：曾为省流量把分片缩到 1.5MB，结果 `cc-hy2` 从 31 → 7.2 Mbps 的假性下跌
> （TCP/QUIC 慢启动受影响程度不同），**排名直接失真**。正确做法是「大分片 + 低采样」。

## 10. 部署
```bash
*/2 * * * * /usr/local/bin/singbox-guard/guard.py >> /var/log/singbox-guard.log 2>&1
```
停用：注释 cron 即可（NFR5）。

## 11. 里程碑
| 里程碑 | 内容 | 交付物 |
|---|---|---|
| **M1** | 探测基座 | `sel-probe` + 路由规则 + `probe.py` + **12 线路吞吐排名** |
| **M2** | 守护进程 | `score/policy/actuator/guard` + cron + 24h 稳定性 |
| **M3** | 告警集成 | 全线失败 → QQ 推送 + 接入 watchdog |
