# autoGit-MCP

`autoGit-MCP` 提供了将常见 Git 操作与辅助自动化能力封装为 [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) 工具的实现，便于在智能体或自动化工作流中安全地执行 Git 命令并生成提交说明。

## ✨ 主要特性

- **`git` 工具**：将常见 Git 子命令统一为 `cmd + args` 调用，提供参数校验、危险命令防护以及结构化输出，覆盖 `status`、`add`、`commit`、`pull`、`push`、`fetch`、`merge`、`rebase`、`diff`、`log`、`branch`、`switch`、`tag`、`reset`、`revert`、`clean`、`remote`、`stash`、`submodule`、`cherry-pick` 等命令。
- **`git_flow` 工具**：结合仓库 README、Git Diff 与自定义提示词，通过 OpenGPT 或 DeepSeek 等兼容 OpenAI Chat Completions 接口的模型自动生成提交信息等内容，亦可基于预设的 Git 组合命令模板生成执行方案，并支持占位符填充与冲突处理提示。
- **FastMCP Server**：基于 `mcp.server.fastmcp.FastMCP` 暴露工具，使用 HTTP/SSE 协议，便于与任意兼容 MCP 的客户端集成。
- **完善的错误处理**：所有工具都包含全面的异常捕获和友好的错误消息返回。
- **代码结构优化**：采用关注点分离设计，接口定义与实现逻辑分离，便于维护和扩展。

更多设计细节可参考仓库中的 [`docs/`](docs/) 与 [`guide.md`](guide.md)。

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

项目主要依赖：
- `mcp` - Model Context Protocol 支持
- `pydantic` (v2) - 数据验证
- `fastapi` - HTTP 服务器框架
- `uvicorn` - ASGI 服务器

### 2. 配置环境变量（可选）

如果使用 `git_flow` 工具，需要设置对应的 API Key：

```bash
# DeepSeek (默认)
export DEEPSEEK_API_KEY="your-api-key"
export DEEPSEEK_API_URL="https://api.deepseek.com/v1/chat/completions"  # 可选
export DEEPSEEK_MODEL="deepseek-chat"  # 可选，默认值

# 或 OpenGPT
export OPENGPT_API_KEY="your-api-key"
export OPENGPT_API_URL="https://api.opengpt.com/v1/chat/completions"  # 可选
export OPENGPT_MODEL="gpt-4.1-mini"  # 可选，默认值
```

### 3. 启动 MCP Server

```bash
uvicorn src.git_tool.server:app --reload --port 9010
```

服务器启动后，默认监听 `http://localhost:9010/mcp`。

### 4. 配置 MCP 客户端

在 Cursor 等 MCP 客户端中配置（通常为 `~/.cursor/mcp.json` 或客户端配置目录）：

```json
{
  "mcpServers": {
    "git-mcp": {
      "url": "http://localhost:9010/mcp"
    }
  }
}
```

重启客户端后，即可使用 `git` 和 `git_flow` 工具。

## 📖 使用示例

### `git` 工具

#### 查看 Git 状态

```json
{
  "repo_path": "/path/to/repo",
  "cmd": "status",
  "args": {},
  "dry_run": false,
  "allow_destructive": false,
  "timeout_sec": 30
}
```

#### 查看已暂存的差异

```json
{
  "repo_path": "/path/to/repo",
  "cmd": "diff",
  "args": {
    "cached": true
  },
  "dry_run": false,
  "allow_destructive": false,
  "timeout_sec": 30
}
```

#### 查看最近提交记录

```json
{
  "repo_path": "/path/to/repo",
  "cmd": "log",
  "args": {
    "oneline": true,
    "graph": false,
    "max_count": 10
  },
  "dry_run": false,
  "allow_destructive": false,
  "timeout_sec": 30
}
```

**重要提示**：`args` 参数必须传递一个字典对象（即使为空），不要使用 `null`。

### `git_flow` 工具

