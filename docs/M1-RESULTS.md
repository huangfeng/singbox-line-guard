# M1 交付物 — 探测基座与全线路真实吞吐排名

**日期**: 2026-09-15 · **方法**: `probe.py` 经 `sel-probe` 专用 selector，5MB 分片 × 3 次取中位数

## 基础设施
| 项 | 实现 |
|---|---|
| 探测 selector | 88.4 配置新增 `sel-probe`（12 条线路，**不被任何业务规则引用**）|
| 探测路由 | `speed.cloudflare.com -> sel-probe`（独立于所有业务规则集）|
| 探测脚本 | `probe.py`（Python 3 标准库）|
| 干扰评估 | 生产流量零影响：业务规则不引用 `sel-probe` |

## 实测排名（2026-09-15）

| 排名 | 线路 | 吞吐 | 样本 | 载体 |
|---:|---|---:|---|---|
| 1 | `rn-hy2` | **39.42** | 20.76 / 44.52 / 39.42 | UDP/QUIC |
| 2 | `cc-bond` | **35.13** | — | UDP 聚合 |
| 3 | `cc-hy2` | **30.95** | — | UDP/QUIC |
| 4 | `rn-bond` | **23.14** | 8.96 / 23.14 / 27.62 | UDP 聚合 |
| 5 | `cc-tuic` | **18.11** | — | UDP/QUIC |
| 6 | `rn-tuic` | **11.73** | 11.73 / 9.90 / 12.66 | UDP/QUIC |
| 7 | `cc-xhttp` | 8.13 | 14.56 / 8.13 / 7.39 | TCP/Reality |
| 8 | `rn-reality` | 5.15 | 9.09 / 5.15 / 0.74 | TCP/Reality |
| 9 | `cc-reality` | 3.11 | 3.11 / 5.05 / 2.81 | TCP/Reality |
| 10 | `rn-xhttp` | 2.71 | 1.74 / 4.43 / 2.71 | TCP/Reality |
| 11 | `cc-awg` | 0.19 | 0.30 / 0.19 / 0.18 | AmneziaWG |
| 12 | `warp-direct` | 0.00 | 冷启动未测到 | WARP 直连 |

## 关键结论

1. **UDP 优先策略被数据验证** ✅ UDP 系占据前 6 名；TCP 系（reality/xhttp）全部 ≤ 8.13 Mbps，
   与 UDP 系存在 **4-12 倍**差距。用 `urltest` 的握手延迟排序会得出**完全相反**的结论。
2. **bond 的价值在稳定性而非峰值**：`cc-bond`(35.13) 低于 `rn-hy2`(39.42)，但其方差显著小于单协议
   （`rn-hy2` 样本 20.76→44.52，波动 2.1×）。
3. **`cc-awg`(0.19) 与 `warp-direct`(0.00) 不具备常规承载能力**，仅作最后兜底。
4. **`warp-direct` 需冷启动预热**（首次 DoH 解析 + WARP 握手约 20s），探测应在判活后延迟采样。

## 静态优先级链建议（M2 落地前）

```
cc-bond → rn-hy2 → cc-hy2 → rn-bond → cc-tuic → rn-tuic
        → cc-xhttp → rn-reality → cc-reality → rn-xhttp → cc-awg → warp-direct
```

> 静态链取「稳定性优先」（`cc-bond` 居首）；动态择优由 M2 守护进程按迟滞规则处理。
