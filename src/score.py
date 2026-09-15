"""score.py — 线路评分（M2）

score = 0.60*norm(吞吐) + 0.20*(1-norm(延迟)) + 0.20*成功率
归一化为本轮 min-max（同轮内可比）。
"""
W_THROUGHPUT, W_LATENCY, W_SUCCESS = 0.60, 0.20, 0.20

def _norm(vals):
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return {k: 1.0 for k in range(len(vals))}
    return {i: (v - lo) / (hi - lo) for i, v in enumerate(vals)}

def rank(metrics):
    """metrics: [{line, mbps, delay_ms, success}] -> 已排序列表（含 score）"""
    if not metrics:
        return []
    nt = _norm([m["mbps"] for m in metrics])
    nl = _norm([m["delay_ms"] for m in metrics])
    out = []
    for i, m in enumerate(metrics):
        s = (W_THROUGHPUT * nt[i]
             + W_LATENCY * (1 - nl[i])
             + W_SUCCESS * m["success"])
        out.append({**m, "score": round(s, 4)})
    return sorted(out, key=lambda x: -x["score"])
