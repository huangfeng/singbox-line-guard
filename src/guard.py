#!/usr/bin/env python3
"""guard.py — sing-box 线路自动优选守护（M2 主循环）

cron: */2 * * * * /usr/local/bin/singbox-guard/guard.py

铁律：UDP 优先。TCP/兜底线路永不自动提升，仅在 UDP 全线不可用时由
fb-main 静态 fallback 链接管（本守护不干预）。
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import actuator
import alerter
import policy
import score
from probe import get_delay, probe_line

LOG = "/var/log/singbox-guard.log"
LOCK = "/var/lib/singbox-guard/guard.lock"
FULL_SWEEP_EVERY = 3      # 每 3 轮（30 分钟）一次全量      # 每 5 轮做一次全量扫描，其余轮只测候选


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
            "delay_ms": (r.get("delay_ms") or (1000.0 if r["alive"] else 5000.0)),
            "success": 1.0 if r["alive"] else 0.0,
        })
    return metrics


def acquire_lock():
    """防止上一轮未结束就启动下一轮（全量轮 ~100s，接近 cron 间隔）"""
    os.makedirs("/var/lib/singbox-guard", exist_ok=True)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        return fd
    except FileExistsError:
        # 锁存在：检查是否陈旧（>15 分钟）
        try:
            if time.time() - os.path.getmtime(LOCK) > 900:
                os.unlink(LOCK)
                return acquire_lock()
        except OSError:
            pass
        return None


def main():
    fd = acquire_lock()
    if fd is None:
        log("上一轮仍在运行，本次跳过（防并发）")
        return
    try:
        _main()
    finally:
        os.close(fd)
        try:
            os.unlink(LOCK)
        except OSError:
            pass


def collect_pools(state, pool):
    """采集指定池的指标（C 用于 TCP/兜底池动态择优）"""
    metrics = []
    for line in pool:
        r = probe_line(line)
        metrics.append({
            "line": line,
            "mbps": r["mbps"],
            "delay_ms": (r.get("delay_ms") or (1000.0 if r["alive"] else 5000.0)),
            "success": 1.0 if r["alive"] else 0.0,
        })
    return metrics


def _main():
    state = actuator.load_state()

    # ---- NFR11) 网关 API 可达性检查（后续所有操作都依赖它，放在最前）----
    api_ok = True
    if os.environ.get("GUARD_TEST_APIDOWN") == "1":
        api_ok = False                                    # 演练：模拟 88.4 网关挂掉
    else:
        try:
            actuator.api("GET", "/proxies", timeout=5)
        except Exception:
            api_ok = False
    api_item, api_reason = alerter.check_api(state, api_ok)
    log("[网关] " + api_reason)
    if not api_ok:
        if api_item:
            log("[网关] 已写入 %s：%s" % (alerter.PENDING, api_item["title"]))
        actuator.save_state(state)
        log("===== 结束（网关不可达，跳过本轮择优）=====")
        return

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

    # ---- C) UDP 全线不可用 → TCP/兜底池动态择优 ----
    udp_alive = [m for m in metrics if m["success"] > 0 and m["mbps"] > 0]
    if os.environ.get("GUARD_TEST_UDPDEAD") == "1":
        udp_alive = []                      # 演练：模拟 UDP 全线不可用
    if not udp_alive:
        # ---- 窄版 E) 探测自检：区分「线真挂了」与「探测目标故障」----
        # 探测全 0 但现任线路的 API 延迟正常 → 极可能是 speed.cloudflare.com 被墙/改版，
        # 而不是线路故障。此时若照常告警 + 兜底切换，会把「探测故障」误报成「网络故障」。
        incumbent_now = state.get("incumbent")
        if (os.environ.get("GUARD_TEST_UDPDEAD") != "2"        # 2 = 跳过自检，专门演练兜底
                and incumbent_now and get_delay(incumbent_now, 6000) is not None):
            log("[自检] 探测全 0 但现任 %s 的 API 延迟正常 → 疑似探测目标故障，"
                "本轮作废（不告警、不切换）" % incumbent_now)
            actuator.save_state(state)
            log("===== 结束 =====")
            return
        log("[兜底] UDP 候选池全部不可用，启动 TCP/兜底池探测")
        fb_metrics = collect_pools(state, list(policy.TCP_POOL) + list(policy.TAIL_POOL))
        fb_ranked = score.rank(fb_metrics)
        for m in fb_ranked:
            log("  [兜底] %-12s score=%-7s %7.2f Mbps  success=%s"
                % (m["line"], m["score"], m["mbps"], m["success"]))
        fb_target, fb_reason = policy.decide_fallback_pool(fb_ranked)
        log("[兜底] " + fb_reason)
        if fb_target:
            target = fb_target

    # ---- M3 告警（边沿触发）----
    alive_lines = [m for m in metrics if m["success"] > 0 and m["mbps"] > 0]
    if os.environ.get("GUARD_TEST_ALLDEAD") == "1":
        alive_lines = []                      # 演练：模拟全线故障
    alert_item, alert_reason = alerter.check(
        state, len(alive_lines), len(policy.AUTO_POOL), metrics)
    log("[告警] " + alert_reason)
    if alert_item:
        log("[告警] 已写入 %s：%s" % (alerter.PENDING, alert_item["title"]))

    # ---- B) 慢速劣化告警 ----
    best_mbps = max([m["mbps"] for m in metrics], default=0.0)
    deg_item, deg_reason = alerter.check_degradation(state, best_mbps)
    log("[劣化] " + deg_reason)
    if deg_item:
        log("[劣化] 已写入 %s：%s" % (alerter.PENDING, deg_item["title"]))

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
