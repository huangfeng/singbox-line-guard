"""actuator.py — API 执行与状态落盘（M2）"""
import json, os, urllib.request

API = "http://192.168.88.4:9090"
STATE_DIR = "/var/lib/singbox-guard"
STATE_FILE = os.path.join(STATE_DIR, "state.json")

def api(method, path, payload=None, timeout=10):
    req = urllib.request.Request(
        API + path, method=method, headers={"Content-Type": "application/json"},
        data=json.dumps(payload).encode() if payload is not None else None)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")

def list_selectors():
    """守护负责的 selector：proxy + 全部分组 sel-*（sel-probe 归探测独占）"""
    data = api("GET", "/proxies")
    tags = []
    for name, info in data.get("proxies", {}).items():
        if info.get("type") != "Selector" or name == "sel-probe":
            continue
        if name == "proxy" or name.startswith("sel-"):
            tags.append(name)
    return tags

def current(name):
    return api("GET", f"/proxies/{name}").get("now")

def switch(name, line):
    api("PUT", f"/proxies/{name}", {"name": line})

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}

def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)
