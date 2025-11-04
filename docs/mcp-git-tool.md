# MCP Git 工具设计与使用说明

本文档基于 `guide.md` 中的设计草案，给出将常用 Git 操作封装为单一 MCP 工具 `git(cmd, args)` 的完整方案，包含输入/输出 schema、安全策略以及在仓库中实现的 Python 代码概览。

## 设计目标

1. **统一接口**：所有 Git 操作统一走 `cmd + args` 的受控枚举，避免任意 shell 命令。
2. **强校验**：使用 Pydantic（或类似机制）对参数做类型、必填、互斥/依赖校验。
3. **安全可控**：对破坏性命令（如 `reset --hard`、`clean -fd`、`push --force`）增加显式开关；提供 dry-run；默认选择安全选项。
4. **结构化输出**：返回 `exit_code`、`stdout`、`stderr` 与可选的 `parsed` 字段，便于客户端展示和逻辑判断。
5. **可扩展**：可快速新增命令或组合动作（如 `sync`、`push_with_set_upstream`、`release_tag` 等）。

## 输入 Schema

### 字段说明

```jsonc
{
  "repo_path": "/path/to/repo",      // 必填，执行目录，需存在 .git
  "cmd": "commit",                   // 见下方支持的 Git 子命令枚举
  "args": {                           // 针对 cmd 的参数对象
    "message": "feat: add solver",
    "all": true,
    "amend": false,
    "signoff": false
  },
  "dry_run": false,                   // true: 仅返回计划执行的命令
  "allow_destructive": false,         // true: 放行 reset --hard / clean -fd / push --force 等危险动作
  "timeout_sec": 120                  // 子进程执行超时（秒）
}
```

- **repo_path**：执行 Git 的工作目录；会在运行前校验 `repo_path/.git` 是否存在。
- **cmd**：受控枚举，见下方表格；所有命令均通过参数映射生成实际 CLI。
- **args**：每个 cmd 的专属参数对象，采用结构化字段而非拼接字符串。
- **dry_run**：对危险动作（commit/merge/reset/revert/clean）返回 `DRY-RUN: git …`，便于预览。
- **allow_destructive**：默认拒绝破坏性命令，显式开启后才会透传 `--force`、`--hard` 等选项。
- **timeout_sec**：超时即返回非零 exit_code，并在 `stderr` 中提示。

### cmd 与参数列表

> 表格中的“默认值”指未显式传入时的安全默认行为。

| cmd | 主要功能 | 参数字段（部分可选） | 默认值/说明 |
| --- | --- | --- | --- |
| `status` | 查看仓库状态 | `short: bool`, `branch: bool` | `short/branch` 任一为真时使用 `-sb` |
| `add` | 暂存文件 | `paths: str \| List[str]`, `all: bool`, `patch: bool` | 默认暂存当前目录；`all=true` 等价 `-A` |
| `commit` | 创建提交 | `message: str*`, `all: bool`, `amend: bool`, `no_verify: bool`, `signoff: bool` | `message` 必填；`all=true` 等价 `-a` |
| `pull` | 拉取远端 | `remote: str`, `branch: str`, `rebase: bool` | 默认 `remote=origin`、`rebase=true` |
| `push` | 推送变更 | `remote: str`, `branch: str`, `set_upstream: bool`, `force_with_lease: bool`, `force: bool`, `tags: bool` | `force` 需 `allow_destructive=true` |
| `fetch` | 更新远端引用 | `remote: str`, `all: bool`, `prune: bool` | 默认 `prune=true` |
| `merge` | 合并分支 | `branch: str*`, `no_ff: bool`, `ff_only: bool`, `squash: bool` | 默认 `no_ff=true` |
| `rebase` | 变基操作 | `upstream: str*`, `interactive: bool`, `autosquash: bool`, `continue: bool`, `abort: bool` | `continue/abort` 互斥；默认 `autosquash=true` |
| `diff` | 查看差异 | `cached: bool`, `name_only: bool`, `against: str` | 默认与 HEAD 比较 |
| `log` | 查看历史 | `oneline: bool`, `graph: bool`, `decorate: bool`, `all: bool`, `max_count: int` | 默认开启 oneline/graph/decorate |
| `branch` | 分支管理 | `create: str`, `delete: str`, `force: bool`, `verbose: bool` | 默认列出分支并附带跟踪信息 |
| `switch` | 切换分支 | `branch: str*`, `create: bool` | `create=true` 等价 `git switch -c` |
| `tag` | 标签管理 | `name: str`, `annotate: bool`, `message: str`, `delete: str`, `list: bool` | `annotate=true` 需提供 `message` |
| `reset` | 重置 HEAD | `mode: "soft|mixed|hard"`, `target: str` | `mode=hard` 需 `allow_destructive=true` |
| `revert` | 生成反向提交 | `commit: str*`, `no_edit: bool` | 默认 `--no-edit` |
| `clean` | 清理工作区 | `force: bool`, `dirs: bool`, `interactive: bool` | `force/dirs` 任一为真需 `allow_destructive=true` |
| `remote` | 远端管理 | `action: "list|add|remove|rename|set_url|prune"`, `name: str`, `url: str`, `new_name: str`, `verbose: bool` | `action=list` 时默认 `-v` |
| `stash` | stash 操作 | `action: "list|push|apply|pop|drop|clear"`, `message: str`, `include_untracked: bool`, `all: bool`, `pathspec`, `ref: str` | `drop/clear` 需 `allow_destructive=true` |
| `submodule` | 子模块管理 | `action: "update|sync|status"`, `init: bool`, `recursive: bool`, `path: str` | `update` 默认 `--init --recursive` |

