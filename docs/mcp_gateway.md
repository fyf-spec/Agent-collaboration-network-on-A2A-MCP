# MCP Gateway 通信治理层说明

本文档记录本项目中 MCP Gateway 的代码改动、当前实现效果、运行方式与验证指令，展示计算机网络架构中的通信治理、缓存、限流、熔断与可观测性。

## 1. 模块定位

原始链路中，工作 Agent 会直接调用 MCP Server：

```text
Weather Agent  -> Weather MCP Server
Traffic Agent  -> Traffic MCP Server
```

加入 MCP Gateway 后，链路变为：

```text
Weather Agent  -> MCP Gateway -> Weather MCP Server
Traffic Agent  -> MCP Gateway -> Traffic MCP Server
```

Gateway 不改变 JSON-RPC 2.0 的业务语义。Agent 仍然发送标准 MCP JSON-RPC 请求，Gateway 根据 `method` 字段进行路由转发。例如：

```json
{
  "jsonrpc": "2.0",
  "id": "task-001",
  "method": "get_weather",
  "params": {
    "city": "北京"
  }
}
```

`get_weather` 会被转发到 Weather MCP Server，`get_transport` 会被转发到 Traffic MCP Server。

## 2. 本次代码改动

### 2.1 新增文件

`mcp_gateway.py`

新增独立 MCP Gateway 进程，默认监听：

```text
http://127.0.0.1:8100
```

提供接口：

```text
POST /        JSON-RPC 2.0 请求入口
GET  /health  Gateway 健康状态
GET  /methods 当前 method 到 MCP Server 的路由表
GET  /metrics Gateway 统计指标
```

核心能力：

- JSON-RPC 2.0 统一转发
- TTL cache
- request coalescing，相同并发请求合并
- 按 MCP method 的并发限流
- circuit breaker 熔断
- metrics 指标统计
- 网络交互日志输出

### 2.2 修改配置

`common/config.py`

新增 `MCP_GATEWAY` 配置：

```python
MCP_GATEWAY = {
    "name": "mcp_gateway",
    "host": "127.0.0.1",
    "port": 8100,
    "path": "/",
    "enabled": True,
    "cache_ttl_seconds": 10.0,
    "max_concurrent_per_method": 3,
    "rate_limit_wait_seconds": 0.2,
    "coalesce_wait_seconds": 5.0,
    "upstream_timeout_seconds": 2.5,
    "circuit_failure_threshold": 3,
    "circuit_cooldown_seconds": 10.0,
}
```

含义：

- `enabled`: 是否启用 Gateway。为 `True` 时 Agent 调用 Gateway；为 `False` 时 Agent 退回直连 MCP Server。
- `cache_ttl_seconds`: 缓存有效期。
- `max_concurrent_per_method`: 每个 MCP method 最大并发上游请求数。
- `rate_limit_wait_seconds`: 获取限流令牌的最大等待时间。
- `coalesce_wait_seconds`: 相同请求等待首个回源结果的最大时间。
- `upstream_timeout_seconds`: Gateway 调用真实 MCP Server 的超时时间。该值小于 Agent 等待 Gateway 的 3 秒超时，避免 Agent 先放弃、Gateway 后返回的演示竞态。
- `circuit_failure_threshold`: 连续失败多少次后打开熔断。
- `circuit_cooldown_seconds`: 熔断打开后的冷却时间。

### 2.3 修改 Agent 调用路径

`agents/base_agent.py`

原来 Agent 直接构造 MCP Server URL：

```text
http://127.0.0.1:8001/
http://127.0.0.1:8002/
```

现在默认构造 Gateway URL：

```text
http://127.0.0.1:8100/
```

日志中可以看到：

```text
weather_agent -> mcp_gateway
mcp_gateway -> weather_mcp_server
mcp_gateway -> weather_agent
```

同时 Agent 结果的 metadata 中会附带：

```json
{
  "mcp_gateway": "mcp_gateway"
}
```

### 2.4 修改启动脚本

`scripts/start_all.py`

启动顺序中加入：

```python
("mcp_gateway", "mcp_gateway.py")
```

因此执行 `start_all.py` 或现有 demo 脚本时，会自动启动 Gateway。

## 3. 当前实现效果

### 3.1 统一转发

Agent 不再直接访问 Weather/Traffic MCP Server，而是统一访问 MCP Gateway。Gateway 根据 JSON-RPC 的 `method` 字段选择上游：

