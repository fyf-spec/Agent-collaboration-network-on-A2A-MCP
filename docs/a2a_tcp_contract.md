## A2A TCP 改造后的修改注意事项

这版代码已经把 Coordinator 和 Agent 之间的 A2A 通信 从 HTTP 改成了 TCP 自定义协议。后续新增 Agent 或修改注册中心时，请注意不要再按旧的 /execute_task HTTP 方式实现 Agent 间通信。

当前通信边界是：
```
User -> Coordinator: HTTP
Coordinator -> Agent: TCP A2A
Agent -> Coordinator: TCP A2A
Agent -> MCP Server: HTTP JSON-RPC
Agent -> Registry Center: HTTP
```

也就是说，HTTP 没有完全移除。Coordinator 的 /submit_task、/health、/tasks、/contracts 仍然保留 HTTP，因为它们是用户入口和管理接口。真正改成 TCP 的是 A2A 数据面。

新增 Agent 时请遵守：
```
class NewAgent(BaseAgent):
    agent_name = "new_agent"
    capability = "new_capability"
    mcp_server_key = "new_mcp_key"

    def build_prompt(self, task_payload, mcp_result):
        ...
```
并在 common/config.py 里注册：
```
"new_agent": {
    "host": "127.0.0.1",
    "port": 9030,
    "protocol": "tcp",
    "enabled": True,
    "capabilities": ["new_capability"],
    "keywords": ["..."],
}
```
不要再新增或依赖：

POST /execute_task
Coordinator 现在会连接：

tcp://host:port
并发送 TCP A2A frame。

TCP A2A 协议格式是：
```
4 字节大端序 length header + UTF-8 JSON body
主要 frame 类型：
TASK_REQUEST   Coordinator -> Agent
TASK_ACK       Agent -> Coordinator
TASK_RESULT    Agent -> Coordinator
RESULT_ACK     Coordinator -> Agent
ERROR          任意一方返回协议错误/限流/拒绝
```

注册中心需要保留现有兼容接口：
```
POST /register
GET  /discover
GET  /health
```

/discover 返回格式必须继续包含：
```
{
  "agents": {
    "weather_agent": {
      "host": "127.0.0.1",
      "port": 9010,
      "protocol": "tcp",
      "enabled": true,
      "capabilities": ["weather"]
    }
  }
}
```
如果要新增 heartbeat、TTL、lookup，可以增量添加，但不要破坏 /discover 和这些字段：
```
agent_name
host
port
protocol
enabled
capabilities
keywords
```
最容易冲突的文件：
```
agents/base_agent.py
common/config.py
coordinator.py
registry_center.py
```
注册中心和agent侧需注意：

1. 新增业务 Agent：只新增 agents/xxx_agent.py，并改 common/config.py。
2. 改注册中心：保留 /register、/discover 兼容格式，再新增 heartbeat/lookup。
3. 不要恢复旧的 Agent HTTP /execute_task。
4. 不要把 Coordinator 的 /submit_task 改成 TCP，它是用户入口，不是 A2A 数据面。

测试命令：
```
python -m unittest discover -s tests -v
```

当前 A2A 协议测试覆盖了 TCP 粘包/半包、callback ACK、非法 source、rate limit error 和 LLM 429 fallback。