> 带 `*` 的字段为必填。所有参数都会在 Pydantic 层做类型校验并给出友好的错误提示。

## 输出 Schema

```jsonc
{
  "exit_code": 0,
  "stdout": "...",
  "stderr": "",
  "parsed": {
    "summary": "Committed 2 files to feat/x",
    "changed_files": ["src/main.py", "README.md"]
  }
}
```

`parsed` 字段根据命令类型可包含：

* `status` → `branch`、`ahead`/`behind`、`changed_files`；
* `log` → 最近提交列表；
* `diff` → 变更文件列表、统计信息；
* 复合命令（如 `sync`）可附带执行步骤与结果摘要。

## 关键安全策略

1. **仓库校验**：执行前确认 `repo_path/.git` 存在；必要时可额外验证当前 HEAD。
2. **危险命令保护**：
   * `reset --hard`、`clean -fd`、`push --force` 默认拒绝，除非 `allow_destructive=true`。
   * `force` 推送优先使用 `--force-with-lease`。
3. **Dry-Run**：对 `commit`、`merge`、`rebase`、`reset`、`revert`、`clean` 等命令支持 dry-run，返回计划执行的命令串。
4. **顺序化操作**：复合命令（如 `sync`）内部先执行 `fetch`，再判断是否需要 `rebase/merge`。
5. **超时与异常处理**：超时或异常时返回非零 exit_code，并在 `stderr` 中提供可操作建议。

## Python 最小实现概览

完整实现位于 [`src/git_tool/server.py`](../src/git_tool/server.py)。核心模块由以下几部分组成：

* `Cmd`：枚举所有受支持的 Git 子命令，确保调用方只能使用白名单操作。
* `GitInput`：Pydantic 模型，验证仓库路径、超时时间等字段是否有效。
* `_*_map_*` 映射函数：针对每个 `cmd` 将结构化参数转换为 CLI 参数，必要时调用 `_ensure_safe` 拦截危险选项。
* `_run_git`：统一子进程执行逻辑，返回 `exit_code/stdout/stderr`。
* `git` 工具函数：通过 `@server.tool()` 暴露，支持 dry-run 并返回 JSON 字符串结果。

以下代码节选展示了危险操作的防护逻辑：

```python
def _map_push(args: Dict[str, Any], allow_destructive: bool) -> List[str]:
    remote = args.get("remote", "origin")
    branch = args.get("branch")
    set_upstream = args.get("set_upstream")
    argv = ["push", "-u", remote, branch] if set_upstream and branch else ["push", remote]
    if branch and not set_upstream:
        argv.append(branch)
    if args.get("force_with_lease"):
        argv.append("--force-with-lease")
    if args.get("force"):
        _ensure_safe(True, allow_destructive, "push --force")
        argv.append("--force")
    if args.get("tags"):
        argv.append("--tags")
    return argv


def _map_clean(args: Dict[str, Any], allow_destructive: bool) -> List[str]:
    force = bool(args.get("force"))
    dirs = bool(args.get("dirs"))
    _ensure_safe(force or dirs, allow_destructive, "clean -fd")
    argv = ["clean"]
    if force:
        argv.append("-f")
    if dirs:
        argv.append("-d")
    if args.get("interactive"):
        argv.append("-i")
    return argv
```

`git` 工具函数在 dry-run 时会返回 `DRY-RUN: git <cmd>`，实际执行时返回结构化的 `exit_code/stdout/stderr` JSON，方便客户端进一步解析或展示。