```text
get_weather   -> weather_mcp_server  -> http://127.0.0.1:8001/
get_transport -> traffic_mcp_server  -> http://127.0.0.1:8002/
```

### 3.2 TTL Cache

Gateway 使用：

```text
method + sorted(params)
```

作为 cache key。短时间内相同请求会直接返回缓存结果，不再重复请求 MCP Server。

示例：

```text
第一次 get_weather({"city": "北京"}) -> 回源 Weather MCP
第二次 get_weather({"city": "北京"}) -> 命中 Gateway 缓存
```

验证结果：

```text
total_requests = 2
upstream_calls = 1
cache_hits     = 1
error_count    = 0
```

这可以用于展示 Gateway 减少重复 MCP 上游请求。

### 3.3 Request Coalescing

如果多个线程同时请求同一个 cache key，Gateway 只允许第一个请求回源，其他请求等待同一个 inflight result。这样可以避免并发请求瞬间把 MCP Server 打穿。

实现思路：

```text
cache_key -> InflightCall(threading.Event)
```

第一个请求负责真实调用 MCP Server，后续相同请求等待该 Event 完成。

### 3.4 Rate Limit / Backpressure

Gateway 使用 `threading.BoundedSemaphore` 对每个 method 做并发限制。当前默认：

```text
max_concurrent_per_method = 3
```

如果同一个 method 的上游请求过多，Gateway 会返回 JSON-RPC error：

```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32002,
    "message": "Gateway busy: too many concurrent requests for get_weather"
  },
  "id": "task-001"
}
```

### 3.5 Circuit Breaker

当某个 MCP Server 连续失败达到阈值后，Gateway 会打开熔断器。当前默认：

```text
circuit_failure_threshold = 3
circuit_cooldown_seconds  = 10
```

验证结果：

```text
第 1 次 get_weather -> 尝试回源，失败
第 2 次 get_weather -> 尝试回源，失败
第 3 次 get_weather -> 尝试回源，失败，熔断打开
第 4 次 get_weather -> 不再请求 Weather MCP，直接返回 circuit_open
```

对应 metrics：

```text
total_requests = 4
upstream_calls = 3
circuit_open   = 1
error_count    = 4
breaker_state  = open
```

返回示例：

```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32001,
    "message": "circuit_open: weather_mcp_server unavailable"
  },
  "id": "fault-4"
}
```

该机制可以展示：MCP 故障时，系统不会每次都等待 timeout，而是进入 fail-fast 状态。

### 3.6 Metrics 可观测性

访问：

```text
GET http://127.0.0.1:8100/metrics
```

返回示例：

```json
{
  "ok": true,
  "metrics": {
    "total_requests": 2,
    "upstream_calls": 1,
    "cache_hits": 1,
    "cache_misses": 1,
    "coalesced_requests": 0,
    "rate_limited": 0,
    "circuit_open": 0,
    "error_count": 0,
    "avg_latency_ms": 129.4,
    "cache_size": 1,
    "method_stats": {
      "get_weather": {
        "requests": 2,
        "upstream_calls": 1,
        "cache_hits": 1,
        "cache_misses": 1,
        "avg_latency_ms": 129.4
      }
    },
    "circuit_breakers": {
      "get_weather": {
        "state": "closed",
        "failure_count": 0,
        "retry_after_ms": 0.0
      }
    }
  }
}
```

## 4. 运行指令

### 4.1 启动全部服务

```bash
uv run python scripts/start_all.py
```

会启动：

```text
registry_center
weather_mcp_server
traffic_mcp_server
mcp_gateway
weather_agent
traffic_agent
coordinator
```

### 4.2 运行正常流程 demo

```bash
uv run python scripts/demo_normal.py
```

预期效果：

- Coordinator 接收用户问题。
- Coordinator 分发给 Weather Agent 和 Traffic Agent。
- Agent 调用 MCP Gateway。
- Gateway 转发到对应 MCP Server。
- Agent 将结果回传 Coordinator。
- Coordinator 汇总旅行方案。

### 4.3 运行延迟场景 demo

```bash
uv run python scripts/demo_delay.py
```

该脚本会给 Weather MCP Server 加人工延迟。预期效果：

- 脚本会设置 `A2A_DEMO_FAST=1`，跳过外部 LLM 调用，保证故障演示几秒内稳定完成。
- Weather 路径可能超时或失败。
- Gateway 记录失败。
- Weather Agent 返回错误结果。
- Coordinator 仍可根据 Traffic Agent 的结果输出 partial 降级方案。

