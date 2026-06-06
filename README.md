# Agent-A2A

本仓库是一个面向计算机网络课程展示的本地多智能体协作系统 Demo。系统围绕“旅行规划”任务，展示 Coordinator、动态 Agent、MCP Gateway、MCP Server、主备注册中心之间的服务发现、任务调度、TCP A2A 通信、HTTP/JSON-RPC 调用、故障降级和网络报文观察。

当前版本已经不是早期的纯 HTTP v1 Demo，核心链路包括：

- User 通过 HTTP 向 Coordinator 提交任务。
- Coordinator 通过 HTTP/REST 向主/备 Registry 发现可用 Agent。
- Coordinator 通过自定义 A2A TCP frame 向 Worker Agent 派发任务。
- Worker Agent 通过 MCP Gateway 调用后端 MCP Server。
- MCP Gateway 通过 HTTP/JSON-RPC 转发到 Weather、Traffic、Attraction、Hotel、Packing 等 MCP Server。
- Coordinator 汇总各 Agent 结果，生成完整或 partial 的最终回答。
- Streamlit UI 实时展示拓扑、节点启停、传输流动、失败链路、最近网络事件和完整协议内容。

## 快速启动 UI Demo

推荐使用 `uv` 管理环境。

```powershell
uv sync
$env:A2A_DEMO_FAST="1"
$env:PYTHONIOENCODING="utf-8"
uv run streamlit run scripts/demo_ui.py --server.port 8504
```

打开：

```text
http://localhost:8504
```

如果不指定端口，Streamlit 会使用默认端口。演示时建议固定使用 `8504`，避免和其他 Streamlit 页面混淆。

如果需要模型能力，请在本地 `.env` 中配置：

```text
A2A_LLM_BASE_URL=https://api-inference.modelscope.cn/v1
A2A_LLM_MODEL=deepseek-ai/DeepSeek-V4-Flash
MODELSCOPE_API_KEY=your_api_key_here
```

不要把真实 API Key 提交到仓库。

## 消融实验 UI 快速启动

```bash
uv run streamlit run scripts/ablation_ui.py --server.port 8505 --server.headless true
```


## UI Demo 展示流程

### 1. 启动所有节点

在左侧“启停控制”中点击“启动所有节点”。启动后拓扑图中的节点右下角状态点会变为绿色。

建议先说明拓扑结构：

- `User -> Coordinator`：用户通过 HTTP 提交旅行任务。
- `Coordinator -> Primary/Backup Registry`：通过 `HTTP/REST` 做服务发现。
- `Coordinator -> Agent Pool`：通过自定义 `A2A TCP` 协议派发任务。
- `Agent Pool -> MCP Gateway`：Agent 调用统一网关。
- `MCP Gateway -> MCP Servers`：通过 `HTTP/JSON-RPC` 请求各领域 MCP 服务。

拓扑图的展示规则：

- 绿色角标表示节点正在运行。
- 红色角标表示节点停止。
- 单击服务节点可启动或停止该节点。
- 普通传输链路显示绿色/黄色/青色渐变流动。
- 失败请求显示红色虚线和红色高亮。
- Agent 池表示动态部署层，展示在 Coordinator 与 Gateway 之间。

### 2. 正常工作流展示

在右侧“旅行任务交互”中输入示例问题：

```text
中秋节假期从上海去北京玩3天，要求穷游并且尽量乘坐地铁，必须去故宫看看。
```

点击“提交任务 / Submit”。

展示重点：

- 阶段 1：Coordinator 生成工作流 DAG，决定需要哪些 Agent。
- 阶段 2：各 Worker Agent 执行任务，分别完成天气、景点、住宿、交通、行李等子任务。
- 阶段 3：Coordinator 汇总结果，输出最终旅行方案。
- 拓扑图上可以看到 Coordinator、Agent、Gateway、MCP Server 之间的实时传输动画。

### 3. 最近网络事件与网络报文

左侧有两个并列展示框：

