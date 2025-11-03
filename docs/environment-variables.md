# 环境变量说明

本文档详细说明项目中使用的所有环境变量及其对应的功能。

## 环境变量概览

| 环境变量 | 功能模块 | 是否必填 | 说明 |
|---------|---------|---------|------|
| `DEEPSEEK_API_KEY` | `git_flow`、`work_log` | 条件必填 | DeepSeek API Key，使用 DeepSeek 时必填 |
| `DEEPSEEK_API_URL` | `git_flow`、`work_log` | 可选 | DeepSeek API 端点，默认：`https://api.deepseek.com/v1/chat/completions` |
| `DEEPSEEK_MODEL` | `git_flow`、`work_log` | 可选 | DeepSeek 模型名称，默认：`deepseek-chat` |
| `OPENGPT_API_KEY` | `git_flow` | 条件必填 | OpenGPT API Key，`git_flow` 使用 OpenGPT 时必填 |
| `OPENGPT_API_URL` | `git_flow` | 可选 | OpenGPT API 端点，默认：`https://api.opengpt.com/v1/chat/completions` |
| `OPENGPT_MODEL` | `git_flow` | 可选 | OpenGPT 模型名称，默认：`gpt-4.1-mini` |
| `OPENAI_API_KEY` | `work_log` | 条件必填 | OpenAI API Key，`work_log` 使用 OpenAI 时必填 |
| `GITHUB_TOKEN` | `work_log` | 条件必填 | GitHub Personal Access Token，访问私有 GitHub 仓库时必填 |
| `GITEE_TOKEN` | `work_log` | 条件必填 | Gitee Personal Access Token，访问私有 Gitee 仓库时必填 |

## 按工具分类

### `git` 工具

**无需任何环境变量**

`git` 工具只执行本地 Git 命令，不需要任何外部服务或 API。

### `git_flow` 工具

`git_flow` 工具用于生成提交信息或执行计划，需要配置 LLM 提供者的 API Key。

#### DeepSeek 配置（推荐）

```bash
export DEEPSEEK_API_KEY="sk-xxxxx"                    # 必填
export DEEPSEEK_API_URL="https://api.deepseek.com/v1/chat/completions"  # 可选
export DEEPSEEK_MODEL="deepseek-chat"                 # 可选
```

**使用场景**：
- `action=generate_commit_message` 且 `provider=deepseek`（默认）
- `action=combo_plan` 且 `provider=deepseek`（默认）

#### OpenGPT 配置

```bash
export OPENGPT_API_KEY="your-opengpt-key"             # 必填
export OPENGPT_API_URL="https://api.opengpt.com/v1/chat/completions"    # 可选
export OPENGPT_MODEL="gpt-4.1-mini"                  # 可选
```

**使用场景**：
- `action=generate_commit_message` 且 `provider=opengpt`
- `action=combo_plan` 且 `provider=opengpt`

### `work_log` 工具

`work_log` 工具的环境变量分为两类：

#### 1. AI 总结生成

如果 `work_log` 的 `add_summary=true`，需要配置以下之一：

**DeepSeek（推荐）**：
```bash
export DEEPSEEK_API_KEY="sk-xxxxx"                    # 必填
export DEEPSEEK_MODEL="deepseek-chat"                 # 可选，默认值
```

**OpenAI（备选）**：
```bash
export OPENAI_API_KEY="sk-xxxxx"                      # 必填
```

**使用场景**：
- `work_log` 调用中设置 `add_summary=true`
- 根据 `provider` 参数选择对应的 API Key

#### 2. 远程仓库访问

如果 `work_log` 需要访问 GitHub 或 Gitee 仓库：

**GitHub 仓库访问**：
```bash
export GITHUB_TOKEN="ghp_xxxxx"                       # 访问私有仓库时必填
```

**使用场景**：
- `work_log` 调用中包含 `github_repos` 参数
- 访问私有仓库时必填
- 访问公开仓库时可选（但建议设置以避免 API 速率限制）

**Gitee 仓库访问**：
```bash
export GITEE_TOKEN="your-gitee-token"                 # 访问私有仓库时必填
```

**使用场景**：
- `work_log` 调用中包含 `gitee_repos` 参数
- 访问私有仓库时必填

## 配置示例

### 场景 1：仅使用 `git` 工具

```bash
# 无需配置任何环境变量
```

### 场景 2：使用 `git_flow` 生成提交信息（DeepSeek）

```bash
export DEEPSEEK_API_KEY="sk-xxxxx"
```

### 场景 3：使用 `work_log` 生成本地仓库工作日志（不含 AI 总结）

```bash
# 无需配置任何环境变量
```

### 场景 4：使用 `work_log` 生成多仓库工作日志（含 AI 总结和 GitHub）

```bash
# AI 总结（DeepSeek）
export DEEPSEEK_API_KEY="sk-xxxxx"

# GitHub 仓库访问
export GITHUB_TOKEN="ghp_xxxxx"
```

### 场景 5：完整配置（所有功能）

```bash
# git_flow 和 work_log 的 LLM（DeepSeek）
export DEEPSEEK_API_KEY="sk-xxxxx"

# git_flow 的备选 LLM（OpenGPT）
export OPENGPT_API_KEY="your-opengpt-key"

# work_log 的备选 LLM（OpenAI）
export OPENAI_API_KEY="sk-xxxxx"

# 远程仓库访问
export GITHUB_TOKEN="ghp_xxxxx"
export GITEE_TOKEN="your-gitee-token"
```

## 错误处理

如果缺少必需的环境变量，工具会返回友好的错误消息：

- **缺少 API Key**：`"错误：未提供 DeepSeek API key。请设置环境变量 DEEPSEEK_API_KEY"`
- **缺少 GitHub Token**：访问私有仓库时会失败，提示需要设置 `GITHUB_TOKEN`
- **缺少 Gitee Token**：访问私有仓库时会失败，提示需要设置 `GITEE_TOKEN`

## 安全建议

1. **不要将 API Key 或 Token 提交到代码仓库**
   - 使用环境变量或密钥管理工具（如 `dotenv`、AWS Secrets Manager 等）
   - 将包含敏感信息的配置文件添加到 `.gitignore`

2. **使用最小权限原则**
   - GitHub/Gitee Token 只需要 `repo` 权限（访问私有仓库）
   - 定期轮换 API Key 和 Token

3. **在不同环境使用不同的配置**
   - 开发环境：使用测试 API Key
   - 生产环境：使用生产 API Key，并限制访问范围

## 相关文档

- [README.md](../README.md) - 快速开始指南
- [git_flow 工具说明](../README.md#-git_flow-自动化能力)
- [work_log 工具说明](../README.md#-work_log-工作日志生成)

