#!/usr/bin/env python3
"""probe.py — sing-box 线路真实吞吐探测（M1 探测基座）

零第三方依赖（Python 3 标准库）。通过 88.4 的 Clash API 把专用探测 selector
(sel-probe) 指向候选线路，经 HTTP 代理下载固定分片，实测该线路真实吞吐。
业务规则不引用 sel-probe，生产流量零影响。

用法:
    probe.py                 # 探测全部线路，每线路 3 次取中位数
    probe.py cc-hy2 rn-bond  # 只测指定线路
"""
import json
import statistics
import subprocess
import sys
import time
import urllib.request

API = "http://192.168.88.4:9090"
PROXY = "http://192.168.88.4:7890"
PROBE_URL = "https://speed.cloudflare.com/__down?bytes={bytes}"
CHUNK = 5_000_000          # 5MB 分片（分片过小会因 TCP/QUIC 慢启动低估吞吐且各协议失真不同）
SAMPLES = 1
TIMEOUT = 25

LINES = ["cc-bond", "cc-hy2", "cc-tuic", "cc-reality", "cc-xhttp", "cc-awg",
         "rn-bond", "rn-hy2", "rn-tuic", "rn-reality", "rn-xhttp", "warp-direct"]


def api(method: str, path: str, payload=None, timeout: int = 10):
    req = urllib.request.Request(
        API + path, method=method,
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload).encode() if payload is not None else None)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def select(line: str) -> None:
    api("PUT", "/proxies/sel-probe", {"name": line})


def download(url: str) -> tuple[float, int]:
    """返回 (秒, 字节数)。用 curl 便于复用系统 TLS/连接管理。"""
    out = subprocess.run(
        ["curl", "-s", "-x", PROXY, "-o", "/dev/null", "--max-time", str(TIMEOUT),
         "-w", "%{time_total} %{size_download}", url],
        capture_output=True, text=True)
    try:
        t, n = out.stdout.split()
        return float(t), int(n)
    except Exception:
        return 0.0, 0


def probe_line(line: str) -> dict:
    """单线路：先判活，再取 SAMPLES 次吞吐中位数。"""
    select(line)
    time.sleep(2)

    # 判活（小请求，失败直接判定不可用）
    alive = subprocess.run(
        ["curl", "-s", "-x", PROXY, "-o", "/dev/null", "--max-time", "12",
         "-w", "%{http_code}", "https://www.gstatic.com/generate_204"],
        capture_output=True, text=True).stdout.strip()

    speeds = []
    for _ in range(SAMPLES):
        t, n = download(PROBE_URL.format(bytes=CHUNK))
        if n > 200_000 and t > 0:
            speeds.append(n * 8 / 1e6 / t)      # Mbps
        time.sleep(1)

    mbps = statistics.median(speeds) if speeds else 0.0
    return {"line": line, "alive": alive == "204", "mbps": round(mbps, 2),
            "samples": [round(s, 2) for s in speeds]}


def main() -> None:
    targets = sys.argv[1:] or LINES
    results = []
    print(f"{'线路':<14}{'活':<4}{'Mbps':<10}{'样本'}")
    print("-" * 52)
    for line in targets:
        r = probe_line(line)
        results.append(r)
        flag = "✅" if r["alive"] else "❌"
        print(f"{r['line']:<14}{flag:<4}{r['mbps']:<10}{r['samples']}")

    results.sort(key=lambda x: (-int(x["alive"]), -x["mbps"]))
    print("\n=== 排名（可用优先，再按吞吐）===")
    for i, r in enumerate(results, 1):
        print(f"{i:>2}. {r['line']:<14}{r['mbps']:>8.2f} Mbps  {'✅' if r['alive'] else '❌ 不可用'}")

    with open("/tmp/probe-result.json", "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n结果已写入 /tmp/probe-result.json")


if __name__ == "__main__":
    main()