## 复合命令建议

* `sync`：`fetch --prune` → 判断是否落后 → `rebase` 或 `merge`。
* `push_with_set_upstream`：首次推送本地分支，并在成功后返回远程 URL。
* `release_tag`：创建注解标签，推送到远程，同时返回最新的 changelog。

### `git_flow` combo_plan 输入补充

`git_flow` 的 `combo_plan` 行为会基于 [`src/git_tool/git_combos.py`](../src/git_tool/git_combos.py) 中的模板，为用户生成逐步执行指南。完整的输入结构：

```jsonc
{
  "repo_path": "/path/to/repo",
  "action": "combo_plan",
  "provider": "opengpt",               // 或 deepseek，自定义时可改
  "model": "gpt-4.1-mini",             // 覆盖默认模型
  "system_prompt": "可选自定义系统提示",
  "user_prompt": "可选自定义用户提示",
  "combo_name": "inspect_update_commit", // 必填：combo 标识
  "combo_replacements": {               // 占位符填充
    "branch": "feature/awesome",
    "msg": "feat: add awesome flow"
  },
  "diff_scope": "staged",              // diff 上下文，同提交信息生成
  "extra_context": "我们在处理线上事故，请优先推送",
  "temperature": 0.2
}
```

- **combo_name**：对应 `git_combos.py` 中的键，决定执行流程骨架。
- **combo_replacements**：为脚本模板或步骤里的 `<branch>`、`<msg>` 等占位符赋值；若缺省则在提示词中保留原始占位符，由模型结合上下文补全。
- **system_prompt / user_prompt**：允许用户完全替换默认提示词，用于调整语言、输出格式或强调冲突解决策略（例如要求模型说明解决冲突的办法、给出回滚提示）。
- **diff_scope / extra_context**：与提交信息生成共享逻辑，可让模型参考最新 diff、需求描述等信息，进一步定制执行建议。

若执行过程中可能遇到冲突（如 rebase/merge），可以在自定义提示中明确要求模型给出冲突解决步骤、推荐的 LLM 占位符消息或回退命令。客户端也可以在收到响应后将 `steps` 与 `script` 直接喂回 `git` 工具依次执行，形成自动化流水线。

## 与提示词生成的联动

可额外暴露工具 `gen_commit_message`：先调用 `git diff` 收集改动，再利用 LLM 生成符合团队规范的提交信息，与 `git` 工具结合形成自动提交流程。

## `git_work` 工作日志工具

除了 `git` 和 `git_flow`，项目还提供了 `git_work` 工具，用于从本地仓库、GitHub 或 Gitee 收集提交记录并生成结构化工作日志。

### 功能特性

- **多数据源支持**：
  - 本地仓库路径列表（`repo_paths`）
  - GitHub 仓库列表（`github_repos`，格式：`OWNER/REPO`）
  - Gitee 仓库列表（`gitee_repos`，格式：`OWNER/REPO`）
- **时间范围**：
  - 支持 ISO 格式或 `YYYY-MM-DD` 格式的起始/结束时间
  - 支持指定最近 N 天（`days` 参数）
- **工作会话分析**：
  - 自动计算工作会话（基于提交时间间隔，默认 60 分钟）
  - 在多项目模式下检测并行工作时间段
  - 识别跨项目的同步工作时间
- **AI 总结生成**：
  - 可选使用 DeepSeek 或 OpenAI 生成中文工作总结
  - 支持自定义系统提示词和温度参数
  - 自动包含工作会话统计和并行工作时间信息

### 输入 Schema

```jsonc
{
  "repo_paths": ["/path/to/repo"],      // 本地仓库路径列表（可选）
  "github_repos": ["owner/repo"],       // GitHub 仓库列表（可选）
  "gitee_repos": ["owner/repo"],        // Gitee 仓库列表（可选）
  "since": "2024-11-01",                // 起始时间（ISO 或 YYYY-MM-DD，可选）
  "until": "2024-11-07",                // 结束时间（ISO 或 YYYY-MM-DD，可选）
  "days": 7,                            // 最近 N 天（覆盖 since/until，可选）
  "author": "John Doe",                 // 作者过滤（可选）
  "session_gap_minutes": 60,            // 工作会话间隔（分钟，默认 60）
  "title": "Work Log",                  // 日志标题（可选）
  "add_summary": false,                 // 是否生成 AI 总结（默认 false）
  "provider": "deepseek",               // AI 提供者：openai 或 deepseek
  "model": "deepseek-chat",             // 模型名称（可选）
  "system_prompt": "...",               // 自定义系统提示词（可选）
  "temperature": 0.3                     // 温度参数（0.0-2.0，默认 0.3）
}
```

