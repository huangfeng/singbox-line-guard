#!/usr/bin/env python3
"""guard.py — sing-box 线路自动优选守护（M2 主循环）

cron: */2 * * * * /usr/local/bin/singbox-guard/guard.py

铁律：UDP 优先。TCP/兜底线路永不自动提升，仅在 UDP 全线不可用时由
fb-main 静态 fallback 链接管（本守护不干预）。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import actuator
import alerter
import policy
import score
from probe import probe_line

LOG = "/var/log/singbox-guard.log"
FULL_SWEEP_EVERY = 5      # 每 5 轮做一次全量扫描，其余轮只测候选


def log(msg):
    line = time.strftime("%F %T ") + msg
    print(line)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def collect(state, round_no):
    """采集指标：全量扫描 or 精简扫描（现任 + 前二挑战者）"""
    if round_no % FULL_SWEEP_EVERY == 1 or "last_ranked" not in state:
        targets = list(policy.AUTO_POOL)
        log("[采集] 全量扫描 %d 条 UDP 线路" % len(targets))
    else:
        prev = state.get("last_ranked", [])[:3]
        targets = list(dict.fromkeys(prev + [state.get("incumbent")]))
        targets = [t for t in targets if t in policy.AUTO_POOL]
        log("[采集] 精简扫描 %s" % targets)

    metrics = []
    for line in targets:
        r = probe_line(line)
        metrics.append({
            "line": line,
            "mbps": r["mbps"],
            "delay_ms": 1000.0 if r["alive"] else 5000.0,
            "success": 1.0 if r["alive"] else 0.0,
        })
    return metrics


def main():
    state = actuator.load_state()
    round_no = state.get("round", 0) + 1
    state["round"] = round_no
    log("===== 第 %d 轮 =====" % round_no)

    metrics = collect(state, round_no)
    ranked = score.rank(metrics)
    state["last_ranked"] = [m["line"] for m in ranked]
    for m in ranked:
        log("  %-12s score=%-7s %7.2f Mbps  success=%s"
            % (m["line"], m["score"], m["mbps"], m["success"]))

    target, reason = policy.decide(state, ranked)
    log("[决策] " + reason)

    # ---- M3 告警（边沿触发）----
    import os
    alive_lines = [m for m in metrics if m["success"] > 0 and m["mbps"] > 0]
    if os.environ.get("GUARD_TEST_ALLDEAD") == "1":
        alive_lines = []                      # 演练：模拟全线故障
    alert_item, alert_reason = alerter.check(
        state, len(alive_lines), len(policy.AUTO_POOL), metrics)
    log("[告警] " + alert_reason)
    if alert_item:
        log("[告警] 已写入 %s：%s" % (alerter.PENDING, alert_item["title"]))

    selectors = actuator.list_selectors()
    if target:
        switched, skipped = [], []
        for sel in selectors:
            cur = actuator.current(sel)
            last = state.get("written", {}).get(sel)
            if last is not None and cur != last:
                skipped.append(sel)          # 用户手动切过 → 尊重人工选择
                continue
            actuator.switch(sel, target)
            state.setdefault("written", {})[sel] = target
            switched.append(sel)
        state["incumbent"] = target
        state["last_switch"] = time.time()
        log("[执行] 目标=%s 已切换=%s 跳过(人工覆盖)=%s" % (target, switched, skipped))
    else:
        for sel in selectors:
            cur = actuator.current(sel)
            last = state.setdefault("written", {}).get(sel)
            if last is None or cur == last:
                state["written"][sel] = cur
        log("[执行] 不切换，现任 %s" % state.get("incumbent"))

    actuator.save_state(state)
    log("===== 结束 =====")


if __name__ == "__main__":
    main()
