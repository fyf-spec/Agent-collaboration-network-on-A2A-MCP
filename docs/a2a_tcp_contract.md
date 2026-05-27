# A2A TCP Contract

本文档说明当前项目中 Coordinator 与 Agent 之间的 A2A TCP 协议格式、收发流程和协作注意事项。

## 通信边界

当前系统不是全量改成 TCP，而是只把 A2A 数据面切换到 TCP：

```text
User -> Coordinator: HTTP JSON
Coordinator -> Agent: TCP A2A
Agent -> Coordinator: TCP A2A
Agent -> MCP Server: HTTP JSON-RPC
Agent -> Registry Center: HTTP JSON
```

Coordinator 的 `/submit_task`、`/health`、`/tasks`、`/contracts` 仍然是 HTTP，因为这些是用户入口和管理/观测接口，不是 Agent 间 A2A 数据面。

## TCP Wire Format

TCP 是字节流，没有天然消息边界。本项目在 TCP 之上定义 Length-Prefix JSON frame：

```text
+----------------------+-------------------------------+
| Length: 4 bytes      | JSON Payload: variable length |
+----------------------+-------------------------------+
```

规则：

- `Length` 是 4 字节无符号大端序整数，表示后续 JSON body 的字节数。
- JSON body 使用 UTF-8 编码。
- 接收端必须先 `recv_exact(4)` 读取长度，再循环 `recv_exact(length)` 读取完整 body。
- 单个 frame 最大长度由 `common.tcp_a2a.MAX_FRAME_BYTES` 限制，当前为 4 MiB。

对应实现：

```text
common/tcp_a2a.py
  send_frame()
  recv_frame()
  recv_exact()
  request_frame()
```

## A2A Envelope

每个 TCP frame 的 JSON body 都是一个 A2A envelope：

```json
{
  "version": "1.0",
  "type": "TASK_REQUEST",
  "trace_id": "trace-<task_id>",
  "span_id": "span-coordinator-dispatch-weather_agent",
  "parent_span_id": null,
  "source": "coordinator",
  "target": "weather_agent",
  "task_id": "<task_id>",
  "deadline_ms": 3000,
  "payload": {}
}
```

字段说明：

```text
version         协议版本，当前固定为 "1.0"
type            frame 类型
trace_id        端到端链路追踪 ID
span_id         当前通信跨度 ID
parent_span_id  父跨度 ID，可为空
source          发送方名称
target          接收方名称
task_id         任务 ID，全链路保持一致
deadline_ms     期望 deadline，毫秒，可为空
payload         业务 payload，必须是 JSON object
```

## Frame Types

当前支持的 frame 类型：

```text
TASK_REQUEST   Coordinator -> Agent，派发任务
TASK_ACK       Agent -> Coordinator，确认已接收任务
TASK_RESULT    Agent -> Coordinator，返回任务执行结果
RESULT_ACK     Coordinator -> Agent，确认已接收结果
ERROR          任意一方返回协议错误、限流、拒绝或其他失败
```

## Coordinator -> Agent

Coordinator 通过注册中心或本地配置发现 Agent 后，连接：

```text
tcp://<agent_host>:<agent_port>
```

并发送 `TASK_REQUEST` frame。

示例：

```json
{
  "version": "1.0",
  "type": "TASK_REQUEST",
  "trace_id": "trace-abc123",
  "span_id": "span-coordinator-dispatch-weather_agent",
  "parent_span_id": null,
  "source": "coordinator",
  "target": "weather_agent",
  "task_id": "abc123",
  "deadline_ms": 10000,
  "payload": {
    "source": "coordinator",
    "target": "weather_agent",
    "task_id": "abc123",
    "instruction": "Plan tomorrow's trip to Guangzhou.",
    "context": {
      "selected_by": "rule_fallback",
      "agent_capabilities": ["weather"]
    },
    "reply_to": "tcp://127.0.0.1:9001",
    "created_at": "2026-05-25T00:00:00+00:00"
  }
}
```

Agent 收到后必须返回 `TASK_ACK`：

```json
{
  "version": "1.0",
  "type": "TASK_ACK",
  "trace_id": "trace-abc123",
  "span_id": "span-weather_agent-001",
  "parent_span_id": "span-coordinator-dispatch-weather_agent",
  "source": "weather_agent",
  "target": "coordinator",
  "task_id": "abc123",
  "deadline_ms": null,
  "payload": {
    "accepted": true,
    "agent": "weather_agent",
    "task_id": "abc123"
  }
}
```

`TASK_ACK` 只表示任务已被 Agent 接收，不表示任务已完成。

## Agent -> Coordinator

Agent 完成 MCP/LLM 处理后，连接 `reply_to`：

```text
tcp://127.0.0.1:9001
```

并发送 `TASK_RESULT` frame。