### 输出 Schema

```jsonc
{
  "exit_code": 0,
  "stdout": "# Work Log\n\n## 2024-11-01 (5 commits)\n...",  // Markdown 格式工作日志
  "stderr": ""
}
```

`stdout` 包含完整的 Markdown 格式工作日志，包括：
- 按日期分组的提交列表
- 每个提交的统计信息（文件、增删行数）
- 工作会话统计（单项目模式）
- 跨项目并行工作时间统计（多项目模式）
- AI 生成的工作总结（如果启用了 `add_summary`）

### 环境变量

- `DEEPSEEK_API_KEY` - DeepSeek API Key（用于 AI 总结）
- `OPENAI_API_KEY` - OpenAI API Key（用于 AI 总结）
- `GITHUB_TOKEN` - GitHub Personal Access Token（访问 GitHub 仓库）
- `GITEE_TOKEN` - Gitee Personal Access Token（访问 Gitee 仓库）

### 使用示例

#### 单项目工作日志

```json
{
  "repo_paths": ["/path/to/repo"],
  "days": 7,
  "author": "John Doe",
  "add_summary": true
}
```

#### 多项目工作日志（包含远程仓库）

```json
{
  "repo_paths": ["/path/to/local/repo"],
  "github_repos": ["owner/repo1", "owner/repo2"],
  "since": "2024-11-01",
  "until": "2024-11-07",
  "session_gap_minutes": 60,
  "add_summary": true,
  "provider": "deepseek"
}
```

---

## `git_catalog` GitHub 仓库目录查询工具

除了 `git`、`git_flow` 和 `git_work`，项目还提供了 `git_catalog` 工具，用于查询 GitHub 仓库和提交活动。

### 功能特性

`git_catalog` 工具提供了统一的 GitHub 活动/仓库目录查询接口，支持 7 个子命令：

1. **`cross_repos`** - 不同仓库同一作者（提交明细）：查询指定作者在多个仓库中的提交记录
2. **`repo_authors`** - 同一仓库不同作者（提交明细）：查询指定仓库中多个作者的提交记录
3. **`repos_by_author`** - 同一作者在哪些仓库（列表）：列出指定作者活跃的仓库及其提交数
4. **`authors_by_repo`** - 同一仓库活跃作者（列表）：列出指定仓库中的活跃作者及其提交数
5. **`search_repos`** - 关键词检索仓库：根据关键词、语言、Star 数等条件搜索仓库
6. **`org_repos`** - 组织仓库列表：列出指定组织的所有仓库
7. **`user_repos`** - 作者拥有或 Star 的项目列表：列出指定用户拥有或 Star 的仓库，支持合并查询和去重

### 输入 Schema

```jsonc
{
  "cmd": "search_repos" | "org_repos" | "cross_repos" | "repo_authors" | 
         "repos_by_author" | "authors_by_repo" | "user_repos",
  "args": {
    // 参数取决于 cmd 值
    // 详见 README.md 中的 git_catalog 工具说明
  }
}
```

### 输出 Schema

```jsonc
{
  "exit_code": 0,
  "count": 10,
  "rows": [
    {
      // 字段取决于子命令类型
      // search_repos/org_repos/user_repos: 
      //   relation (仅 user_repos), full_name, name, owner, description, 
      //   language, stargazers_count, forks_count, archived, private, 
      //   updated_at, pushed_at, html_url
      // cross_repos/repo_authors: 
      //   repo, sha, date, author_login, author_name, author_email, 
      //   committer_login, title, url
      // repos_by_author: 
      //   repo, commits
      // authors_by_repo: 
      //   repo, author_key, author_login, author_email, commits
    }
  ]
}
```

### 环境变量

- `GITHUB_TOKEN` - GitHub Personal Access Token（可选，但强烈建议设置）
  - 未设置时使用匿名访问（速率限制 60/h）
  - 设置后可提高到 5000/h 并访问私有仓库

### 使用示例

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

### 性能优化

- **速率限制保护**：自动检测并处理 GitHub API 速率限制
- **提前退出策略**：在收集足够数据时提前停止，减少不必要的 API 调用
- **批量处理优化**：减少速率限制检查频率（每 10 次检查一次）
- **去重逻辑**：对于 `user_repos` 的 `both` 模式，自动去重（owned 优先级高于 starred）

---

更多最佳实践请参考 [Git 常用命令速查](git-cheatsheet.md)。
