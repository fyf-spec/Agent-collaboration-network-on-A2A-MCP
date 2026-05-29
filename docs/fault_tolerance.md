# 分布式网络容错改动与测试说明

本文档说明本次针对“分布式网络容错”评分点的代码改动、运行方式和预期测试结果。目标是验证：

- Agent 的网络请求均设置超时机制。
- MCP Server 宕机或网络拥堵时，Agent 能向 Coordinator 返回标准错误报文。
- Coordinator 能及时处理节点下线、超时或错误结果，避免系统卡死。

## 1. 本次改动概览

### 1.1 标准错误结果报文

新增文件位置：

```text
common/schemas.py
```

新增 `build_error_result_payload()`，用于统一生成 Agent -> Coordinator 的错误结果报文。报文仍沿用原有 A2A result payload 结构，但在 `metadata.error_report` 中加入 HTTP 500 概念：

```json
{
  "source": "weather_agent",
  "target": "coordinator",
  "task_id": "<task_id>",
  "status": "error",
  "result": null,
  "error": "MCP JSON-RPC error: ...",
  "metadata": {
    "agent": "weather_agent",
    "capability": "weather",
    "elapsed_ms": 2282.54,
    "error_code": "agent_execution_failed",
    "http_status": 500,
    "error_report": {
      "code": "agent_execution_failed",
      "http_status": 500,
      "message": "MCP JSON-RPC error: ..."
    }
  }
}
```

已接入的 Agent：

```text
agents/base_agent.py
agents/weather_agent.py
agents/attraction_agent.py
agents/hotel_agent.py
agents/traffic_agent.py
```

当 MCP 调用失败、MCP Gateway 返回 JSON-RPC error、网络超时或 Agent 内部执行异常时，Agent 不会静默失败，而是回调 Coordinator，并上报 `status: "error"`。

### 1.2 HTTP 请求超时与异常捕获

修改文件：

```text
common/http_client.py
```

`post_json()` 原本已经要求调用方显式传入 `timeout`，本次补强了异常捕获范围：

- `TimeoutError`
- `socket.timeout`
- `urllib.error.URLError`
- `OSError`

因此 MCP Gateway 宕机、MCP Server 宕机、连接被拒绝、网络读超时等情况都会被转换为 `HttpJsonClientError`，再由 Agent 或 Gateway 转成标准错误报文。

关键超时配置在：

```text
common/config.py
```

```python
MCP_HTTP_TIMEOUT_SECONDS = 3.0
A2A_TCP_TIMEOUT_SECONDS = 3.0
MCP_GATEWAY["upstream_timeout_seconds"] = 2.5
```

含义：

- Agent 调用 MCP Gateway 最多等待 3 秒。
- Gateway 调用真实 MCP Server 最多等待 2.5 秒。
- Coordinator 与 Agent 的 TCP A2A 请求最多等待 3 秒。

### 1.3 TCP 入站连接超时

修改文件：

```text
agents/base_agent.py
coordinator.py
```

Agent 和 Coordinator 的 TCP handler 均增加了 socket timeout：

```python
self.request.settimeout(A2A_TCP_TIMEOUT_SECONDS)
```

这可以防止对端建立 TCP 连接后迟迟不发送完整 frame，导致服务端线程长期卡住。

### 1.4 Coordinator 节点下线与超时处理

修改文件：

```text
coordinator.py
```

Coordinator 现在区分三类失败：

```text
1. dispatch 阶段失败
   例如 Agent 未启动、TCP 连接被拒绝、ACK 非法。
   处理方式：写入 record.dispatch_errors[target]。

2. Agent 已 ACK，但迟迟不回调 TASK_RESULT
   例如 Agent 内部卡住或网络极度拥堵。
   处理方式：wait_for_target() 超时后写入 dispatch_errors[target]。

3. Agent 正常回调错误结果
   例如 MCP Server 宕机，Agent 返回 status="error"。
   处理方式：写入 record.results[target]，状态为 error。
```

如果部分 Agent 成功、部分 Agent 失败，最终任务状态为：

```text
partial
```

如果全部 Agent 失败，最终任务状态为：

```text
failed
```

如果全部 Agent 成功，最终任务状态为：

```text
completed
```

### 1.5 用户 timeout 不再被阶段等待放大

修改文件：

```text
coordinator.py
```

之前 `_stage_wait_seconds()` 会用 75/100/150 秒这样的阶段下限，可能导致用户提交：

```json
{"timeout": 20}
```

但实际等待远超 20 秒。现在阶段等待严格受 `/submit_task` 的总 timeout 约束，防止系统在故障场景下长时间卡住。

## 2. 推荐测试命令

略

## 3. 单元测试

运行：

```powershell
python -m unittest discover
```

预期结果：

```text
...........
----------------------------------------------------------------------
Ran 11 tests in <约 2s>

OK
```

当前测试覆盖：

- TCP frame 不粘包、不截断。
- TCP 分片 frame 可重组。
- A2A envelope 字段校验。
- Coordinator 收到合法 `TASK_RESULT` 后返回 `RESULT_ACK`。
- 非法 result source 返回 `ERROR` frame。
- TCP rate limit / error frame 可被 Coordinator 记录。
- LLM rate limit 可降级为 Agent fallback result。
- `wait_for_target()` 等待回调超时后写入 `dispatch_errors`。
- `wait_for_task()` 总等待超时后把剩余节点标为失败。
- `_stage_wait_seconds()` 不再超过用户工作流 deadline。
- Agent 执行失败时返回标准 `status="error"`、`http_status=500` 报文。

## 4. 语法检查

运行：

