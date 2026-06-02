# 1. 项目一句话概述

本项目是一个计算机网络大作业 Demo：用 Coordinator 通过 A2A TCP 协议串联 Weather、Attraction、Hotel、Traffic 多个 Agent，每个 Agent 通过 MCP Gateway 调用对应 MCP Server 获取旅行数据，再结合规则或 LLM 生成结构化旅行规划结果。

# 2. 当前项目目录结构摘要

项目根目录是 `Agent-collaboration-network-on-A2A-MCP`。主要文件和目录如下：

- `coordinator.py`：核心调度器。提供 HTTP `/submit_task`、`/task_result`、`/tasks`、`/contracts`、`/health`，同时启动 A2A TCP 回调服务。当前依赖链固定为 `weather_agent -> attraction_agent -> hotel_agent -> traffic_agent`。
- `registry_center.py`：Agent 注册中心。Agent 启动时 POST `/register`，Coordinator 通过 GET `/discover` 发现 Agent。
- `mcp_gateway.py`：MCP JSON-RPC 网关。负责按 method 路由到后端 MCP Server，并实现 TTL cache、请求合并、限流、熔断和 metrics。
- `llm_client.py`：ModelScope/OpenAI-compatible LLM 客户端。读取 `.env` 中的 `A2A_LLM_BASE_URL`、`A2A_LLM_MODEL`、`MODELSCOPE_API_KEY` 等配置，支持 `chat()` 和 `chat_json()`。
- `common/config.py`：端口、Agent、MCP Server、Gateway、timeout 等运行配置。当前端口基本硬编码为 `127.0.0.1` 本地端口。
- `common/schemas.py`：A2A 任务 payload、结果 payload、状态常量、校验函数。
- `common/tcp_a2a.py`：4 字节长度前缀 + UTF-8 JSON 的 TCP A2A 协议实现。
- `common/http_client.py`：轻量 JSON-over-HTTP POST 客户端。
- `common/logger.py`：控制台和 JSONL 网络日志。
- `agents/base_agent.py`：Agent 通用基类，支持 HTTP/TCP 接收任务、注册、调用 MCP Gateway、调用 LLM、fallback、向 Coordinator 回传结果。
- `agents/weather_agent.py`：天气 Agent，当前规则生成天气约束，不走 LLM。
- `agents/attraction_agent.py`：景点 Agent，LLM 优先选择每日景点 ID，失败后规则 fallback。
- `agents/hotel_agent.py`：酒店 Agent，当前已有一次 LLM 选择住宿区域和酒店 ID 的流程，同时保留一批旧的两阶段 prompt/normalize 函数。
- `agents/traffic_agent.py`：交通 Agent，LLM 优先选择路线 ID，失败后规则 fallback。
- `mcp_servers/base_mcp_server.py`：通用 HTTP JSON-RPC MCP Server 框架。
- `mcp_servers/mock_data.py`：天气、景点、酒店、交通的本地 mock 数据和查询函数。
- `mcp_servers/weather_mcp_server.py`：暴露 `get_weather`。
- `mcp_servers/attraction_mcp_server.py`：暴露 `search_attractions`。
- `mcp_servers/hotel_mcp_server.py`：暴露 `search_hotels`。
- `mcp_servers/traffic_mcp_server.py`：暴露 `get_route`、`get_routes`、`get_transport`、`get_traffic`、`get_intercity_transport`。
- `scripts/start_all.py`：按顺序启动 registry、MCP servers、gateway、agents、coordinator。
- `scripts/demo_normal.py`：启动全链路并提交一次旅行规划请求。
- `scripts/demo_delay.py`：给 Weather MCP 加延迟，测试 timeout/partial/fallback。
- `scripts/demo_fault.py`：不启动 Weather MCP，测试故障场景。
- `scripts/demo_gateway_cache.py`：只启动相关 MCP/Gateway，演示 Gateway cache。
- `tests/test_a2a_tcp_protocol.py`：TCP framing、Coordinator TCP callback、rate limit、LLM fallback 单测。
- `README.md`：说明较旧，仍提到 v1 和部分过时结构，当前实际项目已经有 TCP A2A、MCP Gateway、Attraction/Hotel Agent。
- `.env`：存在 LLM 相关配置项。不要泄露其中值。

