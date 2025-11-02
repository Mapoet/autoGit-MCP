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

---

更多最佳实践请参考 [Git 常用命令速查](git-cheatsheet.md)。
