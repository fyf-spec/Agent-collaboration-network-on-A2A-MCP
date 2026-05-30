import streamlit as st
import requests
import time
import subprocess
import os
import signal
from typing import Dict, Any

st.set_page_config(page_title="A2A 旅行工作流 Agent", page_icon="✈️", layout="wide")

COORDINATOR_URL = "http://127.0.0.1:9000/submit_task"

import sys
import atexit

# --- 状态管理：保存进程和配置 ---
# Streamlit 在退出时会销毁 st.session_state，导致 atexit 钩子报错
# 因此将进程字典提取为真正的全局变量，仅供后台管理使用
if "GLOBAL_PROCESSES" not in st.session_state:
    st.session_state.GLOBAL_PROCESSES = {}

# 方便后续代码引用全局字典
_processes = st.session_state.GLOBAL_PROCESSES

# --- 服务定义与启动逻辑 ---
SERVICES = {
    "registry_center_primary": [sys.executable, "registry_center.py"],
    "registry_center_backup": [sys.executable, "registry_center.py", "--port", "7001"],
    "weather_mcp_server": [sys.executable, "mcp_servers/weather_mcp_server.py"],
    "traffic_mcp_server": [sys.executable, "mcp_servers/traffic_mcp_server.py"],
    "attraction_mcp_server": [sys.executable, "mcp_servers/attraction_mcp_server.py"],
    "hotel_mcp_server": [sys.executable, "mcp_servers/hotel_mcp_server.py"],
    "packing_mcp_server": [sys.executable, "mcp_servers/packing_mcp_server.py"],
    "mcp_gateway": [sys.executable, "mcp_gateway.py"],
    "weather_agent": [sys.executable, "agents/weather_agent.py"],
    "attraction_agent": [sys.executable, "agents/attraction_agent.py"],
    "hotel_agent": [sys.executable, "agents/hotel_agent.py"],
    "traffic_agent": [sys.executable, "agents/traffic_agent.py"],
    "packing_agent": [sys.executable, "agents/packing_agent.py"],
    "coordinator": [sys.executable, "coordinator.py"],
}

def start_service(name: str, env_vars: Dict[str, str], delay: float = 0.0):
    if name in _processes and _processes[name].poll() is None:
        return # Already running
    
    cmd = SERVICES[name]
    env = os.environ.copy()
    env.update(env_vars)
    
    # 强制 Python 无缓冲输出，保证日志实时打印到终端
    env["PYTHONUNBUFFERED"] = "1"
    
    # 支持给 MCP Server 添加 delay
    if name.endswith("_mcp_server") and delay > 0.0:
        cmd = cmd + ["--delay", str(delay)]
        
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr
    )
    _processes[name] = proc

def stop_service(name: str):
    if name in _processes:
        proc = _processes[name]
        if proc.poll() is None:
            if os.name == 'nt':
                proc.terminate()
            else:
                proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        del _processes[name]

def stop_all_services():
    for name in list(_processes.keys()):
        stop_service(name)

# 注册退出时的清理函数，防止 Streamlit 关闭时遗留僵尸进程
atexit.register(stop_all_services)

# --- 页面布局 ---
st.title("✈️ A2A 旅行工作流 Agent")

col_sidebar, col_main = st.columns([1, 2])

with col_sidebar:
    st.header("⚙️ 节点管理与容错配置")
    
    st.markdown("### 全局超时参数")
    a2a_tcp_timeout = st.number_input("A2A TCP Timeout (秒)", value=3.0, step=1.0)
    mcp_http_timeout = st.number_input("MCP HTTP Timeout (秒)", value=3.0, step=1.0)
    task_timeout = st.number_input("Task 整体 Timeout (秒)", value=120.0, step=10.0)
    
    st.markdown("### 启停控制")
    col_btn1, col_btn2 = st.columns(2)
    
    env_config = {
        "A2A_TCP_TIMEOUT_SECONDS": str(a2a_tcp_timeout),
        "MCP_HTTP_TIMEOUT_SECONDS": str(mcp_http_timeout),
        "DEFAULT_TASK_TIMEOUT_SECONDS": str(task_timeout),
        "MAX_TASK_TIMEOUT_SECONDS": str(task_timeout),
        "PYTHONIOENCODING": "utf-8"
    }
    
    # 将系统环境变量中的 API_KEY 也传过去，防止丢失
    for k, v in os.environ.items():
        if k not in env_config:
            env_config[k] = v

    if col_btn1.button("▶️ 启动所有节点", use_container_width=True):
        with st.spinner("正在启动所有服务..."):
            for srv in SERVICES.keys():
                start_service(srv, env_config)
            time.sleep(3)
        st.rerun()
        
    if col_btn2.button("⏹️ 停止所有节点", use_container_width=True):
        stop_all_services()
        st.rerun()

    st.markdown("### 节点独立控制 (Chaos Test)")
    for srv_name in SERVICES.keys():
        is_running = srv_name in _processes and _processes[srv_name].poll() is None
        status_color = "🟢" if is_running else "🔴"
        
        # 为 MCP Server 提供独立的 delay 输入框
        is_mcp = srv_name.endswith("_mcp_server")
        
        if is_mcp:
            col_name, col_delay, col_action = st.columns([2, 1, 1])
        else:
            col_name, col_action = st.columns([3, 1])
            
        with col_name:
            st.write(f"{status_color} {srv_name}")
            
        delay_val = 0.0
        if is_mcp:
            with col_delay:
                if not is_running:
                    delay_val = st.number_input("Delay", value=0.0, step=1.0, key=f"delay_{srv_name}", label_visibility="collapsed")
                else:
                    # st.write("已启动")
                    pass
                    
        with col_action:
            if is_running:
                if st.button("Kill", key=f"kill_{srv_name}"):
                    stop_service(srv_name)
                    st.rerun()
            else:
                if st.button("Start", key=f"start_{srv_name}"):
                    start_service(srv_name, env_config, delay=delay_val)
                    st.rerun()

