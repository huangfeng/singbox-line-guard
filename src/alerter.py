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
