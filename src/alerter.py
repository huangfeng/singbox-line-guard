"""alerter.py — 告警模块（M3）

边沿触发：只在「正常→全线故障」和「全线故障→恢复」的时刻各发一次，
配合冷却时间避免刷屏。告警写入 alert.pending，由 Hermes cron 任务读取并推送到 QQ。
"""
import json
import os
import time

ALERT_DIR = "/var/lib/singbox-guard"
PENDING = os.path.join(ALERT_DIR, "alert.pending")
COOLDOWN = 1800          # 同类告警最小间隔 30 分钟
FAIL_ROUNDS = 1          # 连续多少轮全线故障才告警（1 = 立即）


def _write(kind, title, body, state):
    os.makedirs(ALERT_DIR, exist_ok=True)
    item = {
        "kind": kind,                      # "all_dead" | "recovered"
        "title": title,
        "body": body,
        "ts": time.strftime("%F %T"),
        "round": state.get("round"),
    }
    # 追加写（Hermes cron 读取后清空）
    with open(PENDING, "a") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
    return item


def check(state, alive, total, lines_detail):
    """alive: 可用 UDP 线路数; total: 候选总数; lines_detail: [{line,mbps,success}]"""
    now = time.time()
    last = state.get("last_alert", {})
    active = state.get("alert_active", False)

    if alive == 0:
        if active:
            return None, "全线故障已告警，抑制重复"
        if now - last.get("all_dead", 0) < COOLDOWN:
            return None, "全线故障但处于冷却期"
        detail = "\n".join(
            "  %-12s %s" % (m["line"], "可用" if m.get("mbps", 0) > 0 else "不可用")
            for m in lines_detail)
        item = _write("all_dead",
                      "[严重] sing-box 全部 UDP 线路不可用",
                      "第 %s 轮检测：%d/%d 条 UDP 线路均不可用。\n%s\n\n"
                      "静态 fallback 链 fb-main 已接管（TCP 系 → warp-direct），"
                      "出网未中断但已降级。请检查 148/107 连通性。"
                      % (state.get("round"), alive, total, detail), state)
        state["alert_active"] = True
        state.setdefault("last_alert", {})["all_dead"] = now
        return item, "已发出全线故障告警"

    if active:
        detail = "\n".join("  %-12s %.2f Mbps" % (m["line"], m.get("mbps", 0))
                           for m in lines_detail if m.get("mbps", 0) > 0)
        item = _write("recovered",
                      "[恢复] sing-box UDP 线路已恢复",
                      "第 %s 轮检测：%d/%d 条线路恢复可用。\n%s\n\n"
                      "守护已恢复正常择优。" % (state.get("round"), alive, total, detail), state)
        state["alert_active"] = False
        return item, "已发出恢复告警"

    return None, "线路正常，无告警"


# ==================== B) 慢速劣化告警 ====================
# 场景：现任从 50 Mbps 掉到 8 Mbps，但没有更优候选 → 守护只静默保持。
# VPS 被限速 / 晚高峰劣化属于实际会发生的情况，需要主动告警。

DEGRADE_MBPS = 10.0        # 最优线路低于此值视为劣化
DEGRADE_ROUNDS = 3         # 连续 3 轮才告警（避免瞬时抖动）


def check_degradation(state, best_mbps):
    """best_mbps: 本轮最优线路的吞吐（0 表示无可用线路，交由 all_dead 处理）"""
    now = time.time()
    streak = state.get("degrade_streak", 0)

    if 0 < best_mbps < DEGRADE_MBPS:
        streak += 1
    elif best_mbps <= 0:
        streak = state.get("degrade_streak", 0)      # 全挂由 all_dead 负责，不叠加
    else:
        streak = 0
    state["degrade_streak"] = streak

    active = state.get("degrade_alert_active", False)

    if streak >= DEGRADE_ROUNDS and not active:
        if now - state.get("last_alert", {}).get("degrade", 0) < COOLDOWN:
            return None, "劣化告警处于冷却期"
        item = _write("degraded",
                      "[警告] sing-box 线路普遍降速",
                      "连续 %d 轮最优线路吞吐仅 %.2f Mbps（阈值 %.0f Mbps）。\n"
                      "出网未中断，但体验已明显下降。\n"
                      "常见原因：VPS 被限速 / 晚高峰拥塞 / 运营商 QoS。\n"
                      "建议：检查 148/107 出口带宽，或临时切到 warp-direct。"
                      % (streak, best_mbps, DEGRADE_MBPS), state)
        state["degrade_alert_active"] = True
        state.setdefault("last_alert", {})["degrade"] = now
        return item, "已发出降速告警（%.2f Mbps × %d 轮）" % (best_mbps, streak)

    if streak == 0 and active:
        item = _write("degraded_recovered",
                      "[恢复] sing-box 线路速度已恢复",
                      "最优线路吞吐回到 %.2f Mbps（阈值 %.0f Mbps），劣化告警解除。"
                      % (best_mbps, DEGRADE_MBPS), state)
        state["degrade_alert_active"] = False
        return item, "已发出降速恢复告警"

    if streak == 0:
        return None, "速度正常，无告警"
    return None, "降速 %d/%d 轮，继续观察" % (streak, DEGRADE_ROUNDS)