# 3. 运行方式与入口文件

依赖管理：

```bash
uv sync
```

也保留了 `requirements.txt`，但文件说明依赖以 `pyproject.toml`/`uv` 为准。

启动全部服务：

```bash
uv run python scripts/start_all.py
```

完整 demo：

```bash
uv run python scripts/demo_normal.py
```

故障/延迟/缓存 demo：

```bash
uv run python scripts/demo_delay.py
uv run python scripts/demo_fault.py
uv run python scripts/demo_gateway_cache.py
```

单独启动入口：

```bash
uv run python registry_center.py
uv run python mcp_servers/weather_mcp_server.py
uv run python mcp_servers/attraction_mcp_server.py
uv run python mcp_servers/hotel_mcp_server.py
uv run python mcp_servers/traffic_mcp_server.py
uv run python mcp_gateway.py
uv run python agents/weather_agent.py
uv run python agents/attraction_agent.py
uv run python agents/hotel_agent.py
uv run python agents/traffic_agent.py
uv run python coordinator.py
```

测试：

```bash
uv run python -m unittest tests/test_a2a_tcp_protocol.py
uv run python scripts/test_tcp_framing.py
```

`start_all.bat` 是旧版 Windows 启动脚本，只启动 Weather/Traffic MCP、Weather/Traffic Agent、Coordinator，没有 Registry、MCP Gateway、Attraction、Hotel，当前已经过时。

# 4. 核心数据流

用户向 Coordinator 提交：

```http
POST /submit_task
{
  "question": "...旅行规划自然语言问题...",
  "timeout": 120.0
}
```

Coordinator 的流程：

1. `extract_travel_task(question)` 先尝试 LLM 解析旅行任务，失败后规则 fallback。
2. `build_dependency_plan()` 生成依赖计划。
3. `CoordinatorState.create_task()` 创建 `TaskRecord`。
4. 按固定链路依次派发：
   - Weather：无上游依赖。
   - Attraction：输入 Weather 结果和天气约束。
   - Hotel：输入 Weather、Attraction、`daily_plan_skeleton`。
   - Traffic：输入 Weather、Attraction、Hotel、`hotel_plan`。

任务 payload 大致结构：

```json
{
  "source": "coordinator",
  "target": "weather_agent",
  "task_id": "...",
  "instruction": "原始用户问题",
  "context": {
    "node_id": "weather",
    "stage": "weather_analysis",
    "dependencies": [],
    "travel_task": {},
    "inputs": {}
  },
  "reply_to": "tcp://127.0.0.1:9001",
  "created_at": "..."
}
```

TCP A2A 外层 envelope 由 `common/tcp_a2a.py` 生成，包含 `version`、`type`、`trace_id`、`span_id`、`source`、`target`、`task_id`、`payload` 等字段。Agent 收到 `TASK_REQUEST` 后返回 `TASK_ACK`，异步处理完成后向 Coordinator 的 TCP `reply_to` 发送 `TASK_RESULT`。

Agent 调用 MCP：

- BaseAgent 默认通过 `call_mcp_server()` 向 MCP Gateway 发 JSON-RPC。
- Gateway 根据 method 路由到 `MCP_SERVERS`。
- MCP Server 使用 `base_mcp_server.py` 执行本地 handler。

JSON-RPC 大致结构：

```json
{
  "jsonrpc": "2.0",
  "id": "task_id",
  "method": "search_hotels",
  "params": {}
}
```

结果 payload 大致结构：

```json
{
  "source": "hotel_agent",
  "target": "coordinator",
  "task_id": "...",
  "status": "success",
  "result": "简短文本摘要",
  "error": null,
  "metadata": {
    "structured_result": {},
    "quality": {}
  }
}
```

最终 summary/report：