```powershell
python -m py_compile common\schemas.py common\http_client.py coordinator.py agents\base_agent.py agents\weather_agent.py agents\hotel_agent.py agents\traffic_agent.py agents\attraction_agent.py tests\test_a2a_tcp_protocol.py
```

预期结果：

```text
无输出，退出码为 0
```

如果有语法错误，PowerShell 会输出具体文件和行号。

## 5. MCP Server 宕机测试

运行：

```powershell
$env:MODELSCOPE_API_KEY=''
python scripts\demo_fault.py
```

该脚本会启动除 Weather MCP Server 之外的服务，相当于模拟：

```text
weather_mcp_server 宕机
```

预期关键链路：

```text
weather_agent -> mcp_gateway -> weather_mcp_server
```

由于 Weather MCP 未启动，Gateway 会在短时间内收到连接失败：

```text
gateway_mcp_failed
error_type: ConnectionRefusedError
```

随后 Weather Agent 应回调 Coordinator：

```text
weather_agent -> coordinator
status: error
metadata.error_report.http_status: 500
```

用户侧预期输出重点：

```text
HTTP Status Code: 200
Task Status: partial

Answers of Agents:
- weather_agent: error
- attraction_agent: success
- hotel_agent: success
- traffic_agent: success
```

说明：

- Weather MCP 宕机不会让系统卡死。
- Weather Agent 会返回标准错误结果。
- Coordinator 会继续使用其他 Agent 的成功结果生成降级方案。
- 最终任务不是 `completed`，而是 `partial`。

一次实际验证中，整个请求约 5 秒返回；Weather MCP 连接失败约 2.3 秒被处理。

## 6. 网络拥堵 / 上游超时测试

运行：

```powershell
$env:MODELSCOPE_API_KEY=''
python scripts\demo_delay.py
```

该脚本会给 Weather MCP Server 加人工延迟：

```text
weather_mcp_server --delay 5.0
```

而 Gateway 调用 MCP Server 的上游超时为：

```text
MCP_GATEWAY["upstream_timeout_seconds"] = 2.5
```

预期结果：

```text
gateway_mcp_failed 或 agent_mcp_failed
weather_agent: error
Task Status: partial
```

说明：

- Weather MCP 极慢时，Gateway 不会无限等待。
- Agent 不会无限等待 Gateway。
- Coordinator 不会无限等待 Weather Agent。
- 系统会按 timeout 降级返回。

## 7. Agent 节点下线测试

Agent 未启动时，Coordinator 在 dispatch 阶段会通过 TCP 超时或连接失败发现节点不可用。

典型错误会进入：

```json
{
  "dispatch_errors": {
    "weather_agent": "TCP request failed to tcp://127.0.0.1:9010: ..."
  }
}
```

预期任务状态：

```text
如果至少一个其他 Agent 成功：partial
如果全部 Agent 都不可用：failed
```

这类错误不会出现在 `results` 中，因为任务还没有被 Agent 正常 ACK；它会出现在 `dispatch_errors` 中。

## 8. Agent ACK 后不回调测试

如果 Agent 已经返回 `TASK_ACK`，但后续因为内部卡住或网络拥堵一直不回调 `TASK_RESULT`，Coordinator 会在 `wait_for_target()` 超时后写入：

```json
{
  "dispatch_errors": {
    "weather_agent": "weather_agent timed out after <N>s waiting for result callback"
  }
}
```

这由单元测试覆盖：

```text
CoordinatorTimeoutTests.test_wait_for_target_marks_callback_timeout_as_dispatch_error
```

预期行为：

- Coordinator 不会永久等待。
- 该节点被视为本轮任务的失败节点。
- 如果迟到结果之后到达，`add_result()` 会用新结果覆盖旧的 `dispatch_errors`，任务状态可被刷新。

## 9. 结果判定标准

满足评分点时，应能观察到以下现象：

| 场景 | 预期结果 |
| --- | --- |
| MCP Server 宕机 | Gateway/Agent 在数秒内失败，Agent 回调 `status="error"` |
| MCP Server 极慢 | 上游调用按 timeout 失败，不无限等待 |
| Agent 未启动 | Coordinator 写入 `dispatch_errors[target]` |
| Agent ACK 后不回调 | Coordinator 等待超时后写入 `dispatch_errors[target]` |
| 部分节点失败 | 任务状态为 `partial`，系统继续返回降级结果 |
| 全部节点失败 | 任务状态为 `failed`，HTTP 状态可能为 `504 Gateway Timeout` |
| 全部节点成功 | 任务状态为 `completed` |

## 10. 关键文件清单

```text
common/schemas.py
  build_error_result_payload()

common/http_client.py
  post_json(..., timeout=...)

common/tcp_a2a.py
  request_frame(..., timeout=...)
  recv_exact()

agents/base_agent.py
  AgentA2ATCPRequestHandler.handle()
  BaseAgent.process_task()
  BaseAgent.call_mcp_server()
  BaseAgent.send_result_to_coordinator()

agents/weather_agent.py
agents/attraction_agent.py
agents/hotel_agent.py
agents/traffic_agent.py
  各业务 Agent 的错误回调路径

mcp_gateway.py
  Gateway -> MCP Server 的 timeout、circuit breaker、JSON-RPC error

coordinator.py
  dispatch_to_agent()
  wait_for_target()
  wait_for_task()
  _stage_wait_seconds()

tests/test_a2a_tcp_protocol.py
  容错相关单元测试
```

## 11. 一句话结论

本次改动后，系统在 MCP Server 宕机、上游拥堵、Agent 下线、Agent ACK 后不回调等情况下，都会在有限时间内进入可观测的错误状态，并由 Coordinator 汇总为 `partial` 或 `failed`，不会因为单个节点故障导致整体卡死。
