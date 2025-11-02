好啊！下面给你一套「经典常用」的 **Git 串行（序列化）组合命令**清单。每个组合都给了：

* **用途**（干什么）
* **参数**（你在 MCP `git(cmd,args)` 里可传的高层参数）
* **执行步骤**（按顺序跑什么 Git 命令）
* **可直接复用的脚本模板**（把占位符替换即可）

> 约定占位符：`<branch>`、`<remote>`、`<tag>`、`<msg>`、`<base>`、`<pattern>` 等。
> 安全默认：优先 `--force-with-lease`，危险动作前有检查。

---

## 1) safe_sync（安全同步本地到远端最新）

**用途**：在当前分支上安全拉取最新提交，优先 rebase，保持线性历史。
**参数**：`{ remote:"origin", branch:"<current>", rebase:true }`
**步骤**：fetch → 状态检查 → pull --rebase

```bash
git fetch origin -p
git status --porcelain && git rev-parse --abbrev-ref HEAD
git pull --rebase origin <branch>
```

---

## 2) feature_start（新功能分支启动）

**用途**：从主干切出新分支并建立跟踪。
**参数**：`{ base:"main", name:"feature/xxx", remote:"origin" }`
**步骤**：确保主干最新 → 新建并切换 → 首推设置上游

```bash
git fetch origin -p
git switch main && git pull --rebase origin main
git switch -c <name>
git push -u origin <name>
```

---

## 3) feature_finish（合并功能分支到主干）

**用途**：把功能分支合流回主干（可选 no-ff）。
**参数**：`{ feature:"feature/xxx", target:"main", no_ff:true }`
**步骤**：更新主干 → 合并 → 推送

```bash
git fetch origin -p
git switch <target> && git pull --rebase origin <target>
git merge --no-ff <feature>
git push origin <target>
```

---

## 4) update_from_main（把主干变更合入当前分支）

**用途**：把 main 的最新变更引入当前分支（两种：merge 或 rebase）。
**参数**：`{ base:"main", method:"rebase|merge" }`
**步骤**：fetch → 选择 merge 或 rebase

```bash
git fetch origin -p
# 方案A：rebase（线性历史）
git rebase origin/<base>
# 方案B：merge（保留分叉）
# git merge origin/<base>
```

---

## 5) quick_fix_commit_push（快速修复并推送）

**用途**：一键暂存、提交、推送（紧急修）。
**参数**：`{ msg:"fix: ...", remote:"origin", branch:"<current>", all:true }`
**步骤**：add -A → commit → push

```bash
git add -A
git commit -m "<msg>"
git push origin <branch>
```

---

## 6) hotfix_release（热修 + 打标签 + 发布）

**用途**：在主干上热修并立刻发布一个补丁版本。
**参数**：`{ target:"main", tag:"vX.Y.Z", msg:"hotfix: ...", push_tags:true }`
**步骤**：更新主干 → 提交 → 打标签 → 推送 + 推送标签

```bash
git fetch origin -p
git switch <target> && git pull --rebase origin <target>
git add -A && git commit -m "<msg>"
git tag -a <tag> -m "<msg>"
git push origin <target>
git push origin <tag>     # 或 git push origin --tags
```

---

## 7) rebase_fixup_squash（整理历史：压缩修订）

**用途**：把多个修订压缩到前一个提交（保持历史整洁）。
**参数**：`{ base:"origin/<branch>", autosquash:true }`
**步骤**：自动 fixup/squash

```bash
git fetch origin -p
git rebase -i --autosquash <base>
# 编辑交互式列表：把后续修订改成 fixup/squash
```

---

## 8) clean_workspace（清理工作区）

**用途**：清理未跟踪文件/目录，回滚到干净状态（危险）。
**参数**：`{ mode:"hard", allow_destructive:true }`
**步骤**：确认 → reset --hard → clean -fd

```bash
# 确认你真的要这么做！
git reset --hard
git clean -fd
```

---

## 9) tag_release（打发布标签）

