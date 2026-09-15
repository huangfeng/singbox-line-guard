"""policy.py — 决策策略（M2）

硬规则：UDP 优先。候选池只含 UDP 系；TCP/兜底线路永不参与自动提升。
保护机制：迟滞、限频、手动覆盖保护、全局故障保护、回归回退。
"""
import time

UDP_POOL  = ["cc-bond", "cc-hy2", "cc-tuic", "rn-bond", "rn-hy2", "rn-tuic"]
TCP_POOL  = ["cc-reality", "cc-xhttp", "rn-reality", "rn-xhttp"]
TAIL_POOL = ["cc-awg", "warp-direct"]
AUTO_POOL = UDP_POOL                     # 自动切换候选池（= UDP 系）

HYSTERESIS_RATIO  = 1.20                 # 挑战者需领先 20%
HYSTERESIS_ROUNDS = 2                    # 连续 2 轮
MIN_SWITCH_GAP    = 300                  # 切换间隔 ≥ 300s
BLACKLIST_TTL     = 1800                 # 回退后拉黑 30 分钟

def decide(state, ranked, now=None):
    """返回 (target_line|None, reason)"""
    now = now or time.time()

    alive = [m for m in ranked if m["line"] in AUTO_POOL and m["success"] > 0 and m["mbps"] > 0]
    if not alive:
        return None, "全局故障保护：UDP 候选池全部不可用，保持现状并告警"

    bl = state.get("blacklist", {})
    alive = [m for m in alive if bl.get(m["line"], 0) < now]
    if not alive:
        return None, "候选池全部处于黑名单，保持现状"

    best = max(alive, key=lambda m: m["score"])
    incumbent = state.get("incumbent")

    if not incumbent or incumbent not in AUTO_POOL:
        return best["line"], f"初始化：首位候选 {best['line']}（score={best['score']}）"

    cur = next((m for m in alive if m["line"] == incumbent), None)
    if cur is None:
        return best["line"], f"现任 {incumbent} 不可用 → 切换 {best['line']}"

    if best["line"] == incumbent:
        state["challenger_streak"] = 0
        return None, f"保持 {incumbent}（仍为最优 score={cur['score']}）"

    if best["score"] > cur["score"] * HYSTERESIS_RATIO:
        state["challenger_streak"] = state.get("challenger_streak", 0) + 1
        if state["challenger_streak"] >= HYSTERESIS_ROUNDS:
            if now - state.get("last_switch", 0) < MIN_SWITCH_GAP:
                return None, f"{best['line']} 领先但未达限频间隔（{MIN_SWITCH_GAP}s）"
            state["challenger_streak"] = 0
            return best["line"], (f"迟滞满足：{best['line']} score={best['score']} "
                                  f"领先 {incumbent} score={cur['score']} >20%")
        return None, (f"挑战 {best['line']} 领先 {state['challenger_streak']}/"
                      f"{HYSTERESIS_ROUNDS} 轮，继续观察")
    state["challenger_streak"] = 0
    return None, f"保持 {incumbent}（{best['line']} 领先不足 20%）"