### 4.4 运行故障场景 demo

```bash
uv run python scripts/demo_fault.py
```

该脚本不启动 Weather MCP Server。预期效果：

- 脚本会设置 `A2A_DEMO_FAST=1`，跳过外部 LLM 调用，保证故障演示几秒内稳定完成。
- Gateway 调 Weather MCP 失败。
- 连续失败达到阈值后，Gateway 打开熔断。
- 后续相同 method 请求快速返回 `circuit_open`。
- Coordinator 不会永久卡死，最终输出降级方案。

### 4.5 运行 Gateway 缓存 demo

```bash
uv run python scripts/demo_gateway_cache.py
```

该脚本只启动 Weather MCP Server 和 MCP Gateway，不启动 Coordinator、Agent 或外部 LLM。它会连续向 Gateway 发送两次相同的 `get_weather({"city": "北京"})` 请求，然后读取 `/metrics`。

预期效果：

```text
total_requests = 2
upstream_calls = 1
cache_hits     = 1
error_count    = 0
```

该脚本适合现场展示 Gateway 的 TTL cache 如何减少重复上游 MCP 调用。

## 5. 手动验证指令

以下指令适合在 PowerShell 中单独验证 Gateway。

### 5.1 启动最小验证环境

开三个终端分别运行：

```bash
uv run python mcp_servers/weather_mcp_server.py
```

```bash
uv run python mcp_servers/traffic_mcp_server.py
```

```bash
uv run python mcp_gateway.py
```

### 5.2 发送一次 JSON-RPC 请求

PowerShell：

```powershell
$body = @{
  jsonrpc = "2.0"
  id = "manual-1"
  method = "get_weather"
  params = @{ city = "北京" }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8100/" `
  -Method POST `
  -ContentType "application/json; charset=utf-8" `
  -Body $body
```

预期返回：

```json
{
  "jsonrpc": "2.0",
  "result": {
    "city": "北京",
    "date": "明天",
    "temp": "15°C",
    "condition": "晴",
    "wind": "微风"
  },
  "id": "manual-1"
}
```

### 5.3 查看 Gateway 指标

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8100/metrics" -Method GET
```

重点观察：

```text
total_requests
upstream_calls
cache_hits
coalesced_requests
rate_limited
circuit_open
error_count
method_stats
circuit_breakers
```

### 5.4 验证缓存效果

连续发送两次完全相同的请求：

```powershell
$body = @{
  jsonrpc = "2.0"
  id = "cache-test"
  method = "get_weather"
  params = @{ city = "北京" }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Uri "http://127.0.0.1:8100/" -Method POST -ContentType "application/json; charset=utf-8" -Body $body
Invoke-RestMethod -Uri "http://127.0.0.1:8100/" -Method POST -ContentType "application/json; charset=utf-8" -Body $body
Invoke-RestMethod -Uri "http://127.0.0.1:8100/metrics" -Method GET
```

预期：

```text
total_requests = 2
upstream_calls = 1
cache_hits     = 1
```

### 5.5 验证熔断效果

只启动 Gateway，不启动 Weather MCP Server，然后连续请求：

```powershell
for ($i = 1; $i -le 4; $i++) {
  $body = @{
    jsonrpc = "2.0"
    id = "fault-$i"
    method = "get_weather"
    params = @{ city = "北京" }
  } | ConvertTo-Json -Depth 5

  Invoke-RestMethod `
    -Uri "http://127.0.0.1:8100/" `
    -Method POST `
    -ContentType "application/json; charset=utf-8" `
    -Body $body
}

Invoke-RestMethod -Uri "http://127.0.0.1:8100/metrics" -Method GET
```

预期：

```text
upstream_calls = 3
circuit_open   = 1
breaker_state  = open
```

第 4 次请求会快速返回：

```text
circuit_open: weather_mcp_server unavailable
```

## 6. 日志展示点

系统已有 `common.logger.log_network_event`，Gateway 已接入同一日志体系。控制台会打印：

```text
gateway_jsonrpc_request
gateway_call_mcp
gateway_mcp_response
gateway_cache_hit
gateway_coalesced_result
gateway_mcp_failed
gateway_circuit_open
```

这些日志可以用于现场展示每一次网络交互的：

- source
- target
- HTTP method
- URL
- task_id
- payload size
- latency
- status code
- error type
- JSON payload