**用途**：对当前 HEAD 打一个语义版本标签并推送。
**参数**：`{ tag:"vX.Y.Z", message:"release vX.Y.Z", push:true }`
**步骤**：tag -a → push tag

```bash
git tag -a <tag> -m "<message>"
git push origin <tag>
```

---

## 10) rollback_last_commit（回滚上一次提交）

**用途**：撤销最新一次提交（生成反向提交，不改历史）。
**参数**：`{ commit:"HEAD", no_edit:true, push:false }`
**步骤**：revert → 可选推送

```bash
git revert --no-edit <commit>
# 可选
# git push origin <branch>
```

---

## 11) recover_deleted_branch（恢复被删分支）

**用途**：用 reflog 找回误删分支。
**参数**：`{ lost_ref:"HEAD@{n}", new_branch:"recover/xxx" }`
**步骤**：查 reflog → 创建分支

```bash
git reflog
git branch <new_branch> <lost_ref>
git switch <new_branch>
```

---

## 12) stash_save_apply（存储/恢复临时改动）

**用途**：保存未完成改动，切分支后再恢复。
**参数**：`{ name:"WIP-xxx", apply_to:"<branch>" }`
**步骤**：stash → 切分支 → apply

```bash
git stash push -m "<name>"
git switch <apply_to>
git stash list
git stash apply   # 或 git stash pop
```

---

## 13) inspect_update_commit（检查修改→更新→提交）

**用途**：你刚提的需求：先检查变更，再同步远端，最后提交（可选推送）。
**参数**：`{ remote:"origin", branch:"<current>", stage:"all|paths", paths:[], msg:"...", push:true }`
**步骤**：status/diff → fetch → pull --rebase → add → commit → 可选 push

```bash
git status -sb
git diff --cached || true
git diff || true
git fetch origin -p
git pull --rebase origin <branch>
# 选择暂存策略
# A) 全部：
git add -A
# B) 指定路径：
# git add <path1> <path2> ...

git commit -m "<msg>"
# 可选推送
# git push origin <branch>
```

---

## 14) first_push（首次推送并设置上游）

**用途**：新分支第一次推到远端，建立跟踪。
**参数**：`{ remote:"origin", branch:"feature/xxx" }`
**步骤**：push -u

```bash
git push -u origin <branch>
```

---

## 15) prune_gone_branches（清理远端已删除的本地分支）

**用途**：清理本地“已失效”的远端跟踪分支。
**参数**：`{ remote:"origin" }`
**步骤**：fetch -p → 删除 gone

```bash
git fetch --all -p
git branch -vv | awk '/: gone]/{print $1}' | xargs -r git branch -D
```

---

# 建议的 MCP 组合 Schema（可直接套用）

以 `inspect_update_commit` 为例（其他组合同理）：

```json
{
  "tool": "git_combo",
  "cmd": "inspect_update_commit",
  "args": {
    "repo_path": "/path/to/repo",
    "remote": "origin",
    "branch": "feature/raytracy",
    "stage": "all",
    "paths": [],
    "message": "feat(core): add ray tracing step for 2D bending angle",
    "push": true,
    "dry_run": false
  }
}
```

* 约束/校验建议：

  * `stage` ∈ {`all`,`paths`}；当 `stage=="paths"` 时 `paths` 必须非空。
  * 允许 `dry_run=true` 返回“将执行的命令序列”而不真正操作。
  * 对破坏性子步骤（如 `clean/reset --hard`）要求 `allow_destructive=true`。

---

如果你愿意，我可以把这些组合命令**做成一个可跑的 `git_combo` MCP 工具**（TypeScript 或 Python 版本都行）：

* 提供 **Zod/Pydantic** 的参数校验；
* 每个组合输出结构化 `steps[]` 执行结果（stdout/stderr/exit_code），便于 UI 呈现；
* 内置“提交信息生成器”（结合你的 GNSS/RayTracy 术语库）；
* 带 **dry-run** 与 **回滚点**（记录进入组合前的 `HEAD`）。
