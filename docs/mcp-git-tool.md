# MCP Git 工具设计与使用说明

本文档基于 `guide.md` 中的设计草案，给出将常用 Git 操作封装为单一 MCP 工具 `git(cmd, args)` 的完整方案，包含输入/输出 schema、安全策略以及在仓库中实现的 Python 代码概览。

## 设计目标

1. **统一接口**：所有 Git 操作统一走 `cmd + args` 的受控枚举，避免任意 shell 命令。
2. **强校验**：使用 Pydantic（或类似机制）对参数做类型、必填、互斥/依赖校验。
3. **安全可控**：对破坏性命令（如 `reset --hard`、`clean -fd`、`push --force`）增加显式开关；提供 dry-run；默认选择安全选项。
4. **结构化输出**：返回 `exit_code`、`stdout`、`stderr` 与可选的 `parsed` 字段，便于客户端展示和逻辑判断。
5. **可扩展**：可快速新增命令或组合动作（如 `sync`、`push_with_set_upstream`、`release_tag` 等）。

## 输入 Schema

```jsonc
{
  "repo_path": "/path/to/repo",      // 必填，执行目录，需存在 .git
  "cmd": "commit",                   // 枚举：status/add/commit/.../clean
  "args": {                           // 各命令专属参数对象
    "message": "feat: add solver",
    "all": true,
    "amend": false,
    "signoff": false
  },
  "dry_run": false,                   // true: 仅返回即将执行的命令
  "allow_destructive": false,         // true: 才允许 reset --hard / clean -fd 等
  "timeout_sec": 120                  // 子进程超时
}
```

常见命令与参数示例：

| cmd | args 示例 |
| --- | --------- |
| `status` | `{ "short": true }` → `git status -sb` |
| `add` | `{ "paths": ["."], "patch": false, "all": false }` |
| `commit` | `{ "message": "...", "all": false, "amend": false, "no_verify": false, "signoff": false }` |
| `pull` | `{ "remote": "origin", "branch": null, "rebase": true }` |
| `push` | `{ "remote": "origin", "branch": null, "set_upstream": true, "force": false, "force_with_lease": false, "tags": false }` |
| `merge` | `{ "branch": "feature", "no_ff": true, "squash": false, "ff_only": false }` |
| `rebase` | `{ "upstream": "origin/main", "interactive": false, "autosquash": true, "continue": false, "abort": false }` |
| `diff` | `{ "cached": false, "name_only": false, "against": "HEAD" }` |
| `reset` | `{ "mode": "soft", "target": "HEAD~1" }`（若 `mode` 为 `hard` 需 `allow_destructive=true`） |
| `clean` | `{ "force": false, "dirs": false, "interactive": false }`（若 `force` 或 `dirs` 为真需 `allow_destructive=true`） |

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

## 与提示词生成的联动

可额外暴露工具 `gen_commit_message`：先调用 `git diff` 收集改动，再利用 LLM 生成符合团队规范的提交信息，与 `git` 工具结合形成自动提交流程。

---

更多最佳实践请参考 [Git 常用命令速查](git-cheatsheet.md)。
