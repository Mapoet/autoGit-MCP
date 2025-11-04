# autoGit-MCP

`autoGit-MCP` 提供了将常见 Git 操作与辅助自动化能力封装为 [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) 工具的实现，便于在智能体或自动化工作流中安全地执行 Git 命令并生成提交说明。

## ✨ 主要特性

- **`git` 工具**：将常见 Git 子命令统一为 `cmd + args` 调用，提供参数校验、危险命令防护以及结构化输出，覆盖 `status`、`add`、`commit`、`pull`、`push`、`fetch`、`merge`、`rebase`、`diff`、`log`、`branch`、`switch`、`tag`、`reset`、`revert`、`clean`、`remote`、`stash`、`submodule`、`cherry-pick` 等命令。
- **`git_flow` 工具**：结合仓库 README、Git Diff 与自定义提示词，通过 OpenGPT 或 DeepSeek 等兼容 OpenAI Chat Completions 接口的模型自动生成提交信息等内容，亦可基于预设的 Git 组合命令模板生成执行方案，并支持占位符填充与冲突处理提示。
- **`git_work` 工具**：从本地仓库、GitHub 或 Gitee 收集 Git 提交记录，生成结构化工作日志。支持多项目分析、工作会话计算、并行工作时间检测，并可选择性地使用 AI 生成工作总结。
- **`git_catalog` 工具**：GitHub 活动/仓库目录查询工具，支持 7 个子命令查询 GitHub 仓库和提交活动。包括跨仓库提交明细、仓库作者统计、关键词搜索、组织仓库列表、用户拥有/Star 项目列表等功能。
- **FastMCP Server**：基于 `mcp.server.fastmcp.FastMCP` 暴露工具，使用 HTTP/SSE 协议，便于与任意兼容 MCP 的客户端集成。
- **完善的错误处理**：所有工具都包含全面的异常捕获和友好的错误消息返回。
- **代码结构优化**：采用关注点分离设计，接口定义与实现逻辑分离，便于维护和扩展。

更多设计细节可参考仓库中的 [`docs/`](docs/) 与 [`guide.md`](guide.md)。

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

**核心依赖（必需）**：
- `mcp` - Model Context Protocol 支持（包含 FastAPI）
- `pydantic` (v2) - 数据验证
- `uvicorn` - ASGI 服务器
- `GitPython` - Git 仓库操作（`git_work` 工具必需）
- `requests` - HTTP 请求（`git_work` 工具访问 GitHub/Gitee API）

**可选依赖（根据使用场景安装）**：
- `openai` - OpenAI API 客户端（`git_work` 工具使用 OpenAI 时）
- `PyGithub` - GitHub API 客户端（`git_work` 和 `git_catalog` 工具访问 GitHub 时）
- `python-dateutil` - 日期解析（`git_catalog` 工具必需）

> **注意**：`mcp` 包已包含 FastAPI，无需单独安装。

详细的依赖列表请参考 [`requirements.txt`](requirements.txt)。

### 2. 配置环境变量（可选）

根据使用的功能，配置相应的环境变量：

#### `git_flow` 工具所需环境变量

`git_flow` 工具用于生成提交信息或执行计划，需要配置 LLM 提供者的 API Key：

**DeepSeek（推荐，默认）**：
```bash
export DEEPSEEK_API_KEY="your-deepseek-api-key"              # 必填
export DEEPSEEK_API_URL="https://api.deepseek.com/v1/chat/completions"  # 可选，默认值
export DEEPSEEK_MODEL="deepseek-chat"                        # 可选，默认值
```

**OpenGPT（备选）**：
```bash
export OPENGPT_API_KEY="your-opengpt-api-key"                # 必填
export OPENGPT_API_URL="https://api.opengpt.com/v1/chat/completions"    # 可选，默认值
export OPENGPT_MODEL="gpt-4.1-mini"                         # 可选，默认值
```

#### `git_work` 工具所需环境变量

`git_work` 工具用于生成工作日志，包含两部分配置：

**1. AI 总结生成（可选）**：

如果启用了 `add_summary: true`，需要配置以下之一：

```bash
# 使用 DeepSeek（推荐）
export DEEPSEEK_API_KEY="your-deepseek-api-key"              # 必填

# 或使用 OpenAI
export OPENAI_API_KEY="your-openai-api-key"                  # 必填
```