示例：

```json
{
  "version": "1.0",
  "type": "TASK_RESULT",
  "trace_id": "trace-abc123",
  "span_id": "span-weather_agent-result-001",
  "parent_span_id": "span-coordinator-dispatch-weather_agent",
  "source": "weather_agent",
  "target": "coordinator",
  "task_id": "abc123",
  "deadline_ms": null,
  "payload": {
    "source": "weather_agent",
    "target": "coordinator",
    "task_id": "abc123",
    "status": "success",
    "result": "Weather answer",
    "error": null,
    "metadata": {
      "agent": "weather_agent",
      "capability": "weather",
      "mcp_server": "weather_mcp_server",
      "mcp_method": "get_weather"
    }
  }
}
```

Coordinator 收到合法结果后返回 `RESULT_ACK`：

```json
{
  "version": "1.0",
  "type": "RESULT_ACK",
  "trace_id": "trace-abc123",
  "span_id": "span-coordinator-ack-001",
  "parent_span_id": "span-weather_agent-result-001",
  "source": "coordinator",
  "target": "weather_agent",
  "task_id": "abc123",
  "deadline_ms": null,
  "payload": {
    "received": true,
    "task_id": "abc123",
    "task_status": "completed"
  }
}
```

## Error And Rate Limit

协议错误、非法 source、限流、拒绝等情况都应该返回 `ERROR` frame，而不是直接让连接异常退出。

示例：

```json
{
  "version": "1.0",
  "type": "ERROR",
  "trace_id": "trace-abc123",
  "span_id": "span-agent-error-001",
  "parent_span_id": "span-coordinator-dispatch-weather_agent",
  "source": "weather_agent",
  "target": "coordinator",
  "task_id": "abc123",
  "deadline_ms": null,
  "payload": {
    "error": "rate_limited: too many A2A requests",
    "code": "rate_limited",
    "retry_after_ms": 250
  }
}
```

Coordinator 收到 `ERROR` frame 后会记录到 `dispatch_errors`，并根据其他 Agent 结果将任务状态聚合为 `failed` 或 `partial`。

Agent 内部如果遇到 LLM 429/rate limit，当前策略是生成 fallback answer，并通过正常 `TASK_RESULT` 回传。这样外部模型限流不会破坏 A2A TCP 协议链路。

## Registry Contract

Agent 启动时仍通过 HTTP 注册到 Registry Center。

必须保留接口：

```text
POST /register
GET  /discover
GET  /health
```

`/discover` 返回值必须保留以下字段：

```json
{
  "agents": {
    "weather_agent": {
      "agent_name": "weather_agent",
      "host": "127.0.0.1",
      "port": 9010,
      "protocol": "tcp",
      "enabled": true,
      "capabilities": ["weather"],
      "keywords": ["weather", "forecast"]
    }
  }
}
```

可以新增 heartbeat、TTL、lookup，但不要破坏 `/discover` 兼容格式。

## Add A New Agent

新增业务 Agent 时，优先继承 `BaseAgent`：

```python
class NewAgent(BaseAgent):
    agent_name = "new_agent"
    capability = "new_capability"
    mcp_server_key = "new_mcp_key"

    def build_prompt(self, task_payload, mcp_result):
        ...
```

并在 `common/config.py` 添加：

```python
"new_agent": {
    "host": "127.0.0.1",
    "port": 9030,
    "protocol": "tcp",
    "enabled": True,
    "capabilities": ["new_capability"],
    "keywords": ["..."],
}
```

不要恢复旧的 Agent HTTP A2A 入口：

```text
POST /execute_task
```

Coordinator 现在只会使用 TCP frame 派发任务。

## Tests

运行协议测试：

```powershell
python -m unittest discover -s tests -v
```

当前测试覆盖：

```text
TCP 连续 frame 不粘包
TCP 分片 frame 可重组
Envelope 类型和必填字段校验
Coordinator 收到 TASK_RESULT 后返回 RESULT_ACK
非法 result source 返回 ERROR
A2A rate limit ERROR 可被 Coordinator 正确记录
LLM 429/rate limit 可降级为 Agent fallback result
```

## High-Risk Conflict Areas

多人协作时最容易冲突的文件：

```text
agents/base_agent.py
common/config.py
common/tcp_a2a.py
coordinator.py
registry_center.py
tests/test_a2a_tcp_protocol.py
```

修改建议：

1. 新增业务 Agent：只新增 `agents/xxx_agent.py`，并修改 `common/config.py`。
2. 修改注册中心：保留 `/register` 和 `/discover` 的兼容格式，再增量添加 heartbeat/lookup。
3. 不要把 Coordinator 的 `/submit_task` 改成 TCP。
4. 不要恢复旧的 Agent `/execute_task` HTTP A2A 数据面。
