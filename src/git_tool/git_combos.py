"""Predefined Git command combos shared with the git_flow tool."""

from __future__ import annotations

from typing import Dict, List, TypedDict


class Combo(TypedDict):
    """Structured description of a Git combo workflow."""

    name: str
    summary: str
    parameters: str
    steps: List[str]
    script: str
    notes: str


def _lines(text: str) -> List[str]:
    """Split a multi-line string into stripped lines, skipping empties."""

    return [line.strip() for line in text.strip().splitlines() if line.strip()]


GIT_COMBOS: Dict[str, Combo] = {
    "safe_sync": {
        "name": "safe_sync",
        "summary": "安全同步当前分支到远端的最新状态，保持线性历史。",
        "parameters": '{ remote:"origin", branch:"<current>", rebase:true }',
        "steps": _lines(
            """
fetch --prune 远端
检查工作区状态与当前分支
以 rebase 方式拉取远端更新
"""
        ),
        "script": """git fetch origin -p\ngit status --porcelain && git rev-parse --abbrev-ref HEAD\ngit pull --rebase origin <branch>""",
        "notes": "优先保证线性历史，执行前需确认工作区干净。",
    },
    "feature_start": {
        "name": "feature_start",
        "summary": "从主干切出新的功能分支并建立上游跟踪。",
        "parameters": '{ base:"main", name:"feature/xxx", remote:"origin" }',
        "steps": _lines(
            """
更新主干分支到最新
创建并切换到新分支
首次推送并设置上游
"""
        ),
        "script": """git fetch origin -p\ngit switch <base> && git pull --rebase origin <base>\ngit switch -c <name>\ngit push -u origin <name>""",
        "notes": "确保 <base> 为团队约定的主干分支，例如 main 或 develop。",
    },
    "feature_finish": {
        "name": "feature_finish",
        "summary": "将功能分支合并回目标主干，可选 no-ff。",
        "parameters": '{ feature:"feature/xxx", target:"main", no_ff:true }',
        "steps": _lines(
            """
更新目标主干
以 --no-ff 合并功能分支
推送主干到远端
"""
        ),
        "script": """git fetch origin -p\ngit switch <target> && git pull --rebase origin <target>\ngit merge --no-ff <feature>\ngit push origin <target>""",
        "notes": "合并前建议在功能分支完成自测并清理历史。",
    },
    "update_from_main": {
        "name": "update_from_main",
        "summary": "将主干最新提交合入当前分支，支持 rebase 或 merge。",
        "parameters": '{ base:"main", method:"rebase|merge" }',
        "steps": _lines(
            """
fetch 更新远端引用
根据选择执行 rebase 或 merge
"""
        ),
        "script": """git fetch origin -p\n# 方案A：rebase（线性历史）\ngit rebase origin/<base>\n# 方案B：merge（保留分叉）\n# git merge origin/<base>""",
        "notes": "选择 rebase 时请确保本地提交尚未推送，避免历史冲突。",
    },
    "inspect_update_commit": {
        "name": "inspect_update_commit",
        "summary": "检查变更、同步远端后提交并可选推送。",
        "parameters": '{ remote:"origin", branch:"<current>", stage:"all|paths", msg:"..." }',
        "steps": _lines(
            """
检查当前变更与暂存区
拉取远端最新并 rebase
按照策略暂存文件
提交并可选推送
"""
        ),
        "script": """git status -sb\ngit diff --cached || true\ngit diff || true\ngit fetch origin -p\ngit pull --rebase origin <branch>\n# 选择暂存策略\ngit add -A\n# 或者 git add <path1> <path2> ...\ngit commit -m "<msg>"\n# git push origin <branch>""",
        "notes": "提交前可根据 diff 决定暂存策略，必要时再推送。",
    },
    "quick_fix_commit_push": {
        "name": "quick_fix_commit_push",
        "summary": "快速暂存、提交并推送紧急修复。",
        "parameters": '{ msg:"fix: ...", remote:"origin", branch:"<current>", all:true }',
        "steps": _lines(
            """
将改动全部暂存
提交修复说明
推送到远端
"""
        ),
        "script": """git add -A\ngit commit -m "<msg>"\ngit push origin <branch>""",
        "notes": "适用于已经确认的热修，提交前仍建议快速自测。",
    },
    "hotfix_release": {
        "name": "hotfix_release",
        "summary": "在主干上进行热修、打标签并发布。",
        "parameters": '{ target:"main", tag:"vX.Y.Z", msg:"hotfix: ...", push_tags:true }',
        "steps": _lines(
            """
更新主干并准备修复
提交热修
创建注解标签
推送代码与标签
"""
        ),
        "script": """git fetch origin -p\ngit switch <target> && git pull --rebase origin <target>\ngit add -A && git commit -m "<msg>"\ngit tag -a <tag> -m "<msg>"\ngit push origin <target>\ngit push origin <tag>""",
        "notes": "标签名称建议遵循语义化版本规范。",
    },
    "rebase_fixup_squash": {
        "name": "rebase_fixup_squash",
        "summary": "通过交互式 rebase 将多个修订压缩为整洁历史。",
        "parameters": '{ base:"origin/<branch>", autosquash:true }',
        "steps": _lines(
            """
fetch 最新远端引用
执行带 autosquash 的交互式 rebase
在编辑界面标记 fixup/squash
"""
        ),
        "script": """git fetch origin -p\ngit rebase -i --autosquash <base>\n# 在交互界面将相关提交标记为 fixup/squash""",
        "notes": "适用于整理推送前的提交历史。",
    },
    "clean_workspace": {
        "name": "clean_workspace",
        "summary": "清理未跟踪文件并重置工作区（危险操作）。",
        "parameters": '{ mode:"hard", allow_destructive:true }',
        "steps": _lines(
            """
确认确实要放弃改动
执行 hard reset 回到 HEAD
清理未跟踪文件与目录
"""
        ),
        "script": """git reset --hard\ngit clean -fd""",
        "notes": "务必在备份或确认无需保留改动后执行。",
    },
    "tag_release": {
        "name": "tag_release",
        "summary": "对当前 HEAD 打语义化版本标签并推送。",
        "parameters": '{ tag:"vX.Y.Z", message:"release vX.Y.Z", push:true }',
        "steps": _lines(
            """
创建注解标签
推送标签到远端
"""
        ),
        "script": """git tag -a <tag> -m "<message>"\ngit push origin <tag>""",
        "notes": "推送前可通过 git show <tag> 验证标签信息。",
    },
    "rollback_last_commit": {
        "name": "rollback_last_commit",
        "summary": "通过 revert 撤销最近一次提交而不改历史。",
        "parameters": '{ commit:"HEAD", no_edit:true, push:false }',
        "steps": _lines(
            """
执行 revert 生成反向提交
（可选）推送到远端
"""
        ),
        "script": """git revert --no-edit <commit>\n# git push origin <branch>""",
        "notes": "适用于已经推送的提交，需要生成新的修复提交。",
    },
    "recover_deleted_branch": {
        "name": "recover_deleted_branch",
        "summary": "通过 reflog 找回误删分支。",
        "parameters": '{ lost_ref:"HEAD@{n}", new_branch:"recover/xxx" }',
        "steps": _lines(
            """
使用 git reflog 定位丢失的提交
从记录创建新分支
切换到恢复分支
"""
        ),
        "script": """git reflog\ngit branch <new_branch> <lost_ref>\ngit switch <new_branch>""",
        "notes": "及时操作可提高恢复成功率。",
    },
    "stash_save_apply": {
        "name": "stash_save_apply",
        "summary": "存储当前改动并在其他分支恢复。",
        "parameters": '{ name:"WIP-xxx", apply_to:"<branch>" }',
        "steps": _lines(
            """
将改动存入命名的 stash
切换到目标分支
列出并应用 stash
"""
        ),
        "script": """git stash push -m "<name>"\ngit switch <apply_to>\ngit stash list\ngit stash apply""",
        "notes": "如需一次性恢复并删除，可改用 git stash pop。",
    },
    "first_push": {
        "name": "first_push",
        "summary": "首次将本地分支推送到远端并建立跟踪。",
        "parameters": '{ remote:"origin", branch:"feature/xxx" }',
        "steps": _lines(
            """
使用 -u 推送并设置上游
"""
        ),
        "script": """git push -u origin <branch>""",
        "notes": "后续推送可直接使用 git push。",
    },
    "prune_gone_branches": {
        "name": "prune_gone_branches",
        "summary": "清理远端已删除的本地跟踪分支。",
        "parameters": '{ remote:"origin" }',
        "steps": _lines(
            """
fetch 时带 --prune 清理引用
删除状态为 gone 的本地分支
"""
        ),
        "script": """git fetch --all -p\ngit branch -vv | awk '/: gone]/{print $1}' | xargs -r git branch -D""",
        "notes": "第二步依赖 shell 管道，执行前可先查看输出。",
    },
}


def list_combos() -> List[str]:
    """Return all supported combo identifiers."""

    return sorted(GIT_COMBOS)


def get_combo(name: str) -> Combo:
    """Fetch a combo definition by name, raising if unavailable."""

    try:
        return GIT_COMBOS[name]
    except KeyError as exc:  # pragma: no cover - defensive guard
        available = ", ".join(list_combos())
        raise ValueError(f"unknown combo '{name}'. Available combos: {available}") from exc


__all__ = ["Combo", "GIT_COMBOS", "list_combos", "get_combo"]