**2. 远程仓库访问（可选）**：

如果需要在 `git_work` 中访问 GitHub 或 Gitee 仓库：

```bash
# GitHub 仓库访问（访问私有仓库或提高 API 限制）
export GITHUB_TOKEN="your-github-personal-access-token"     # 必填（访问私有仓库时）

# Gitee 仓库访问（访问私有仓库）
export GITEE_TOKEN="your-gitee-personal-access-token"       # 必填（访问私有仓库时）
```

#### `git_catalog` 工具所需环境变量

`git_catalog` 工具用于查询 GitHub 仓库和提交活动，需要配置 GitHub API 访问：

```bash
# GitHub API 访问（提高速率限制并访问私有仓库）
export GITHUB_TOKEN="your-github-personal-access-token"     # 可选，但强烈建议设置
```

> **注意**：未设置 `GITHUB_TOKEN` 时，工具会使用匿名访问（速率限制 60/h）。设置 token 可提高到 5000/h 并访问私有仓库。

> **注意**：
> - `git` 工具不需要任何环境变量
> - 访问公开的 GitHub/Gitee 仓库可以不设置 token，但设置了 token 可以避免 API 速率限制
> - `git_catalog` 工具未设置 token 时使用匿名访问（速率限制较低），建议设置 token 以提高性能
> - 所有环境变量都是可选的，只有在使用对应功能时才需要配置
> - 完整的环境变量配置指南请参考 [`docs/environment-variables.md`](docs/environment-variables.md)

### 3. 启动 MCP Server

```bash
uvicorn src.git_tool.server:app --reload --port 9010 --lifespan on
```

服务器启动后，MCP 端点为 `http://localhost:9010/mcp/`（注意尾斜杠）。

### 4. 配置 MCP 客户端

在 Cursor 等 MCP 客户端中配置（通常为 `~/.cursor/mcp.json` 或客户端配置目录）：

```json
{
  "mcpServers": {
    "git-mcp": {
      "url": "http://localhost:9010/mcp/"
    }
  }
}
```

重启客户端后，即可使用 `git`、`git_flow`、`git_work` 和 `git_catalog` 工具。

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

### `git_work` 工具

#### 生成本地仓库工作日志

```json
{
  "repo_paths": ["/path/to/repo"],
  "days": 7,
  "author": "John Doe",
  "add_summary": true,
  "provider": "deepseek"
}
```

#### 生成多项目工作日志（包含 GitHub/Gitee）

```json
{
  "repo_paths": ["/path/to/local/repo1", "/path/to/local/repo2"],
  "github_repos": ["owner/repo1", "owner/repo2"],
  "gitee_repos": ["owner/repo1"],
  "since": "2024-11-01",
  "until": "2024-11-07",
  "session_gap_minutes": 60,
  "add_summary": true,
  "provider": "deepseek",
  "temperature": 0.3
}
```

### `git_catalog` 工具

#### 搜索仓库

```json
{
  "cmd": "search_repos",
  "args": {
    "keyword": "gnss",
    "language": "C++",
    "min_stars": 50,
    "limit": 200
  }
}
```

#### 列出组织仓库

```json
{
  "cmd": "org_repos",
  "args": {
    "org": "tensorflow",
    "repo_type": "public",
    "limit": 200
  }
}
```

#### 查询用户拥有或 Star 的项目

```json
{
  "cmd": "user_repos",
  "args": {
    "login": "mapoet",
    "mode": "both",
    "include_archived": false,
    "include_forks": true,
    "sort": "stars",
    "order": "desc",
    "limit": 300
  }
}
```

#### 查询跨仓库提交明细

```json
{
  "cmd": "cross_repos",
  "args": {
    "author_login": "octocat",
    "owner": "github",
    "since": "2025-01-01",
    "until": "2025-11-04",
    "max_per_repo": 1000
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
    ├── git_gitwork_commands.py    # git_work 工具实现
    ├── git_catalog_commands.py   # git_catalog 工具实现
    ├── git_combos.py              # Git 组合命令模板
    └── prompt_profiles.py        # 提示词配置模板
```

### 架构说明

