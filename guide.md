# 一、Git 常用命令与典型用法

| 任务      | 命令                                        | 常见参数/示例                                      |
| ------- | ----------------------------------------- | -------------------------------------------- |
| 查看状态    | `git status`                              | `git status -sb`（简洁）                         |
| 暂存变更    | `git add <path>`                          | `git add -A`（所有）`git add -p`（交互分块）           |
| 取消暂存    | `git reset <path>`                        | `git reset`（全部取消暂存）                          |
| 提交      | `git commit -m "msg"`                     | `git commit -a -m "msg"`（跳过 add，跟踪文件）        |
| 修改上次提交  | `git commit --amend`                      | `git commit --amend -m "new msg"`            |
| 查看日志    | `git log`                                 | `git log --oneline --graph --decorate --all` |
| 查看差异    | `git diff`                                | `git diff --cached`（暂存区 vs HEAD）             |
| 分支列表    | `git branch`                              | `git branch -vv`                             |
| 新建分支    | `git branch <new>`                        | 或 `git switch -c <new>`                      |
| 切换分支    | `git switch <branch>`                     | 旧写法 `git checkout <branch>`                  |
| 合并      | `git merge <branch>`                      | `git merge --no-ff <branch>`                 |
| 变基      | `git rebase <upstream>`                   | `git rebase -i <base>`（交互式）                  |
| 拉取      | `git pull`                                | `git pull --rebase`                          |
| 推送      | `git push`                                | `git push -u origin <branch>`（设置跟踪）          |
| 抓取远程    | `git fetch`                               | `git fetch --all -p`（清理失效分支）                 |
| 远程列表    | `git remote -v`                           |                                              |
| 设定远程    | `git remote add origin <url>`             | `git remote set-url origin <url>`            |
| 设置上游    | `git branch --set-upstream-to=origin/<b>` |                                              |
| 暂存现场    | `git stash`                               | `git stash pop` / `git stash list`           |
| 标签      | `git tag v1.2.3`                          | `git push origin --tags`                     |
| 回退（软/硬） | `git reset --soft HEAD~1`                 | `git reset --hard <hash>`（危险）                |
| 还原提交    | `git revert <hash>`                       | 产生“反向”提交                                     |
| 清理      | `git clean -fd`                           | 删除未跟踪文件（谨慎）                                  |
| 子模块     | `git submodule update --init --recursive` |                                              |

> 经验法则：团队常用“拉取用 `pull --rebase`，合流用 `merge --no-ff` 或走 PR”，能让主干历史更干净。

---

# 二、把 Git 包成一个 MCP 工具：`git(cmd, args)`

## 1) 设计思想

* **cmd**：受控枚举（白名单），如：`status/add/commit/pull/push/fetch/merge/rebase/diff/log/branch/switch/tag/reset/revert/clean` …
* **args**：每个 cmd 的特定参数对象（强校验、默认值、互斥/依赖关系）。
* **工作目录**：`repo_path` 必填或默认当前项目；所有命令在此目录下执行。
* **安全**：

  * 白名单 + 参数校验（禁止任意 Shell）；
  * 提供 `dry_run`/`confirm` 机制；
  * 对危险命令（`reset --hard`、`clean -fd`）要求明确 `allow_destructive=true` 才可执行。
* **可观测**：返回结构化结果（stdout、stderr、exit_code、parsed），便于客户端展示/判断。
* **可扩展**：附带几个“复合动作”（如 `sync` = `fetch` + `rebase` 或 `merge`；`upstream_set`；`push_with_set_upstream`）。

## 2) 输入/输出 Schema（示例）

**Tool 名称**：`git`

**Input (JSON)**：

```json
{
  "repo_path": "/path/to/repo",
  "cmd": "commit",
  "args": {
    "message": "feat(core): add 2D bending-angle solver",
    "all": true,
    "amend": false,
    "signoff": false
  },
  "dry_run": false,
  "allow_destructive": false,
  "timeout_sec": 120
}
```

**cmd 枚举与 args 约定（示例）**：