#### 生成提交信息

```json
{
  "repo_path": "/path/to/repo",
  "action": "generate_commit_message",
  "provider": "deepseek",
  "diff_scope": "staged",
  "include_readme": true,
  "max_diff_chars": 8000
}
```

#### 生成组合命令执行计划

```json
{
  "repo_path": "/path/to/repo",
  "action": "combo_plan",
  "combo_name": "safe_sync",
  "combo_replacements": {
    "branch": "main",
    "remote": "origin"
  }
}
```

## 🏗️ 项目结构

项目采用关注点分离的架构设计，接口定义与实现逻辑分离：

```
.
├── README.md
├── LICENSE
├── guide.md
├── docs/
│   ├── code-structure.md          # 代码结构详细说明
│   ├── git-cheatsheet.md          # Git 命令速查表
│   ├── git_comb.md                # Git 组合命令说明
│   ├── mcp-git-tool.md            # MCP Git 工具设计文档
│   └── troubleshooting.md         # 故障排查指南
└── src/git_tool/
    ├── __init__.py                # 模块导出
    ├── server.py                  # MCP 接口定义（仅包含 @server.tool() 装饰器）
    ├── models.py                  # 数据模型（Pydantic V2）
    ├── git_commands.py            # git 工具实现
    ├── git_flow_commands.py       # git_flow 工具实现
    ├── git_combos.py              # Git 组合命令模板
    └── prompt_profiles.py        # 提示词配置模板
```

### 架构说明

- **`server.py`**：仅包含 MCP 工具接口定义，不包含实现逻辑
- **`models.py`**：所有数据模型和验证规则（使用 Pydantic V2）
- **`git_commands.py`**：git 工具的所有实现逻辑和异常处理
- **`git_flow_commands.py`**：git_flow 工具的所有实现逻辑和 LLM 调用

详细的代码结构说明请参考 [`docs/code-structure.md`](docs/code-structure.md)。

## 🔧 `git_flow` 自动化能力

`git_flow` 旨在将 Git 工作流中的"提交信息生成"等任务交给 LLM 处理，并且支持围绕文档中的 Git 组合命令为你定制执行计划。它会根据以下信息构造提示词：