- “最近网络事件”：以表格形式列出 event、protocol、operation、source、target、method、url。
- “网络报文”：每一行对应一条网络事件，单击后弹出“完整协议内容”窗口。

“完整协议内容”窗口会展示：

- 协议分层：应用层、表示层、传输层、端点。
- 完整协议：例如 HTTP request/response，或 A2A TCP length-prefix JSON frame。
- Payload：业务载荷。
- 原始事件：日志中的完整 JSON 字段。

这一部分适合从计算机网络课程角度讲解：

- 应用层协议如何映射到具体 URL、method 和 payload。
- TCP 字节流为什么需要 length-prefix frame 解决消息边界问题。
- JSON-RPC 请求和响应如何通过 Gateway 转发。
- 失败请求如何在事件日志和拓扑链路上体现。

### 4. 主备注册中心容错展示

先保持所有节点运行，然后在拓扑图中单击 `Primary Reg.` 关闭主注册中心。

再次提交任务后观察：

- `Coordinator -> Primary Reg.` 链路出现红色失败传输。
- Coordinator 自动访问 `Backup Reg.`。
- 任务仍可继续执行。
- “最近网络事件”中会出现 `registry_discover_error` 和备用注册中心的 `registry_discover_response`。
- “网络报文”中可以点开对应事件，查看 `HTTP/REST` 请求和失败原因。

### 5. Agent 或 MCP 故障降级展示

可以在拓扑图中关闭某个 Agent 或 MCP Server，例如：

- 关闭 `Attract` Agent。
- 关闭 `Packing` Agent。
- 关闭 `Traffic` MCP。

再次提交任务后观察：

- 对应链路出现红色失败传输。
- 最终状态可能变为 `partial`。
- partial 回答会诚实总结：
  - 已获取哪些信息。
  - 哪些 Agent 或 MCP 调用失败。
  - 能给出的最低限度简短回答。

这部分用于展示分布式系统中的故障隔离、超时处理和降级输出。

### 6. 延迟注入展示

左侧“MCP 延迟注入”可以为 MCP 节点设置启动时延迟。设置后需要先停止对应 MCP，再重新启动该 MCP。

展示建议：

1. 给 `Weather MCP` 设置较大 delay。
2. 重启 `Weather MCP`。
3. 提交任务。
4. 观察 Agent 调用 MCP 超时、拓扑失败链路、网络事件和 partial 回答。

## 命令行 Demo

除了 UI，也可以运行脚本复现实验场景。

```powershell
uv run python scripts/start_all.py
```

启动全部本地服务，按 `Ctrl+C` 停止。

```powershell
uv run python scripts/demo_normal.py
```

正常旅行规划链路。

```powershell
uv run python scripts/demo_fault.py
```

跳过某个 MCP Server，验证 MCP 故障后的 partial 输出。

```powershell
uv run python scripts/demo_mcp_delay.py
```

注入 MCP 响应延迟，验证 Agent 内部超时和降级。

```powershell
uv run python scripts/demo_backup_registry.py
```

模拟主注册中心宕机，验证自动切换到备用注册中心。

```powershell
uv run python scripts/demo_ack_delay.py
```

模拟 Agent A2A TCP ACK 延迟，验证 Coordinator 派发超时。

```powershell
uv run python scripts/demo_gateway_cache.py
```

单独演示 MCP Gateway 缓存命中和 metrics。

```powershell
uv run python scripts/test_tcp_framing.py
```

验证 TCP length-prefix frame 可以处理连续帧和分片帧。

## 端口说明

```text
Registry Primary       7000
Registry Backup        7001
Coordinator HTTP       9000
Coordinator A2A TCP    9001
MCP Gateway            8100
Weather MCP            8001
Traffic MCP            8002
Attraction MCP         8003
Hotel MCP              8004
Packing MCP            8005
Weather Agent          9010
Traffic Agent          9020
Attraction Agent       9030
Hotel Agent            9040
Packing Agent          9060
Streamlit UI           8504
```

## 代码仓库结构

