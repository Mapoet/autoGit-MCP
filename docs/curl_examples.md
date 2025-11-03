# curl 调用示例

本文档提供使用 `curl` 测试 MCP 工具的示例命令。

## 前提条件

1. 确保 MCP 服务器正在运行：
```bash
uvicorn src.git_tool.server:app --reload --port 9010 --lifespan on
```

2. 服务器端点：
   - **REST API（推荐，无需 session ID）**: `http://localhost:9010/api`
   - **MCP SSE 端点（需要 session ID）**: `http://localhost:9010/mcp/`（注意尾斜杠）

## ⭐ 推荐：使用 REST API（无需 session ID）

新增的 REST API 端点可以直接调用，**无需 session ID**，更简单方便！

### REST API 端点

- `GET /api/tools` - 列出所有工具
- `POST /api/git` - 调用 git 工具
- `POST /api/git_flow` - 调用 git_flow 工具
- `POST /api/git_work` - 调用 git_work 工具

## ⭐ REST API 示例（推荐，无需 session ID）

### git_work 工具 - REST API

```bash
# 简单直接，无需 session ID
curl -X POST http://localhost:9010/api/git_work \
  -H "Content-Type: application/json" \
  -d '{
    "repo_paths": ["/mnt/d/works/RayTracy"],
    "days": 1,
    "add_summary": true,
    "provider": "deepseek",
    "temperature": 0.2
  }'
```

### git 工具 - REST API

```bash
# Git Status
curl -X POST http://localhost:9010/api/git \
  -H "Content-Type: application/json" \
  -d '{
    "repo_path": "/mnt/d/works/RayTracy",
    "cmd": "status",
    "args": {"branch": true}
  }'
```

### 列出工具 - REST API

```bash
curl http://localhost:9010/api/tools
```

---

## MCP SSE 端点示例（需要 session ID）

> **注意**：以下示例需要先调用 `initialize` 获取 session ID，然后使用 session ID 进行后续调用。推荐使用上面的 REST API，更简单。

### 示例 1: 基本调用（本地仓库，今天的工作，启用 AI 总结）

```bash
# 1. 先初始化获取 session ID
SESSION_ID=$(curl -s -i -X POST http://localhost:9010/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream, application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 0,
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {"name": "curl-client", "version": "1.0.0"}
    }
  }' | grep -i "mcp-session-id" | cut -d: -f2 | tr -d ' \r\n')

# 2. 使用 session ID 调用工具
curl -X POST http://localhost:9010/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream, application/json" \
  -H "Mcp-Session-Id: $SESSION_ID" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "git_work",
      "arguments": {
        "repo_paths": ["/mnt/d/works/RayTracy"],
        "days": 1,
        "add_summary": true,
        "provider": "deepseek"
      }
    }
  }'
```

### 示例 2: 使用 since/until 指定日期范围

```bash
curl -X POST http://localhost:9010/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
      "name": "git_work",
      "arguments": {
        "repo_paths": ["/mnt/d/works/RayTracy"],
        "since": "2024-11-01T00:00:00",
        "until": "2024-11-03T23:59:59",
        "days": null,
        "add_summary": false,
        "session_gap_minutes": 60
      }
    }
  }'
```

### 示例 3: 多仓库（本地 + GitHub）

```bash
curl -X POST http://localhost:9010/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
      "name": "git_work",
      "arguments": {
        "repo_paths": ["/home/mapoet/Data/works/git2work"],
        "github_repos": ["owner/repo"],
        "gitee_repos": null,
        "days": 7,
        "add_summary": true,
        "provider": "deepseek"
      }
    }
  }'
```

### 示例 4: 最小参数（仅必填字段）

```bash
curl -X POST http://localhost:9010/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 4,
    "method": "tools/call",
    "params": {
      "name": "git_work",
      "arguments": {
        "repo_paths": ["/mnt/d/works/RayTracy"]
      }
    }
  }'
```

## git 工具

### 查看 Git 状态

```bash
curl -X POST http://localhost:9010/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 5,
    "method": "tools/call",
    "params": {
      "name": "git",
      "arguments": {
        "repo_path": "/mnt/d/works/RayTracy",
        "cmd": "status",
        "args": {
          "short": false,
          "branch": true
        },
        "dry_run": false,
        "allow_destructive": false,
        "timeout_sec": 30
      }
    }
  }'
```

### 查看 Git 日志

```bash
curl -X POST http://localhost:9010/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 6,
    "method": "tools/call",
    "params": {
      "name": "git",
      "arguments": {
        "repo_path": "/mnt/d/works/RayTracy",
        "cmd": "log",
        "args": {
          "oneline": true,
          "max_count": 10
        }
      }
    }
  }'
```

## git_flow 工具

### 生成提交信息

```bash
curl -X POST http://localhost:9010/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 7,
    "method": "tools/call",
    "params": {
      "name": "git_flow",
      "arguments": {
        "repo_path": "/mnt/d/works/RayTracy",
        "action": "generate_commit_message",
        "provider": "deepseek",
        "diff_scope": "staged"
      }
    }
  }'
```

## 格式化输出

### 使用 jq 格式化 JSON 响应

```bash
curl -X POST http://localhost:9010/mcp \
  -H "Content-Type: application/json" \
  -d '{...}' | jq .
```

### 提取 stdout 字段（工作日志内容）

```bash
curl -X POST http://localhost:9010/mcp \
  -H "Content-Type: application/json" \
  -d '{...}' | jq -r '.result.content[0].text' | jq -r '.stdout'
```

### 提取错误信息

```bash
curl -X POST http://localhost:9010/mcp \
  -H "Content-Type: application/json" \
  -d '{...}' | jq -r '.result.content[0].text' | jq -r '.stderr'
```

## 快速测试脚本

保存为 `test_work_log.sh`：

```bash
#!/bin/bash

ENDPOINT="http://localhost:9010/mcp"

curl -X POST "$ENDPOINT" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "git_work",
      "arguments": {
        "repo_paths": ["/mnt/d/works/RayTracy"],
        "days": 1,
        "add_summary": true,
        "provider": "deepseek",
        "temperature": 0.2
      }
    }
  }' | jq .
```

使用：
```bash
chmod +x test_work_log.sh
./test_work_log.sh
```

## 注意事项

1. **JSON 格式**: 确保使用 `null` 而不是 `None`（Python 语法）
2. **Content-Type**: 必须设置为 `application/json`
3. **端口**: 默认端口是 `9010`，如果修改了请相应调整
4. **路径**: 确保仓库路径存在且可访问
5. **环境变量**: 如果使用 AI 总结，确保设置了相应的 API key（`DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY`）

## 调试技巧

### 查看原始响应

```bash
curl -v -X POST http://localhost:9010/mcp \
  -H "Content-Type: application/json" \
  -d '{...}'
```

### 检查服务器是否运行

```bash
curl http://localhost:9010/mcp
```

### 列出可用工具

```bash
curl -X POST http://localhost:9010/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/list"
  }' | jq .
```

