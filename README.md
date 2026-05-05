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

## 环境管理

本仓库使用 `uv` 管理 Python 环境和依赖，依赖声明在 `pyproject.toml` 中。所有 Python 脚本都通过 `uv run python ...` 执行。

```bash
uv sync
uv run python coordinator.py
```

## Workflow
```bash
# 1. 更新 main
git checkout main
git pull

# 2. 回到自己的分支
git checkout feature/your-name

# 3. 把 main 的更新同步到自己的分支
git pull origin main

# 4. 开始工作
# ... 修改代码 ...

# 5. 提交并推送自己的分支
git status
git add .
git commit -m "type: brief description"
git push
```

## Pull Request

个人分支完成阶段性工作后，在 GitHub 上创建 Pull Request，将自己的分支合并到 `main`。

建议在一个功能基本完成、代码可以运行后再合并。

## Commit Message

提交信息建议使用：

```text
type: brief description
```

常见类型：

```text
feat: 新增功能
fix: 修复问题
docs: 修改文档
chore: 工程配置或杂项修改
refactor: 代码重构
test: 测试相关修改
```

示例：

```bash
git commit -m "feat: add MFCC feature extraction"
git commit -m "fix: handle audio loading error"
git commit -m "docs: update README"
git commit -m "chore: update requirements"
```

## Files Not Tracked by Git

相关规则已写入 `.gitignore`。如果后续产生新的临时文件或大型中间结果文件，需要及时补充 `.gitignore`。

## Notes

- *README内容由GPT辅助生成*