- `build_final_answer(question, snapshot)` 生成最终回答。
- 当前逻辑：如果 `A2A_DEMO_FAST` 开启或任务不是 completed，则走 `_fallback_final_answer()`；如果四个 Agent 全成功，则优先使用 `_grounded_final_answer()` 的确定性回答。
- 只有非全成功场景才可能调用最终 LLM summary prompt。
- `_build_clean_final_plan_payload()` 会剥离 Agent/MCP/debug 字段，只保留旅行事实，避免最终回答暴露内部工作流。

# 5. 各 agent 当前实现状态

## base_agent

提供通用能力：

- 注册到 Registry。
- 支持 HTTP `/execute_task` 和 TCP A2A 接收任务。
- TCP 模式是当前默认。
- 异步线程执行 `process_task()`。
- 默认 `process_task()`：调用 MCP、构造 prompt、调用 LLM，LLM 失败后 `build_fallback_answer()`。
- 支持 `A2A_DEMO_FAST` 跳过 LLM。
- 支持通过 TCP 或 HTTP 回传结果给 Coordinator。

## weather_agent

当前是规则型实现：

- 从 `context.travel_task` 或 `context.inputs.travel_task` 中取目的地、日期。
- 调用 Weather MCP 的 `get_weather`。
- 根据天气 condition 生成 `weather_constraints`。
- 不调用 LLM，`quality.llm_used` 恒为 False。
- 输出 `structured_result.weather_constraints`。

## attraction_agent

当前较完善：

- 调用 Attraction MCP 获取景点候选。
- 构造 `compact_spots`、`grouped_spots`、`spot_relations`。
- LLM 优先输出 `daily_spot_ids`。
- 对 LLM 输出做 normalize 和校验。
- LLM 失败或输出为空时使用规则 fallback。
- 输出 `daily_plan_skeleton`、`constraints_for_traffic`、`estimated_cost.ticket_total`、`rejected_spots`。
- `quality` 包含 `llm_used`、`llm_error`、`source`、`missing_fields`、`confidence`。

## hotel_agent

当前已有 LLM 参与，但结构和 attraction_agent 不完全一致。它会：

- 从 `context.inputs.daily_plan_skeleton` 或 attraction result metadata 中取每日景点计划。
- 根据每日区域构造 `area_options`。
- 对前 3 个区域分别调用 Hotel MCP `search_hotels`，合并酒店候选。
- 构造 `hotel_options_for_llm`。
- 一次 LLM 调用选择 `recommended_area_id` 和 `selected_hotel_id`。
- normalize LLM 输出，非法 ID 会局部 fallback。
- LLM 调用失败则规则 fallback。
- 输出 `hotel_plan` 和 `constraints_for_traffic`。

注意：文件后半部还保留 `_hotel_area_selection_prompt()`、`_hotel_choice_prompt()`、`_normalize_area_selection()`、`_normalize_hotel_plan()`、`_fallback_hotel_plan()` 等旧两阶段结构，但当前 `process_task()` 主流程没有使用它们。

## traffic_agent

当前较完善：

- 从 Attraction 结果取 `daily_plan_skeleton`。
- 从 Hotel 结果取 `hotel_plan`。
- 调用 Traffic MCP 获取城市间交通和市内路线候选。
- LLM 优先输出每段路线的 `selected_route_ids`。
- 对 LLM 输出做 normalize，非法 route_id 局部 fallback。
- LLM 失败时规则 fallback。
- 输出 `traffic_plan`、`traffic_summary`、`intercity_transport`。
- `quality` 包含 `llm_used`、`llm_error`、`source`、`confidence`。

整体成熟度：

- 较完善：`attraction_agent`、`traffic_agent`。
- 已有结构但可继续整理：`hotel_agent`。
- 主要 rule fallback：`weather_agent`。

# 6. hotel_agent 重点分析

hotel_agent 当前输入来自 Coordinator 的 hotel stage context：

- `context.travel_task`
- `context.inputs.travel_task`
- `context.inputs.weather_result`
- `context.inputs.weather_constraints`
- `context.inputs.attraction_result`
- `context.inputs.daily_plan_skeleton`
- `context.inputs.constraints_for_hotel`