with col_main:
    st.header("💬 旅行任务交互")
    st.markdown("输入您的旅行需求，Coordinator 将根据当前存活的 Agents 动态分析依赖，为您完成规划。")

    query = st.text_area(
        "请描述您的旅行需求：", 
        value="中秋节假期从上海去北京玩3天，要求穷游并且尽量乘坐地铁，必须去故宫看看。"
    )

    if st.button("提交任务 / Submit", type="primary"):
        is_coordinator_running = "coordinator" in _processes and _processes["coordinator"].poll() is None
        
        if not is_coordinator_running:
            st.error("Coordinator 尚未启动！请先在左侧启动节点。")
        elif not query.strip():
            st.warning("请输入您的旅行需求。")
        else:
            try:
                st.session_state.task_start_time = time.time()
                with st.status("正在提交任务，Coordinator 正在进行总体规划...", expanded=True, state="running") as submit_status:
                    st.write("已收到你的问题，正在发送给 Coordinator。")
                    st.write("正在生成总体规划与工作流 DAG，请稍等...")
                    response = requests.post(
                        COORDINATOR_URL,
                        json={"question": query, "timeout": task_timeout, "async": True},
                        timeout=task_timeout + 1
                    )
                    response.raise_for_status()
                    data = response.json()
                    st.session_state.current_task_id = data.get("task", {}).get("task_id")
                    submit_status.update(
                        label=f"任务已提交成功，耗时 {time.time() - st.session_state.task_start_time:.1f} 秒",
                        state="complete",
                        expanded=False,
                    )
                st.rerun() # 触发页面重载以脱离按钮作用域渲染任务状态
            except Exception as e:
                st.error(f"启动任务失败: {e}")

    task_id = st.session_state.get("current_task_id")
    if task_id:
        try:
            start_time = st.session_state.get("task_start_time", time.time())
            poll_resp = requests.get(f"http://127.0.0.1:9000/tasks?task_id={task_id}", timeout=5)
            poll_resp.raise_for_status()
            task = poll_resp.json().get("task", {})

            is_completed = task.get("status") in ["completed", "failed", "partial"]
            elapsed = time.time() - start_time
            status_label = (
                f"整个工作流执行结束！总耗时: {elapsed:.1f} 秒"
                if is_completed
                else f"正在调度多 Agent 工作流，请稍等... 当前耗时: {elapsed:.1f} 秒"
            )
            status_state = "complete" if is_completed else "running"

            with st.status(status_label, expanded=True, state=status_state):
                if not is_completed:
                    st.write("已将您的描述发送到 Coordinator...")

                st.write("✅ Coordinator 已完成初始调度网络规划！")
                plan = task.get("plan", {})
                if plan:
                    with st.expander("📝 阶段 1：Coordinator 动态规划的工作流和分配情况 (DAG)", expanded=False):
                        st.json(plan, expanded=True)

                st.markdown("### 🤖 阶段 2：各个 Worker Agent 执行动态")
                results = task.get("results", {})
                errors = task.get("dispatch_errors", {})

                if not results and not errors:
                    st.info("当前还没有 Agent 返回结果。")

                for agent_name, result_data in results.items():
                    status_emoji = "✅" if result_data.get("status") == "success" else "❌"
                    with st.expander(f"{status_emoji} {agent_name} 的执行报告", expanded=False):
                        st.json(result_data, expanded=True)

                for agent_name, err_msg in errors.items():
                    with st.expander(f"💀 {agent_name} [DISPATCH_ERROR]", expanded=True):
                        st.error(err_msg)

            final_answer = task.get("final_answer")
            if final_answer:
                st.markdown("---")
                st.markdown(f"### 🌟 阶段 3：最终旅行方案 (状态: {task.get('status')})")
                st.success("多智能体系统已组合生成了这一份行程报告！", icon="🎉")
                st.markdown(final_answer)
            elif is_completed:
                st.warning("任务已结束，但未生成最终结果。")

            if not is_completed:
                time.sleep(2)
                st.rerun()

        except Exception as e:
            st.error(f"获取任务状态失败: {e}")
