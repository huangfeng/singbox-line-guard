# singbox-line-guard

sing-box 线路自动优选守护 —— **UDP 优先、真实吞吐评分、API 实时切换、零第三方依赖**。

## 为什么不用 urltest

sing-box 内置 `urltest` 只测一次 HTTP 握手延迟，**与吞吐排序相反**
（实测 UDP 59 Mbps vs TCP 45 Mbps），且系统性偏向 TCP 协议。
本项目用真实吞吐评分 + 硬性 UDP 优先策略替代它。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) | 需求分析（目标/非目标、FR/NFR、约束、验收标准） |
| [docs/DESIGN.md](docs/DESIGN.md) | 详细设计（架构、探测基座、评分模型、状态机、部署） |

## 里程碑

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M1 | 探测基座 + 12 线路真实吞吐排名 | 🚧 进行中 |
| M2 | 守护进程（评分/迟滞/覆盖保护） | ⬜ 待开始 |
| M3 | 告警集成（QQ 推送） | ⬜ 待开始 |

## 核心约束

- **UDP 优先是硬规则**：`cc-reality` / `cc-xhttp` / `warp-direct` 永不参与自动提升
- **零配置侵入**：只调 Clash API，不改 sing-box 配置文件
- **探测不干扰生产**：独立 `sel-probe` 路由（见设计 §4）
- **可随时停用**：停用后回退到静态优先级链 `fb-main`

## 相关项目
- [huangfeng/homelab-singbox](https://github.com/huangfeng/homelab-singbox) —— 88.4 透明代理网关与两端 VPS 配置
