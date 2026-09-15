#!/bin/bash
# singbox-guard-alert.sh — 读取守护告警并输出（供 Hermes cron no_agent 模式投递）
# 约定：stdout 为空 → 不发送任何消息；有内容 → 原文投递
F=/var/lib/singbox-guard/alert.pending

[ -s "$F" ] || exit 0

python3 - "$F" <<'PY'
import json, sys
path = sys.argv[1]
items = []
with open(path) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            items.append({"title": "（无法解析的告警）", "body": line, "ts": ""})

if not items:
    sys.exit(0)

out = ["🔔 singbox-line-guard 告警"]
for it in items:
    out.append("")
    out.append(it.get("title", ""))
    out.append(it.get("body", "").rstrip())
    if it.get("ts"):
        out.append("时间: %s（第 %s 轮）" % (it["ts"], it.get("round", "-")))
print("\n".join(out))
PY

# 投递成功后清空，避免重复推送
: > "$F"