```text
Agent-A2A/
├── coordinator.py
│   └── Coordinator HTTP API、A2A TCP Server、任务 DAG、结果汇总、Registry 发现
├── registry_center.py
│   └── 主/备注册中心，维护 Agent 注册信息和 /discover 接口
├── mcp_gateway.py
│   └── MCP 统一网关，负责 JSON-RPC 转发、缓存、限流、熔断和 metrics
├── llm_client.py
│   └── ModelScope/OpenAI-compatible LLM 客户端
├── agents/
│   ├── base_agent.py
│   ├── weather_agent.py
│   ├── traffic_agent.py
│   ├── attraction_agent.py
│   ├── hotel_agent.py
│   └── packing_agent.py
├── mcp_servers/
│   ├── base_mcp_server.py
│   ├── weather_mcp_server.py
│   ├── traffic_mcp_server.py
│   ├── attraction_mcp_server.py
│   ├── hotel_mcp_server.py
│   ├── packing_mcp_server.py
│   └── mock_data.py
├── common/
│   ├── config.py
│   ├── http_client.py
│   ├── logger.py
│   ├── schemas.py
│   ├── tcp_a2a.py
│   └── prompt_templtes.py
├── scripts/
│   ├── demo_ui.py
│   ├── start_all.py
│   ├── demo_normal.py
│   ├── demo_fault.py
│   ├── demo_mcp_delay.py
│   ├── demo_backup_registry.py
│   ├── demo_ack_delay.py
│   ├── demo_gateway_cache.py
│   ├── test_tcp_framing.py
│   ├── topology_component/
│   │   └── index.html
│   └── packet_component/
│       └── index.html
├── docs/
│   ├── a2a_tcp_contract.md
│   ├── fault_tolerance.md
│   ├── mcp_gateway.md
│   ├── Proposal.docx
│   └── 计算机网络大作业2026.pdf
├── tests/
│   ├── __init__.py
│   └── test_a2a_tcp_protocol.py
├── logs/
│   └── demo_log.jsonl
├── start_all.bat
├── requirements.txt
├── pyproject.toml
├── uv.lock
└── README.md
```

## 核心协议与事件

### A2A TCP

`common/tcp_a2a.py` 在 TCP 字节流上定义 length-prefix JSON frame：

```text
[4-byte big-endian length][UTF-8 JSON envelope]
```

常见 frame type：

```text
TASK_REQUEST   Coordinator -> Agent，派发任务
TASK_ACK       Agent -> Coordinator，确认收到任务
TASK_RESULT    Agent -> Coordinator，返回执行结果
RESULT_ACK     Coordinator -> Agent，确认收到结果
ERROR          任意一方返回协议错误或拒绝
```

### HTTP/REST

Coordinator 通过 Registry 的 `/discover` 接口发现可用 Agent。UI 拓扑图中该链路标注为 `HTTP/REST`。

### HTTP/JSON-RPC

MCP Gateway 和 MCP Server 之间使用 HTTP 承载 JSON-RPC 请求。UI 拓扑图中该链路标注为 `HTTP/JSON`，网络报文详情中会展示 JSON-RPC 字段。

### 事件日志

所有网络事件写入：

```text
logs/demo_log.jsonl
```

常见事件：

```text
submit_task
registry_discover_request
registry_discover_response
registry_discover_error
dependency_dispatch_<agent>
dispatch_response
dispatch_failed
gateway_jsonrpc_request
gateway_call_mcp
mcp_jsonrpc_request
gateway_mcp_response
gateway_mcp_failed
agent_callback_result
agent_callback_response
task_result
task_result_failed
dependency_workflow_finished
```

UI 中“最近网络事件”和“网络报文”均从该日志读取。

## 开发与测试

```powershell
uv sync
uv run python -m unittest tests.test_a2a_tcp_protocol
uv run python scripts/test_tcp_framing.py
```

如果只是检查单个文件语法：

```powershell
python -m py_compile scripts/demo_ui.py
python -m py_compile coordinator.py
```

## Git 工作流

```powershell
git checkout main
git pull

git checkout feature/your-name
git pull origin main

git status
git add .
git commit -m "type: brief description"
git push
```