- **`server.py`**：仅包含 MCP 工具接口定义，不包含实现逻辑
- **`models.py`**：所有数据模型和验证规则（使用 Pydantic V2）
- **`git_commands.py`**：git 工具的所有实现逻辑和异常处理
- **`git_flow_commands.py`**：git_flow 工具的所有实现逻辑和 LLM 调用
- **`git_gitwork_commands.py`**：git_work 工具的所有实现逻辑，包括提交收集、会话计算、AI 总结生成
- **`git_catalog_commands.py`**：git_catalog 工具的所有实现逻辑，包括 GitHub API 调用、数据收集和格式化

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
| DeepSeek | `DEEPSEEK_API_KEY`          | `DEEPSEEK_API_URL`、`DEEPSEEK_MODEL` | 默认 URL `https://api.deepseek.com/v1/chat/completions`，默认模型 `deepseek-chat`。 |
| OpenGPT  | `OPENGPT_API_KEY`           | `OPENGPT_API_URL`、`OPENGPT_MODEL` | 默认 URL `https://api.opengpt.com/v1/chat/completions`，默认模型 `gpt-4.1-mini`（可被环境变量或请求参数覆盖）。 |

若需要连接兼容 OpenAI 格式的其他服务，可通过设置 URL 与模型名称实现。

> **注意**：`git_flow` 工具仅支持 `deepseek` 和 `opengpt` 两种提供者。`git_work` 工具的 AI 总结功能支持 `deepseek` 和 `openai`（通过 `OPENAI_API_KEY` 环境变量）。

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

## 📊 `git_work` 工作日志生成

`git_work` 工具可以从本地仓库、GitHub 或 Gitee 收集 Git 提交记录，生成结构化的 Markdown 工作日志。它支持：

- **多数据源**：支持本地仓库路径、GitHub 仓库（`OWNER/REPO`）、Gitee 仓库
- **时间范围**：支持指定时间范围（`since`/`until`）或最近 N 天（`days`）
- **作者过滤**：可以按作者姓名或邮箱过滤提交
- **工作会话分析**：自动计算工作会话，识别提交的连续性和时间间隔
- **并行工作检测**：在多项目模式下，可以检测跨项目的并行工作时间段
- **AI 总结生成**：可选择性地使用 DeepSeek 或 OpenAI 生成中文工作总结

### 环境变量

| 用途 | 环境变量 | 是否必填 | 说明 |
| ---- | -------- | -------- | ---- |
| AI 总结（DeepSeek） | `DEEPSEEK_API_KEY` | 条件必填 | DeepSeek API Key（使用 DeepSeek 时必填） |
| AI 总结（OpenAI） | `OPENAI_API_KEY` | 条件必填 | OpenAI API Key（使用 OpenAI 时必填） |
| GitHub 仓库访问 | `GITHUB_TOKEN` | 条件必填 | GitHub Personal Access Token（访问私有仓库时必填） |
| Gitee 仓库访问 | `GITEE_TOKEN` | 条件必填 | Gitee Personal Access Token（访问私有仓库时必填） |

> **详细说明**：完整的环境变量配置指南请参考 [`docs/environment-variables.md`](docs/environment-variables.md)，包含按工具分类的配置说明和使用场景示例。

### 工具参数

`git_work` 接口签名如下：

```jsonc
{
  "repo_paths": ["/path/to/repo"],      // 本地仓库路径列表
  "github_repos": ["owner/repo"],       // GitHub 仓库列表（格式：OWNER/REPO）
  "gitee_repos": ["owner/repo"],        // Gitee 仓库列表（格式：OWNER/REPO）
  "since": "2024-11-01",                // 起始时间（ISO 格式或 YYYY-MM-DD）
  "until": "2024-11-07",                // 结束时间（ISO 格式或 YYYY-MM-DD）
  "days": 7,                            // 最近 N 天（覆盖 since/until）
  "author": "John Doe",                 // 作者过滤（可选）
  "session_gap_minutes": 60,            // 工作会话间隔（分钟，默认 60）
  "title": "Work Log",                  // 日志标题（可选）
  "add_summary": false,                 // 是否生成 AI 总结（默认 false）
  "provider": "deepseek",               // AI 提供者：openai 或 deepseek
  "model": "deepseek-chat",             // 模型名称（可选，覆盖默认值）
  "system_prompt": "...",               // 自定义系统提示词（可选）
  "temperature": 0.3                     // 温度参数（0.0-2.0，默认 0.3）
}
```

