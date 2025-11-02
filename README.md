# autoGit-MCP

`autoGit-MCP` 提供了将常见 Git 操作与辅助自动化能力封装为 [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) 工具的实现，便于在智能体或自动化工作流中安全地执行 Git 命令并生成提交说明。

## 功能概览

- **`git` 工具**：将常见 Git 子命令统一为 `cmd + args` 调用，提供参数校验、危险命令防护以及结构化输出，覆盖 `remote`、`stash`、`submodule` 等拓展指令。
- **`git_flow` 工具**：结合仓库 README、Git Diff 与自定义提示词，通过 OpenGPT 或 DeepSeek 等兼容 OpenAI Chat Completions 接口的模型自动生成提交信息等内容，亦可基于预设的 Git 组合命令模板生成执行方案。
- **FastAPI MCP Server**：基于 `mcp.server.fastapi.FastAPIMCPServer` 暴露工具，便于与任意兼容 MCP 的客户端集成。

更多设计细节可参考仓库中的 [`docs/`](docs/) 与 [`guide.md`](guide.md)。

## 快速开始

1. **安装依赖**

   ```bash
   pip install -r requirements.txt  # 如果你使用的是隔离环境
   ```

   > 项目本身仅依赖标准库与 `mcp`，如需自定义可在环境中自行安装额外依赖。

2. **启动 MCP Server**

   ```bash
   uvicorn src.git_tool.server:app --reload --port 8000
   ```

   服务器启动后即可通过 MCP 客户端（或直接以 HTTP/WebSocket）调用 `git` 与 `git_flow` 工具。

## `git_flow` 自动化能力

`git_flow` 旨在将 Git 工作流中的“提交信息生成”等任务交给 LLM 处理，并且支持围绕文档中的 Git 组合命令为你定制执行计划。它会根据以下信息构造提示词：

- 自定义的 system prompt 与 user prompt（均为可选项）；
- 仓库根目录下的 `README`（可通过参数控制是否包含，并支持字符数限制）；
- 指定范围的 `git diff` 结果（支持暂存区、工作区、或与任意目标 commit 的 diff）；
- 额外的上下文字符串（如需求描述、Issue 链接等）。
- 选定的 Git 串行组合命令模板（当 `action` 为 `combo_plan` 时注入）。

> 默认会针对暂存区（`git diff --cached`）收集变更，并使用一个符合 Conventional Commits 的示例 Prompt 作为模板。

### 环境变量

为了调用不同的模型服务，需要在运行服务器前设置对应的 API Key 与 Endpoint：

| 提供方   | 必填变量                    | 可选变量                    | 说明 |
| -------- | --------------------------- | --------------------------- | ---- |
| OpenGPT  | `OPENGPT_API_KEY`           | `OPENGPT_API_URL`、`OPENGPT_MODEL` | 默认 URL `https://api.opengpt.com/v1/chat/completions`，默认模型 `gpt-4.1-mini`（可被环境变量或请求参数覆盖）。 |
| DeepSeek | `DEEPSEEK_API_KEY`          | `DEEPSEEK_API_URL`、`DEEPSEEK_MODEL` | 默认 URL `https://api.deepseek.com/v1/chat/completions`，默认模型 `deepseek-chat`。 |

若需要连接兼容 OpenAI 格式的其他服务，可通过设置 URL 与模型名称实现。

### 工具参数

`git_flow` 接口签名如下：

```jsonc
{
  "repo_path": "/path/to/repo",
  "action": "generate_commit_message",  // 或 "combo_plan"
  "provider": "opengpt" | "deepseek",
  "model": "可选模型名",
  "system_prompt": "可选 system prompt",
  "user_prompt": "可选 user prompt",
  "diff_scope": "staged" | "workspace" | "head",
  "diff_target": "HEAD" // 当 diff_scope 为 head 时使用，默认 HEAD,
  "include_readme": true,
  "max_readme_chars": 4000,
  "max_diff_chars": 8000,
  "extra_context": "其他上下文",
  "temperature": 0.2,
  "combo_name": "safe_sync",            // action 为 combo_plan 时必填
  "combo_replacements": { "branch": "main" } // 可选占位符替换
}
```

调用成功会返回如下结构：

```jsonc
{
  "exit_code": 0,
  "stdout": "feat: add git_flow automation\n\n- detail 1\n- detail 2",
  "stderr": "",
  "details": {
    "provider": "opengpt",
    "model": "gpt-4.1-mini",
    "diff_scope": "staged",
    "combo": "safe_sync" // combo_plan 动作会包含该字段
  }
}
```

若调用模型失败，`stderr` 会包含错误描述，同时 `exit_code` 为非零值。

## 提示词模板

项目内置了以下默认模板（可通过参数覆盖）：

- **System Prompt**：`"You are an experienced software engineer who writes Conventional Commits."`
- **User Prompt**：`"请基于以下项目上下文与 diff，生成一条简洁的 Conventional Commit 信息，并给出简短的正文说明。"`

当 `action` 设为 `combo_plan` 时，会默认使用专为 Git 串行组合命令设计的提示词，生成包含“适用场景、逐步说明、脚本模板”的执行指南；你也可以通过 `system_prompt` 与 `user_prompt` 自定义文案风格。

调用时会自动在用户提示尾部附加 README 摘要与 Git Diff 内容。

## 目录结构

```
.
├── README.md
├── LICENSE
├── guide.md
├── docs/
│   ├── git-cheatsheet.md
│   └── mcp-git-tool.md
└── src/git_tool/
    ├── __init__.py
    └── server.py
```

## 许可协议

本项目遵循 [MIT License](LICENSE)。
