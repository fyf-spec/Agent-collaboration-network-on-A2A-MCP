# Agent-A2A

本仓库为 v1 版本，主要演示 Coordinator、Agent 与 MCP Server 的本地 A2A 协作流程。  
v1 不包含 TCP 通信和多设备传输能力；这些能力属于扩展。

## Commit & PR

- `main` 分支保持稳定，不直接提交未确认代码。每个人从 `main` 拉出自己的分支开发
- 开发完成后 push 到自己的远程分支，再通过 Pull Request 合并到 `main`。

## v1 目录

```text
Project/
├── coordinator.py
├── registry_center.py
├── llm_client.py
├── agents/
│ ├── base_agent.py
│ ├── weather_agent.py
│ └── traffic_agent.py
├── mcp_servers/
│ ├── weather_mcp_server.py
│ ├── traffic_mcp_server.py
│ └── mock_data.py
├── common/
│ ├── config.py
│ ├── http_client.py
│ ├── logger.py
│ ├── schemas.py
│ └── prompt_templates.py
├── scripts/
│ ├── start_all.py
│ ├── demo_normal.py
│ └── demo_fault.py
├── logs/
│ └── demo_log.jsonl
├── docs/
│ 
```

## 后续版本目录参考：TCP + 多设备传输

```text
project/
├── coordinator.py
├── llm_client.py
├── agents/
│   ├── base_agent.py
│   ├── weather_agent.py
│   └── traffic_agent.py
├── mcp_servers/
│   ├── weather_mcp_server.py
│   ├── traffic_mcp_server.py
│   └── mock_data.py
├── common/
│   ├── config.py
│   ├── tcp_protocol.py
│   ├── http_a2a_client.py
│   ├── metrics.py
│   ├── logger.py
│   └── prompt_templates.py
├── scripts/
│   ├── start_local_http_mode.py
│   ├── start_local_tcp_mode.py
│   ├── start_device_a.py
│   ├── start_device_b.py
│   ├── start_device_c.py
│   ├── check_lan_connection.py
│   └── benchmark_http_vs_tcp.py
├── configs/
│   ├── local_config.json
│   └── lan_config.json
├── logs/
│   ├── demo_http.jsonl
│   ├── demo_tcp.jsonl
│   └── benchmark_result.csv
└── docs/
```