提交信息建议：

```text
feat: 新增功能
fix: 修复问题
docs: 文档更新
chore: 工程配置或杂项修改
refactor: 代码重构
test: 测试相关修改
```

## 注意事项

- `.env` 中的真实 API Key 不应提交。
- `logs/`、临时 Streamlit 日志和本地缓存文件不应作为核心代码提交。
- UI 节点启停依赖本机端口状态；如果端口被外部进程占用，先结束占用进程再启动。
- Windows PowerShell 中建议设置 `$env:PYTHONIOENCODING="utf-8"`，避免中文输出乱码。

## Realtime MCP Data Source

MCP servers now support realtime providers first, with automatic mock fallback. Weather uses Open-Meteo forecasts after AMap geocoding; attractions, hotels, and routes use AMap Web Service. Existing MCP method names are unchanged:

```text
get_weather
search_attractions
search_hotels
get_route / get_routes / get_transport
```

Configure `.env` locally:

```env
A2A_REALTIME_MCP_ENABLED=true
AMAP_WEB_KEY=your_amap_web_service_key
AMAP_API_BASE_URL=https://restapi.amap.com
OPEN_METEO_API_BASE_URL=https://api.open-meteo.com
OPEN_METEO_MAX_FORECAST_DAYS=16
MCP_REALTIME_TIMEOUT_SECONDS=5
MCP_REALTIME_FALLBACK_TO_MOCK=true
MCP_HTTP_TIMEOUT_SECONDS=8
MCP_TRAFFIC_REALTIME_ENABLED=true
MCP_TRAFFIC_MAX_WORKERS=4
MCP_TRAFFIC_ROUTE_TIMEOUT_SECONDS=1.5
MCP_TRAFFIC_MAX_SEGMENTS=8
MCP_GATEWAY_UPSTREAM_TIMEOUT_SECONDS=8
```

Do not commit `.env` or API keys. The code does not print the key.

Run the realtime MCP smoke test:

```bash
uv run python scripts/test_realtime_mcp.py
```

Check whether realtime data was used through `data_source` in MCP results:

```json
{
  "provider": "open-meteo",
  "realtime": true,
  "fallback_used": false,
  "fetched_at": "...",
  "missing_fields": []
}
```

If `AMAP_WEB_KEY` is missing, the network times out, a realtime provider returns an error, or the response cannot be normalized, the MCP server falls back to `mcp_servers/mock_data.py`:

```json
{
  "provider": "mock",
  "realtime": false,
  "fallback_used": true,
  "fallback_reason": "..."
}
```

Traffic realtime support is intentionally limited to route planning in this first version. `get_transport/get_traffic` still use mock data and mark `fallback_reason=transport_status_not_implemented`.

For realtime traffic demos and concurrent tasks, keep both `MCP_HTTP_TIMEOUT_SECONDS=8` and `MCP_GATEWAY_UPSTREAM_TIMEOUT_SECONDS=8` so Agents and the Gateway have enough time for realtime AMap calls. Route segment requests still use short per-segment timeouts and partial mock fallback; cache is intentionally not implemented in the AMap client or MCP servers because Gateway-level caching will be handled separately.

Weather uses Open-Meteo forecast mode when the travel task has multiple days, and `weather_agent` builds per-day `weather_by_day` constraints from `forecast_days`. Open-Meteo is capped at `OPEN_METEO_MAX_FORECAST_DAYS` days; farther future trips return a structured "too far to forecast accurately" weather result and ask the user to recheck near departure.

AMap POI responses do not reliably provide ticket price, visit duration, opening hours, reservation requirement, indoor/outdoor classification, nearest subway, hotel price, hotel pros, or hotel cons. Realtime normalizers keep those existing fields and fill missing values with `None` or empty lists. For fixed demo cities, `mcp_servers/enrichment/*.json` supplements known attraction and hotel fields from local profiles. Enriched results are marked with `provider=amap+local_profile` and `data_source.field_sources`; fields still unavailable remain in `data_source.missing_fields`.