* `status`: `{ "short": true }`
* `add`: `{ "paths": ["."], "patch": false, "all": false }`
* `commit`: `{ "message": "<string>", "all": false, "amend": false, "no_verify": false, "signoff": false }`
* `pull`: `{ "remote": "origin", "branch": null, "rebase": true }`
* `push`: `{ "remote": "origin", "branch": null, "set_upstream": true, "force": false, "force_with_lease": false, "tags": false }`
* `fetch`: `{ "remote": "origin", "all": false, "prune": true }`
* `merge`: `{ "branch": "<string>", "no_ff": true, "squash": false, "ff_only": false }`
* `rebase`: `{ "upstream": "origin/main", "interactive": false, "autosquash": true, "continue": false, "abort": false }`
* `diff`: `{ "cached": false, "name_only": false, "against": "HEAD" }`
* `log`: `{ "oneline": true, "max_count": 20, "graph": true, "decorate": true, "all": false }`
* `branch`: `{ "list": true, "create": null, "delete": null, "verbose": true }`
* `switch`: `{ "branch": "<string>", "create": false }`
* `tag`: `{ "name": null, "annotate": false, "delete": null, "list": true }`
* `reset`: `{ "mode": "soft|mixed|hard", "target": "HEAD~1" }`
* `revert`: `{ "commit": "<hash>", "no_edit": true }`
* `clean`: `{ "force": false, "dirs": false, "interactive": false }`

**Output (JSON)**：

```json
{
  "exit_code": 0,
  "stdout": "…raw stdout…",
  "stderr": "",
  "parsed": { "summary": "Committed 3 files to feat/xyz", "changed_files": ["a.cpp","b.hpp"] }
}
```

## 3) cmd → 真实 Git 命令映射（片段）

* `commit`: `git commit [-a] [--amend] [--no-verify] [--signoff] -m "<msg>"`
* `push`: `git push [--set-upstream origin <branch>] [--force-with-lease|--force] [--tags]`
* `pull`: `git pull [--rebase] <remote> <branch>`
* `merge`: `git merge [--no-ff|--ff-only|--squash] <branch>`
* `rebase`: `git rebase [--autosquash] <upstream>` / `git rebase --continue|--abort`
* `reset`: `git reset --<mode> <target>`
* `diff`: `git diff [--cached] [--name-only] <against>`
* `log`: `git log [--oneline] [--graph] [--decorate] [-n <k>] [--all]`

## 4) 关键策略与安全护栏

* **顺序化危险操作**：`pull --rebase` 前先 `fetch`；`merge`/`rebase` 前先确保工作区干净（`git diff --quiet`）。
* **默认温和**：`push` 默认 `--force-with-lease=false`；`reset` 默认拒绝 `--hard`，除非 `allow_destructive=true`。
* **幂等 & 可回退**：在执行“批处理动作”前，自动记录当前 `HEAD`（`rev-parse HEAD`）到输出里。
* **人机协同**：对冲突/失败的情况，返回明确的状态码与可操作建议（继续、放弃、手动介入步骤）。
* **监测/更新**：做一个复合 cmd `sync`：`fetch --prune` → 判断是否落后 → `rebase`（或 `merge`）→ 返回更新摘要。

---

## 5) 最小实现（思路与伪代码）

> 这里给 Python 方向（FastAPI + MCP server）的结构化伪代码，便于快速落地。本质是：校验 → 映射 → 子进程执行 → 标准化输出。

