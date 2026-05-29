import streamlit as st
import requests
import time

COORDINATOR_URL = "http://127.0.0.1:9000/submit_task"

st.set_page_config(page_title="A2A 旅行工作流 Agent", page_icon="✈️", layout="centered")

st.title("✈️ A2A 旅行工作流 Agent")
st.markdown("输入您的旅行需求，Coordinator 将根据存活的 Agents 动态分析依赖，为您完成规划。")

st.markdown("""
**本示例由以下 Agent 共同协作完成:**
* ⛅ **Weather Agent** (查询目的地多日天气)
* 🎒 **Packing Agent** (根据天气动态生成行李清单，与 Attraction 完美并行)
* 🎢 **Attraction Agent** (基于天气和用户偏好，挑选并安排每日景点)
* 🏨 **Hotel Agent** (依据每日游玩路线区域，规划最方便的住宿)
* 🚗 **Traffic Agent** (串联所有位置，输出市内的通勤路线和跨城交通)
""")

query = st.text_area(
    "请描述您的旅行需求：", 
    value="中秋节假期从上海去北京玩3天，要求穷游并且尽量乘坐地铁，必须去故宫看看。"
)

if st.button("提交 / Submit", type="primary"):
    if not query.strip():
        st.warning("请输入您的旅行需求。")
    else:
        with st.status("正在调度多 Agent 工作流，请稍等...", expanded=True) as status:
            start_time = time.time()
            st.write("已将您的描述发送到 Coordinator...")
            try:
                response = requests.post(
                    COORDINATOR_URL, 
                    json={"question": query, "timeout": 300, "async": True}, 
                    timeout=310
                )
                response.raise_for_status()
                data = response.json()
                task = data.get("task", {})
                task_id = task.get("task_id")
                
                # 初始显示 Coordinator 规划（DAG）
                st.write("✅ Coordinator 已完成初始调度网络规划！")
                plan = task.get("plan", {})
                if plan:
                    with st.expander("📝 阶段 1：Coordinator 动态规划的工作流和分配情况 (DAG)", expanded=True):
                        st.json(plan)
                        
                st.markdown("### 🤖 阶段 2：各个 Worker Agent 执行动态")
                
                # 创建占位容器
                agent_containers = {}
                final_answer_container = st.empty()
                
                # 轮询获取任务状态
                while True:
                    time.sleep(2)
                    poll_resp = requests.get(f"http://127.0.0.1:9000/tasks?task_id={task_id}")
                    poll_resp.raise_for_status()
                    task = poll_resp.json().get("task", {})
                    
                    results = task.get("results", {})
                    
                    # 增量展现刚获得结果的 Agent
                    for agent_name, result_data in results.items():
                        if agent_name not in agent_containers:
                            agent_containers[agent_name] = st.empty()
                            status_emoji = "✅" if result_data.get("status") == "success" else "❌"
                            with agent_containers[agent_name].container():
                                with st.expander(f"{status_emoji} {agent_name} 的执行报告", expanded=True):
                                    st.json(result_data)
                                    
                    # 检查是否完成
                    if task.get("status") in ["completed", "failed", "partial"]:
                        final_answer = task.get("final_answer")
                        if final_answer:
                            with final_answer_container.container():
                                st.markdown("---")
                                st.markdown("### 🌟 阶段 3：最终旅行方案")
                                st.success("多智能体系统已组合生成了这一份行程报告！", icon="🎉")
                                st.markdown(final_answer)
                        status.update(label=f"整个工作流执行结束！总耗时: {time.time() - start_time:.1f} 秒", state="complete", expanded=False)
                        break

            except Exception as e:
                status.update(label="工作流执行失败", state="error")
                st.error(f"报错信息: {e}")
                st.stop()