hotel_agent 内部实际读取：

- `_extract_travel_task(context)`：取旅行任务。
- `_extract_weather_constraints(context)`：取天气约束。
- `_extract_daily_plan(context)`：优先取 `inputs.daily_plan_skeleton` 或 `inputs.daily_plan`，其次从 `attraction_result.metadata.structured_result.daily_plan/daily_plan_skeleton` 或 `metadata.daily_plan_skeleton` 取。

MCP 输入：

- `city`
- `days`
- `budget_level`
- `preferences`
- `target_area`
- `preferred_areas`
- `area_selection`
- `daily_plan`
- `requested_fields`

hotel_agent 输出：

- 顶层 `result`：简短酒店选择摘要。
- `metadata.workflow`：`area_options_hotel_mcp_then_single_llm_selector`
- `metadata.area_options_for_llm`
- `metadata.hotel_options_for_llm`
- `metadata.llm_hotel_selection`
- `metadata.recommended_area_id`
- `metadata.selected_hotel_id`
- `metadata.area_candidates`
- `metadata.area_selection`
- `metadata.mcp_result`
- `metadata.hotel_candidates`
- `metadata.travel_task`
- `metadata.hotel_constraints`
- `metadata.general_constraints`
- `metadata.weather_constraints`
- `metadata.daily_plan_skeleton`
- `metadata.structured_result`
- `metadata.hotel_plan`
- `metadata.selected_hotel`
- `metadata.hotel_area`
- `metadata.constraints_for_traffic`
- `metadata.quality`
- `metadata.llm_error`
- `metadata.elapsed_ms`

`structured_result` 当前结构：

```json
{
  "hotel_plan": {
    "recommended_area": "...",
    "area_reason": "...",
    "selected_hotel": {
      "name": "...",
      "area": "...",
      "price_per_night": "...",
      "nearest_subway": "...",
      "type": "..."
    },
    "hotel_reason": "...",
    "estimated_total_hotel_cost": "..."
  },
  "constraints_for_traffic": []
}
```

是否使用 LLM：

- 是。当前主流程调用 `llm.chat_json(_hotel_selector_prompt(...))`。
- LLM 输出 schema 期望包含：
  - `recommended_area_id`
  - `selected_hotel_id`
  - `reason`

fallback：

- LLM 调用失败：使用 `_fallback_hotel_selection()`。
- LLM 返回非法 `selected_hotel_id`：使用 `_fallback_hotel_id()`，并记录 `selection_errors`。
- LLM 返回非法 `recommended_area_id`：根据酒店 area 匹配 area_id，或 `_fallback_area_id()`。

`metadata.quality` 字段：

```json
{
  "llm_used": true/false,
  "llm_error": null 或错误文本,
  "source": "hotel_agent_llm_area_hotel_selector / hotel_agent_llm_area_hotel_selector_with_partial_fallback / hotel_agent_rule_fallback",
  "confidence": 0.9 或 0.76
}
```

和 attraction_agent 相比的差距：

- attraction_agent 有更完整的候选压缩、分组、关系构造和 must_visit/weather 约束融合；hotel_agent 目前只从 daily plan 统计区域，再查酒店候选。
- attraction_agent 的 fallback 会在 LLM 输出为空、扩展后为空时再次兜底；hotel_agent 对空 hotel_options 的处理较弱，可能产生空 ID 和“待确认”酒店。
- attraction_agent 的 metadata quality 有 `missing_fields`；hotel_agent 没有。
- attraction_agent 主流程比较集中，旧代码较少；hotel_agent 文件里保留了未使用的两阶段 prompt/normalize 代码，容易让后续维护者误解。
- hotel_agent 现在是一轮 LLM 同时选区域和酒店；旧代码暗示曾计划“两阶段：先选区域，再查酒店，再选酒店”。如果想做得更像 attraction_agent，应明确采用一种结构并删除或标注未使用函数。

如果要改成更像 attraction_agent 的“LLM 优先 + rule fallback”结构，主要改：