```python
# pip install mcp[all] fastapi uvicorn pydantic
import subprocess, shlex, os, json
from enum import Enum
from pydantic import BaseModel, Field
from mcp.server.fastapi import FastAPIMCPServer

app, server = FastAPIMCPServer("git-mcp")

class Cmd(str, Enum):
    status="status"; add="add"; commit="commit"; pull="pull"; push="push"
    fetch="fetch"; merge="merge"; rebase="rebase"; diff="diff"; log="log"
    branch="branch"; switch="switch"; tag="tag"; reset="reset"; revert="revert"; clean="clean"

class GitInput(BaseModel):
    repo_path: str
    cmd: Cmd
    args: dict = Field(default_factory=dict)
    dry_run: bool = False
    allow_destructive: bool = False
    timeout_sec: int = 120

def run(repo, argv, timeout):
    proc = subprocess.run(
        ["git"] + argv,
        cwd=repo, capture_output=True, text=True, timeout=timeout
    )
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr
    }

def map_args_to_argv(cmd: Cmd, args: dict, allow_destructive: bool):
    a = []
    if cmd == Cmd.commit:
        if args.get("all"): a += ["-a"]
        if args.get("amend"): a += ["--amend"]
        if args.get("no_verify"): a += ["--no-verify"]
        if args.get("signoff"): a += ["--signoff"]
        msg = args.get("message", "")
        a += ["-m", msg]
        return ["commit"] + a

    if cmd == Cmd.push:
        remote = args.get("remote","origin")
        branch = args.get("branch")
        a = ["push", remote] + ([branch] if branch else [])
        if args.get("set_upstream") and branch:
            a = ["push", "-u", remote, branch]
        if args.get("force_with_lease"):
            a += ["--force-with-lease"]
        elif args.get("force", False):
            a += ["--force"]  # consider blocking without allow_destructive
        if args.get("tags"):
            a += ["--tags"]
        return a

    # ……其余命令同理映射……
    # 例如 pull / fetch / merge / rebase / diff / log 等

    raise ValueError(f"unsupported cmd: {cmd}")

@server.tool()
def git(repo_path: str, cmd: str, args: dict | None = None,
        dry_run: bool = False, allow_destructive: bool = False,
        timeout_sec: int = 120) -> str:
    """
    Run controlled git commands in a given repository.
    """
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        return json.dumps({"exit_code": 2, "stderr": "not a git repo"})

    cmd_enum = Cmd(cmd)
    argv = map_args_to_argv(cmd_enum, args or {}, allow_destructive)

    if dry_run and cmd_enum in [Cmd.commit, Cmd.merge, Cmd.reset, Cmd.revert, Cmd.clean]:
        # 简易 dry-run：仅返回计划执行
        return json.dumps({"exit_code": 0, "stdout": f"DRY-RUN: git {' '.join(shlex.quote(x) for x in argv)}"})

    res = run(repo_path, argv, timeout_sec)
    return json.dumps(res)
```

> 你可以加：
>
> * `preflight` 检查（工作树是否干净、是否有跟踪分支等）；
> * `parsed` 字段（解析 `git status -sb`、`log --oneline` 等输出为结构化对象）；
> * 复合动作 `sync/push_with_set_upstream/release_tag` 等；
> * 监控工具（文件系统监听 + 定时 `fetch --prune`），发现落后自动提示或自动 `rebase`。

---

## 6) 组合命令（复合流程）示例

* **同步到上游（安全版）**

  1. `git(fetch, {remote:"origin", prune:true})`
  2. 如果落后：`git(rebase, {upstream:"origin/<branch>", autosquash:true})`
  3. 输出变更摘要（`git(log, {oneline:true, max_count:5})`）

* **首推新分支**

  1. `git(branch, {create:"feat/xxx"})` → `git(switch, {branch:"feat/xxx"})`
  2. `git(commit, {...})` → `git(push, {remote:"origin", branch:"feat/xxx", set_upstream:true})`

* **安全推送**

  * 用 `force_with_lease` 代替 `force`；只有当 `allow_destructive=true` 且确有需要才放开强推。

---

## 7) 与“生成提示词（commit message）”联动

* 额外提供工具 `gen_commit_message(style, from="staged|working", branch)`：

  * 内部先跑 `git diff --cached`（或 `git diff HEAD`）提取摘要 → 喂给你的 LLM／规则引擎 → 输出 `message`；
  * 再由 `git(commit, {message: …})` 执行。
* 你也可以把它做成另一个 MCP 工具，或在同一个 `git` 工具里加 `cmd: "gen_commit_and_commit"` 的复合模式。

---

### 小结

* 上面这套把 Git 统一封装成 `git(cmd, args)` 的 MCP 设计，优点是**强约束 + 易扩展 + 易观测**；
* 你可以先实现 6–8 个最常用 cmd（`status/add/commit/pull/push/fetch/merge/rebase`），再逐步补齐；
* 对危险命令加“明确开关”和“dry-run”，并把**冲突/失败**做成清晰的返回状态与下一步指引。

如果你需要，我可以按照你的项目（RayTracy / Taskflow）的工作流，直接给出**完整可运行的 MCP 服务器仓库模板**（含：命令映射、结构化解析、复合命令、commit 文本生成、以及一个简单的“自动监测与同步”守护进程）。
