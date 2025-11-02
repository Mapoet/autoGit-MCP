# Git 常用命令速查

本速查表覆盖日常工作中最常用的 Git 命令，沿用了 `guide.md` 中的结构，并补充了上下文说明与典型参数组合。建议配合 `git --help <cmd>` 获取更详细的选项说明。

## 工作区与暂存区

| 任务 | 命令 | 常见参数/示例 |
| ---- | ---- | ------------- |
| 查看当前状态 | `git status` | `git status -sb`（精简输出，显示分支与跟踪状态） |
| 暂存变更 | `git add <path>` | `git add -A`（所有改动）<br>`git add -p`（交互式分块暂存） |
| 取消暂存 | `git reset <path>` | `git reset`（取消全部暂存）<br>`git restore --staged <path>`（更直观的写法） |
| 清理未跟踪文件 | `git clean` | `git clean -fd`（删除未跟踪文件与目录，危险操作，慎用） |
| 暂存工作现场 | `git stash` | `git stash push -m "wip"`（带备注保存）<br>`git stash list` / `git stash pop` |

## 提交与历史

| 任务 | 命令 | 常见参数/示例 |
| ---- | ---- | ------------- |
| 提交当前暂存区 | `git commit -m "msg"` | `git commit -a -m "msg"`（自动暂存已跟踪文件） |
| 修改上一条提交 | `git commit --amend` | `git commit --amend -m "new msg"`（覆写提交信息） |
| 查看历史 | `git log` | `git log --oneline --graph --decorate --all`（可视化图形历史）<br>`git log -p <path>`（查看单文件历史差异） |
| 查看差异 | `git diff` | `git diff --cached`（暂存区 vs. HEAD）<br>`git diff HEAD~1..HEAD`（两个提交之间的差异） |
| 还原某次提交 | `git revert <hash>` | `git revert --no-edit <hash>`（保留默认信息） |
| 回退 HEAD | `git reset --soft HEAD~1` | `git reset --mixed <hash>`（默认模式，保留文件改动）<br>`git reset --hard <hash>`（危险：丢弃所有改动） |

## 分支与协作

| 任务 | 命令 | 常见参数/示例 |
| ---- | ---- | ------------- |
| 查看分支 | `git branch` | `git branch -vv`（显示跟踪分支和最新提交） |
| 新建分支 | `git branch <new>` | `git switch -c <new>`（一条命令创建并切换） |
| 切换分支 | `git switch <branch>` | 旧写法：`git checkout <branch>` |
| 合并分支 | `git merge <branch>` | `git merge --no-ff <branch>`（保留 merge commit）<br>`git merge --ff-only`（仅允许 fast-forward） |
| 变基 | `git rebase <upstream>` | `git rebase -i <base>`（交互式重排提交）<br>`git rebase --continue/--abort`（冲突处理） |
| 抓取远程更新 | `git fetch` | `git fetch --all -p`（同步所有远程并清理失效分支） |
| 拉取并整合 | `git pull` | `git pull --rebase`（先抓取再变基，保持线性历史） |
| 推送到远程 | `git push` | `git push -u origin <branch>`（设置上游）<br>`git push --force-with-lease`（安全强制推送） |
| 远程仓库管理 | `git remote -v` | `git remote add origin <url>`<br>`git remote set-url origin <url>` |
| 子模块 | `git submodule update --init --recursive` | 初始化并更新所有子模块 |
| 标签管理 | `git tag v1.2.3` | `git tag -a v1.2.3 -m "release"`（注解标签）<br>`git push origin --tags` |

> **经验法则**：团队协作时常用“拉取用 `git pull --rebase`，合流用 `git merge --no-ff` 或通过 PR”，可以让主干历史更加整洁。

---

### 常见排错技巧

* 使用 `git status` 确认当前分支、暂存区与工作区的状态。
* 通过 `git diff --name-status` 快速查看变动文件列表及状态。
* 冲突解决后依次执行 `git add <冲突文件>` → `git rebase --continue`（或 `git merge --continue`）。
* 若误删或覆盖，尝试 `git reflog` 查找历史引用，再使用 `git checkout` 或 `git reset` 恢复。

---

更多场景化流程（如同步上游、首推新分支、安全推送）可参考 [MCP Git 工具说明](mcp-git-tool.md)。