- `agents/hotel_agent.py`
  - 明确主流程：候选准备 -> LLM 输出 ID -> normalize -> expand -> fallback。
  - 增加 `compact_hotels` 或 `area/hotel relation` 类似结构。
  - 增加空候选处理。
  - 统一 `quality` 字段，建议加入 `missing_fields`。
  - 清理或注释未使用的旧两阶段函数。
- `mcp_servers/mock_data.py`
  - 如需更多酒店字段或更稳定筛选逻辑，改 `search_hotels()`。
- `mcp_servers/hotel_mcp_server.py`
  - 一般无需大改，只要 handler 仍为 `search_hotels`。
- `coordinator.py`
  - 一般无需改。只有当 hotel 输出 schema 改名时，需要同步 `extract_hotel_plan()`、`extract_hotel_constraints_for_traffic()` 和 `_build_clean_final_plan_payload()`。
- `tests/test_a2a_tcp_protocol.py` 或新增测试
  - 建议增加 hotel normalize/fallback 单元测试。

# 7. LLM 与 fallback 机制

LLM 配置：

- `.env` 中存在 `A2A_LLM_BASE_URL`、`A2A_LLM_MODEL`、`MODELSCOPE_API_KEY`。
- `llm_client.py` 使用 `python-dotenv` 加载。
- 不应在报告、代码或日志中泄露 API key 原文。

LLM 使用点：

- Coordinator：`extract_travel_task()` 用 LLM 解析自然语言旅行任务，失败后规则解析。
- Coordinator：最终 summary 在部分场景可能调用 LLM，但四 Agent 全成功时当前优先 deterministic grounded answer。
- BaseAgent 默认 process_task 支持 LLM，但当前四个具体 agent 多数 override。
- Attraction：LLM 选每日景点 ID。
- Hotel：LLM 选住宿区域 ID 和酒店 ID。
- Traffic：LLM 选路线 ID。
- Weather：当前不使用 LLM。

fallback：

- LLM 缺 key、超时、API 错误、返回非 JSON、返回非法 ID 时，各 Agent 基本都有规则 fallback。
- `A2A_DEMO_FAST=1` 会跳过部分 LLM，便于 demo。
- Gateway 有 timeout、rate limit、circuit breaker，但 MCP server 本身多为本地 mock，没有外部实时 API retry。

# 8. 当前 Git 状态

命令结果摘要：

- 当前分支：`yah/agents`
- `git status --short --branch`：`## yah/agents...origin/yah/agents`
- 工作区：干净，没有已修改未提交文件。
- `git diff --stat`：无输出。
- `git diff`：无输出。

最近 10 次提交：

```text
36c71c7 feat: optimize multi-agent travel planning workflow
fc3aeca merge main with TCP A2A and MCP gateway travel workflow
4290794 merge MCP gateway and integrate travel workflow
1a544f7 feat: add attraction and hotel agents and improve travel workflow
5f858de Merge pull request #5 from fyf-spec/fyf/coordinator
bdee5c7 feat: Add mcp_gatway & fix: scripts
0caba63 feat:unified TCP protocal format for registry and agent config, TCP port 9001
1cd9f2e feat:TCP protocal between coordinator and agents
58b33aa Merge pull request #4 from fyf-spec/xrx/register
3a88a61 docs: Update README
```

当前未发现明显未提交半成品。注意本报告生成后工作区会新增 `PROJECT_HANDOFF_FOR_CHATGPT.md`。

# 9. 当前已知问题