调用成功会返回如下结构：

```jsonc
{
  "exit_code": 0,
  "stdout": "# Work Log\n\n## 2024-11-01 (5 commits)\n...",
  "stderr": ""
}
```

`stdout` 包含完整的 Markdown 格式工作日志，如果启用了 `add_summary`，会在日志末尾包含 AI 生成的中文总结。

## 🔍 `git_catalog` GitHub 仓库目录查询

`git_catalog` 工具提供了统一的 GitHub 活动/仓库目录查询接口，支持 7 个子命令查询 GitHub 仓库和提交活动。

### 功能特性

- **跨仓库提交查询**：查询指定作者在多个仓库中的提交记录（明细）
- **仓库作者统计**：查询指定仓库中多个作者的提交记录（明细）
- **作者活跃仓库列表**：列出指定作者活跃的仓库及其提交数
- **仓库活跃作者列表**：列出指定仓库中的活跃作者及其提交数
- **关键词搜索仓库**：根据关键词、语言、Star 数等条件搜索仓库
- **组织仓库列表**：列出指定组织的所有仓库
- **用户拥有/Star 项目列表**：列出指定用户拥有或 Star 的仓库，支持合并查询和多种过滤排序选项

### 环境变量

| 用途 | 环境变量 | 是否必填 | 说明 |
| ---- | -------- | -------- | ---- |
| GitHub API 访问 | `GITHUB_TOKEN` | 可选但强烈建议 | GitHub Personal Access Token。未设置时使用匿名访问（速率限制 60/h），设置后可提高到 5000/h 并访问私有仓库 |

> **详细说明**：完整的环境变量配置指南请参考 [`docs/environment-variables.md`](docs/environment-variables.md)，包含按工具分类的配置说明和使用场景示例。

### 工具参数

`git_catalog` 接口签名如下：

```jsonc
{
  "cmd": "search_repos" | "org_repos" | "cross_repos" | "repo_authors" | "repos_by_author" | "authors_by_repo" | "user_repos",
  "args": {
    // 参数取决于 cmd 值，详见下方说明
  }
}
```

#### 子命令说明

**1. `search_repos` - 关键词检索仓库**
- `keyword`*: 关键词（匹配 name/description/readme）
- `language`: 语言限定（如 "Python", "C++", "TypeScript"）
- `min_stars`: 最小 Star 数
- `pushed_since`: 最近活跃起始时间
- `topic`: 限定 topic
- `owner`: 限定用户或组织域
- `sort`: 排序字段（"updated" | "stars" | "forks"）
- `order`: 排序方向（"desc" | "asc"）
- `limit`: 最多返回条数（1-2000，默认 200）

**2. `org_repos` - 组织仓库列表**
- `org`*: 组织名
- `repo_type`: 仓库类型（"all" | "public" | "private" | "forks" | "sources" | "member"）
- `include_archived`: 是否包含 archived 仓库（默认 false）
- `sort`: 排序字段（"updated" | "pushed" | "full_name"）
- `limit`: 最多返回条数（1-5000，默认 500）

**3. `cross_repos` - 不同仓库同一作者（提交明细）**
- `author_login`: 作者 GitHub 登录名
- `author_email`: 作者邮箱（更稳定）
- `owner`: 枚举此 owner 的仓库（用户或组织）
- `repo_type`: 仓库类型（"owner" | "member" | "all" | "public" | "private"）
- `max_per_repo`: 每仓最多抓取条数（1-5000，默认 1000）
- `since`: 起始时间（ISO 或日期格式）
- `until`: 结束时间（ISO 或日期格式）

**4. `repo_authors` - 同一仓库不同作者（提交明细）**
- `repo_full`*: 仓库全名（格式 "owner/name"）
- `authors_login`: 作者登录名列表
- `authors_emails`: 作者邮箱列表
- `max_per_author`: 每作者最多抓取条数（1-5000，默认 1000）
- `since`: 起始时间
- `until`: 结束时间

