import json
import time
import urllib.request
from urllib.error import URLError

REGISTRY_URL = "http://127.0.0.1:7000"
AGENT_NAME = "mock_heartbeat_agent"

def print_header(title: str):
    print(f"\n{'='*20} {title} {'='*20}")

def post_json(endpoint: str, payload: dict):
    req = urllib.request.Request(
        f"{REGISTRY_URL}{endpoint}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3.0) as res:
            return json.loads(res.read().decode())
    except URLError as e:
        print(f"Failed to POST {endpoint}: {e}")
        return None

def get_json(endpoint: str):
    req = urllib.request.Request(f"{REGISTRY_URL}{endpoint}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=3.0) as res:
            return json.loads(res.read().decode())
    except URLError as e:
        print(f"Failed to GET {endpoint}: {e}")
        return None

def main():
    print_header("1. 注册模拟 Agent (Register)")
    payload = {
        "agent_name": AGENT_NAME,
        "host": "127.0.0.1",
        "port": 9999,
        "protocol": "tcp",
        "capabilities": ["mock.heartbeat.test"],
    }
    res = post_json("/register", payload)
    print(f"注册响应: {res}")

    print_header("2. 初始状态检查 (GET /agents)")
    agents_data = get_json("/agents")
    if agents_data and agents_data.get("ok"):
        mock_data = agents_data["agents"].get(AGENT_NAME, {})
        print(f"{AGENT_NAME} 当前状态: {mock_data.get('status')}")
    else:
        print("无响应或注册中心未启动")
        return

    print_header("3. 模拟正常心跳机制 (发送3次，间隔2秒)")
    for i in range(1, 4):
        time.sleep(2)
        res = post_json("/heartbeat", {"agent_name": AGENT_NAME})
        print(f"发送第 {i} 次心跳，响应: {res}")

    print_header("4. 正常心跳期间检查存活列表 (GET /discover)")
    discover_data = get_json("/discover")
    if discover_data:
        discovered = discover_data.get("agents", {})
        is_present = AGENT_NAME in discovered
        print(f"{AGENT_NAME} 是否在存活列表中? {'是 (Yes)' if is_present else '否 (No)'}")

    print_header("5. 停止发送心跳，等待超过 TTL 阈值 (6秒+)...")
    for wait_time in range(7, 0, -1):
        print(f"距离判定离线还有... {wait_time} 秒")
        time.sleep(1)

    print_header("6. TTL 超时后检查存活列表 (GET /discover)")
    discover_data = get_json("/discover")
    if discover_data:
        discovered = discover_data.get("agents", {})
        is_present = AGENT_NAME in discovered
        print(f"{AGENT_NAME} 是否还在存活列表中? {'是 (Yes)' if is_present else '否 (No，已被软剔除)'}")

    print_header("7. 最终全量诊断状态 (GET /agents)")
    agents_data = get_json("/agents")
    if agents_data:
        mock_data = agents_data["agents"].get(AGENT_NAME, {})
        print(f"{AGENT_NAME} 最终标记状态: {mock_data.get('status', 'unknown')}")

if __name__ == "__main__":
    main()