- 端口和 host 基本硬编码在 `common/config.py`，不利于多机器部署。
- `.env` 有真实 LLM 配置，报告和后续提交必须避免泄露值。
- README 和 `start_all.bat` 过时，尤其 bat 没有启动 Registry、MCP Gateway、Attraction、Hotel。
- 源码中很多中文字符串显示为乱码，疑似历史编码问题。运行逻辑可能仍可用，但报告、展示和最终输出可读性受影响。
- `DISPATCH_HTTP_TIMEOUT_SECONDS` 在配置中存在，但当前主链路是 TCP A2A。
- Gateway cache TTL 是全局 `cache_ttl_seconds=10.0`，没有按 method 区分。
- MCP 数据仍是 mock，本项目还没有真正实时数据接口。
- Agent metadata schema 不完全统一：Attraction 有 `missing_fields`，Traffic/Hotel 没有；Hotel 有 `workflow`、`area_options_for_llm` 等额外字段。
- Hotel Agent 文件里有未使用的旧两阶段代码，维护成本高。
- Hotel Agent 对空酒店候选、空 area_options 的结果较弱，可能输出“待确认”或空 ID。
- Coordinator 的等待逻辑给 Hotel/Traffic 额外等待窗口，适合 demo，但整体 timeout 语义比较复杂。
- Windows 兼容性尚可，但 `start_all.py` 用当前 Python 解释器启动；README 推荐 `uv run`。跨平台时端口占用和进程清理仍要注意。
- `scripts/demo_normal.py` 请求 timeout 60 秒，但完整 LLM 链路可能超过，尤其每个 agent 都可能 45 秒 LLM timeout。

# 10. 下一步建议修改计划

建议下一步聚焦 hotel_agent，不要先大改 Coordinator：

1. 先整理 `agents/hotel_agent.py` 主流程，明确采用“一次 LLM 同时选 area_id/hotel_id”还是“两阶段 LLM”。当前代码实际是一阶段。
2. 若保持一阶段，删除或标注未使用的 `_hotel_area_selection_prompt()`、`_hotel_choice_prompt()`、`_normalize_hotel_plan()` 等旧函数。
3. 为 Hotel 增加更像 Attraction 的候选压缩结构，例如 `compact_hotels`、`area_hotel_groups`。
4. 强化 fallback：空酒店候选、非法 ID、area 与 hotel 不一致时都返回稳定 schema。
5. 统一 metadata quality：加入 `missing_fields`，并保持 `llm_used`、`llm_error`、`source`、`confidence`。
6. 增加 hotel 单元测试：LLM 成功、LLM 非 JSON、非法 hotel_id、空候选。
7. 更新 README 和 `start_all.bat`，让文档与当前 TCP A2A + Gateway + 四 Agent 架构一致。
8. 后续再做实时数据接口时，优先从 MCP Server 后端替换 `mock_data`，不要改 A2A 主链路。

# 11. 给 ChatGPT 的重点阅读文件列表

建议 ChatGPT 按这个顺序读：

1. `common/config.py`
2. `common/schemas.py`
3. `common/tcp_a2a.py`
4. `coordinator.py`
5. `agents/base_agent.py`
6. `agents/weather_agent.py`
7. `agents/attraction_agent.py`
8. `agents/hotel_agent.py`
9. `agents/traffic_agent.py`
10. `mcp_gateway.py`
11. `mcp_servers/base_mcp_server.py`
12. `mcp_servers/mock_data.py`
13. `mcp_servers/hotel_mcp_server.py`
14. `scripts/start_all.py`
15. `tests/test_a2a_tcp_protocol.py`
16. `llm_client.py`

# 12. 五分钟接手版摘要

现在项目已经不是早期 HTTP demo，而是 TCP A2A + Registry + MCP Gateway + 四 Agent 的旅行规划系统。Coordinator 固定按 Weather -> Attraction -> Hotel -> Traffic 执行，最后生成旅行方案。

已经完成较多的是 Attraction 和 Traffic：二者都有“LLM 选 ID + 校验 + 规则 fallback + structured_result”。Weather 主要是规则生成天气约束，不用 LLM。

Hotel Agent 不是空白，它已经有一次 LLM 调用：从行程区域和酒店候选中选择 `recommended_area_id`、`selected_hotel_id`，失败后规则 fallback。但它还不如 Attraction 干净：文件里有未使用的旧两阶段代码，metadata schema 和 quality 字段也不完全统一，空候选处理需要加强。

下一步最建议改 `agents/hotel_agent.py`：整理主流程，统一输出 schema，补强 fallback，增加 `missing_fields`，加 hotel 测试。除非改 hotel 输出字段，否则 Coordinator 基本不用动。