- 自定义的 system prompt 与 user prompt（均为可选项）
- 仓库根目录下的 `README`（可通过参数控制是否包含，并支持字符数限制）
- 指定范围的 `git diff` 结果（支持暂存区、工作区、或与任意目标 commit 的 diff）
- Git 状态信息（`git status`）
- 额外的上下文字符串（如需求描述、Issue 链接等）
- 选定的 Git 串行组合命令模板（当 `action` 为 `combo_plan` 时注入）

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
  "provider": "opengpt" | "deepseek",   // 默认 "deepseek"
  "model": "可选模型名",                  // 覆盖默认模型
  "system_prompt": "可选 system prompt",
  "user_prompt": "可选 user prompt",
  "prompt_profile": "software_engineering" | "devops" | "product_analysis" | "documentation" | "data_analysis",
  "diff_scope": "staged" | "workspace" | "head",  // 默认 "staged"
  "diff_target": "HEAD",                 // 当 diff_scope 为 head 时使用，默认 HEAD
  "include_readme": true,                // 默认 true
  "include_diff": true,                  // 默认 true
  "include_status": true,                // 默认 true
  "max_readme_chars": 4000,              // 默认 4000
  "max_diff_chars": 8000,                // 默认 8000
  "max_status_chars": 2000,              // 默认 2000
  "extra_context": "其他上下文",
  "temperature": 0.2,                   // 默认 0.2，范围 0.0-2.0
  "timeout_sec": 120,                    // 默认 120
  // --- combo_plan 专用字段 ---
  "combo_name": "safe_sync",             // action 为 combo_plan 时必填
  "combo_replacements": {                // 可选占位符替换
    "branch": "main",
    "remote": "origin"
  }
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
    "endpoint": "https://api.opengpt.com/v1/chat/completions",
    "combo": "safe_sync"  // combo_plan 动作会包含该字段
  }
}
```

若调用模型失败，`stderr` 会包含错误描述，同时 `exit_code` 为非零值。错误信息会根据失败类型提供具体的提示（如 API 密钥未设置、网络连接错误、Git 操作错误等）。

## 📝 提示词模板

项目内置了以下默认模板（可通过参数覆盖）：

- **System Prompt**：`"You are an experienced software engineer who writes Conventional Commits."`
- **User Prompt**：`"请基于以下项目上下文与 diff，生成一条简洁的 Conventional Commit 信息，并给出简短的正文说明。"`

当 `action` 设为 `combo_plan` 时，会默认使用专为 Git 串行组合命令设计的提示词，生成包含"适用场景、逐步说明、脚本模板"的执行指南；你也可以通过 `system_prompt` 与 `user_prompt` 自定义文案风格，或直接设置 `prompt_profile` 选择内置模板（当同时提供自定义 Prompt 时优先使用自定义内容）。

### 预设提示词模板

项目提供了以下专业领域的提示词模板，可通过 `prompt_profile` 参数使用：

1. **`software_engineering`** - 软件工程（实现 / 重构 / 缺陷修复）
2. **`devops`** - DevOps / 运维自动化
3. **`product_analysis`** - 产品 / 需求分析
4. **`documentation`** - 文档与知识库维护
5. **`data_analysis`** - 数据分析 / 指标洞察

详细的模板内容请参考 [`src/git_tool/prompt_profiles.py`](src/git_tool/prompt_profiles.py)。

## 🛡️ 安全特性

### 危险命令防护

默认情况下，以下危险命令需要显式设置 `allow_destructive: true` 才能执行：

- `reset --hard` - 硬重置
- `clean -fd` - 强制清理未跟踪文件
- `push --force` - 强制推送
- `stash drop` / `stash clear` - 删除 stash
- 其他可能导致数据丢失的操作

### Dry Run 模式

对于以下命令支持 `dry_run: true` 预览执行计划：

- `commit`
- `merge`
- `reset`
- `revert`
- `clean`

## ⚠️ 错误处理

所有工具都包含完善的错误处理机制：

- **参数验证错误**：提供清晰的错误消息，指出哪个参数无效
- **命令执行错误**：返回 Git 命令的 stdout 和 stderr
- **超时错误**：可配置超时时间，超时时返回明确提示
- **网络错误**：区分 HTTP 错误和连接错误
- **API 密钥错误**：提示需要设置的环境变量

详细错误处理说明请参考 [`docs/troubleshooting.md`](docs/troubleshooting.md)。

## 🔄 版本更新

### 最新改进（v1.1）

- ✅ **代码重构**：分离接口定义与实现逻辑，提高代码可维护性
- ✅ **Pydantic V2 迁移**：所有验证器已迁移到 Pydantic V2（`@field_validator` 和 `@model_validator`）
- ✅ **参数类型修复**：修复 `args` 参数类型问题，使用 `dict` 替代 `Optional[Dict[str, Any]]`
- ✅ **完善的错误处理**：为所有工具添加了分类异常处理和友好的错误消息
- ✅ **文档优化**：整理并优化文档结构，移除临时文档

## 📚 文档

- [`docs/code-structure.md`](docs/code-structure.md) - 代码结构详细说明
- [`docs/git-cheatsheet.md`](docs/git-cheatsheet.md) - Git 命令速查表
- [`docs/git_comb.md`](docs/git_comb.md) - Git 组合命令说明
- [`docs/mcp-git-tool.md`](docs/mcp-git-tool.md) - MCP Git 工具设计文档
- [`docs/troubleshooting.md`](docs/troubleshooting.md) - 故障排查指南

## 📄 许可协议

本项目遵循 [MIT License](LICENSE)。
