# autoGit-MCP

`autoGit-MCP` 提供了将常见 Git 操作与辅助自动化能力封装为 [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) 工具的实现，便于在智能体或自动化工作流中安全地执行 Git 命令并生成提交说明。

## 功能概览

- **`git` 工具**：将常见 Git 子命令统一为 `cmd + args` 调用，提供参数校验、危险命令防护以及结构化输出，覆盖 `remote`、`stash`、`submodule` 等拓展指令。
- **`git_flow` 工具**：结合仓库 README、Git Diff 与自定义提示词，通过 OpenGPT 或 DeepSeek 等兼容 OpenAI Chat Completions 接口的模型自动生成提交信息等内容，亦可基于预设的 Git 组合命令模板生成执行方案，并支持占位符填充与冲突处理提示。
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
  "prompt_profile": "software_engineering" | "devops" | "product_analysis" | "documentation" | "data_analysis",
  "diff_scope": "staged" | "workspace" | "head",
  "diff_target": "HEAD" // 当 diff_scope 为 head 时使用，默认 HEAD,
  "include_readme": true,
  "max_readme_chars": 4000,
  "max_diff_chars": 8000,
  "extra_context": "其他上下文",
  "temperature": 0.2,
  // --- combo_plan 专用字段 ---
  "combo_name": "safe_sync",            // action 为 combo_plan 时必填
  "combo_replacements": { "branch": "main" } // 可选占位符替换，缺省时会在提示词中保留占位符交由模型补全
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

若调用模型失败，`stderr` 会包含错误描述，同时 `exit_code` 为非零值。无论 `action` 类型如何，均可通过 `system_prompt` 与 `user_prompt` 覆盖默认提示词，从而让模型输出自定义的占位符填充策略、冲突处理建议或额外的安全提醒。

## 提示词模板

项目内置了以下默认模板（可通过参数覆盖）：

- **System Prompt**：`"You are an experienced software engineer who writes Conventional Commits."`
- **User Prompt**：`"请基于以下项目上下文与 diff，生成一条简洁的 Conventional Commit 信息，并给出简短的正文说明。"`

当 `action` 设为 `combo_plan` 时，会默认使用专为 Git 串行组合命令设计的提示词，生成包含“适用场景、逐步说明、脚本模板”的执行指南；你也可以通过 `system_prompt` 与 `user_prompt` 自定义文案风格，或直接设置 `prompt_profile` 选择内置模板（当同时提供自定义 Prompt 时优先使用自定义内容）。

调用时会自动在用户提示尾部附加 README 摘要与 Git Diff 内容。

### 不同专业领域的提示词模板

为了满足不同角色、任务与工作场景的需求，你可以在调用 `git_flow` 时设置 `prompt_profile` 字段来注入下列模板，也可以手动拷贝后自定义。所有模板均提供了需要在调用前渲染的占位符，便于传入关键上下文：

- `{{repo_summary}}`：当前仓库或子模块的简要描述，可由 README 摘要或人工撰写。
- `{{task_description}}`：本次需求、缺陷或目标的说明。
- `{{diff_snippet}}`：与任务相关的 Git Diff 片段，可结合 `max_diff_chars` 控制长度。
- `{{risk_notice}}`：潜在风险、兼容性或上线限制信息（可选）。
- `{{desired_output}}`：期待的输出形态，例如“生成 Conventional Commit + 说明”或“列出执行步骤”。

> 你可以按需增删占位符，并在发起 MCP 请求前自行替换为实际内容；若保留未替换的占位符，模型会尝试基于上下文补全。

#### 1. 软件工程（实现 / 重构 / 缺陷修复）

- **System Prompt**

  ```text
  You are a senior software engineer specializing in Git-based workflows. Produce safe, review-ready outputs, call out risks, and respect repository conventions surfaced in the context.
  ```

- **User Prompt**

  ```text
  项目概览：
  {{repo_summary}}

  任务目标：
  {{task_description}}

  相关变更：
  {{diff_snippet}}

  风险 / 兼容性提示：
  {{risk_notice}}

  请基于以上信息，输出 {{desired_output}}，并确保：
  1. 给出必要的代码上下文解释；
  2. 指出可能的副作用与测试建议；
  3. 若发现不一致或潜在问题，请明确标注并提出修正思路。
  ```

#### 2. DevOps / 运维自动化

- **System Prompt**

  ```text
  You are a DevOps specialist focused on reliable delivery, CI/CD, and infrastructure automation. Emphasize reproducibility, rollback safety, and observability practices.
  ```

- **User Prompt**

  ```text
  当前服务与仓库信息：
  {{repo_summary}}

  运维 / 部署任务：
  {{task_description}}

  配置或脚本差异：
  {{diff_snippet}}

  约束与风险：
  {{risk_notice}}

  请产出 {{desired_output}}，需包含：
  - 环境或流水线的更新步骤；
  - 监控与验证建议；
  - 回滚策略或故障预案。
  ```

#### 3. 产品 / 需求分析

- **System Prompt**

  ```text
  You are a product strategist skilled at translating business requirements into actionable engineering guidance. Balance user value, feasibility, and measurable outcomes.
  ```

- **User Prompt**

  ```text
  产品背景：
  {{repo_summary}}

  当前需求与痛点：
  {{task_description}}

  相关实现或差异：
  {{diff_snippet}}

  业务限制 / 风险说明：
  {{risk_notice}}

  请围绕 {{desired_output}} 进行分析，需包含：
  1. 用户价值与成功指标；
  2. 方案可行性评估（含依赖与影响范围）；
  3. 对后续迭代或验证的建议。
  ```

#### 4. 文档与知识库维护

- **System Prompt**

  ```text
  You are a technical writer who keeps engineering knowledge bases consistent, concise, and accessible. Maintain tone alignment with existing documentation.
  ```

- **User Prompt**

  ```text
  文档上下文：
  {{repo_summary}}

  更新目标：
  {{task_description}}

  内容差异或待整合信息：
  {{diff_snippet}}

  注意事项：
  {{risk_notice}}

  请输出 {{desired_output}}，并确保：
  - 用词统一且符合既有术语；
  - 给出必要的交叉引用或链接建议；
  - 标注需要人工确认的部分。
  ```

#### 5. 数据分析 / 指标洞察

- **System Prompt**

  ```text
  You are a data analyst experienced in experimental design, metrics interpretation, and communicating insights to mixed audiences.
  ```

- **User Prompt**

  ```text
  数据集与项目背景：
  {{repo_summary}}

  分析诉求：
  {{task_description}}

  代码 / 笔记本差异：
  {{diff_snippet}}

  潜在风险或数据质量提示：
  {{risk_notice}}

  请生成 {{desired_output}}，需要：
  - 概述关键发现与指标波动；
  - 指出假设、前提条件与可能的偏差；
  - 给出下一步验证或可视化建议。
  ```

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