**5. `repos_by_author` - 同一作者在哪些仓库（列表）**
- `author_login`: 作者登录名
- `author_email`: 作者邮箱
- `owner`: 枚举此 owner 的仓库
- `repo_type`: 仓库类型
- `min_commits`: 最小提交数阈值（1-10000，默认 1）
- `since`: 起始时间
- `until`: 结束时间

**6. `authors_by_repo` - 同一仓库活跃作者（列表）**
- `repo_full`*: 仓库全名
- `prefer`: 作者主键偏好（"login" | "email" | "name"）
- `min_commits`: 最小提交数阈值（1-10000，默认 1）
- `since`: 起始时间
- `until`: 结束时间

**7. `user_repos` - 作者拥有或 Star 的项目列表**
- `login`*: GitHub 用户登录名
- `mode`: 查询模式（"owned" | "starred" | "both"，默认 "both"）
- `include_private`: 是否包含私有仓库（需要 token 权限，默认 false）
- `include_archived`: 是否包含 archived 仓库（默认 true）
- `include_forks`: 是否包含 fork 仓库（默认 true）
- `sort`: 排序字段（"updated" | "pushed" | "full_name" | "stars"）
- `order`: 排序方向（"desc" | "asc"）
- `limit`: 最多返回条数（1-5000，默认 500）

> 字段标记说明：* 表示必填字段。

调用成功会返回如下结构：

```jsonc
{
  "exit_code": 0,
  "count": 10,
  "rows": [
    {
      // 字段取决于子命令类型
      // search_repos/org_repos/user_repos: full_name, name, description, language, stargazers_count, etc.
      // cross_repos/repo_authors: repo, sha, date, author_login, title, url, etc.
      // repos_by_author: repo, commits
      // authors_by_repo: repo, author_key, author_login, author_email, commits
    }
  ]
}
```

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

### 最新改进（v1.3）

- ✅ **新增 `git_catalog` 工具**：支持 7 个子命令查询 GitHub 仓库和提交活动
- ✅ **跨仓库提交查询**：支持查询指定作者在多个仓库中的提交记录
- ✅ **仓库作者统计**：支持查询仓库中多个作者的提交活动
- ✅ **关键词搜索**：支持根据关键词、语言、Star 数等条件搜索仓库
- ✅ **组织仓库列表**：支持列出指定组织的所有仓库
- ✅ **用户拥有/Star 项目**：支持查询用户拥有或 Star 的仓库，支持合并查询和去重
- ✅ **速率限制优化**：优化 API 调用频率，减少速率限制检查开销

### 历史版本（v1.2）

- ✅ **新增 `git_work` 工具**：支持从本地/GitHub/Gitee 收集提交并生成工作日志
- ✅ **工作会话分析**：自动计算工作会话，检测并行工作时间
- ✅ **AI 总结生成**：集成 DeepSeek 和 OpenAI，生成中文工作总结
- ✅ **多项目支持**：支持同时分析多个本地或远程仓库

### 历史版本（v1.1）

- ✅ **代码重构**：分离接口定义与实现逻辑，提高代码可维护性
- ✅ **Pydantic V2 迁移**：所有验证器已迁移到 Pydantic V2（`@field_validator` 和 `@model_validator`）
- ✅ **参数类型修复**：修复 `args` 参数类型问题，使用 `dict` 替代 `Optional[Dict[str, Any]]`
- ✅ **完善的错误处理**：为所有工具添加了分类异常处理和友好的错误消息
- ✅ **文档优化**：整理并优化文档结构，移除临时文档

## 📚 文档

- [`docs/code-structure.md`](docs/code-structure.md) - 代码结构详细说明
- [`docs/environment-variables.md`](docs/environment-variables.md) - **环境变量详细说明**（按工具和功能分类）
- [`docs/git-cheatsheet.md`](docs/git-cheatsheet.md) - Git 命令速查表
- [`docs/git_comb.md`](docs/git_comb.md) - Git 组合命令说明
- [`docs/mcp-git-tool.md`](docs/mcp-git-tool.md) - MCP Git 工具设计文档
- [`docs/troubleshooting.md`](docs/troubleshooting.md) - 故障排查指南

## 📄 许可协议

本项目遵循 [MIT License](LICENSE)。
