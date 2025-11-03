"""Implementation of work log generation commands."""
import json
import os
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from git import Repo

from .models import WorkLogInput, WorkLogProvider

# Try to import optional dependencies
try:
    from openai import OpenAI

    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    from github import Github

    try:
        from github import Auth

        GITHUB_AUTH_AVAILABLE = True
    except ImportError:
        GITHUB_AUTH_AVAILABLE = False
    GITHUB_AVAILABLE = True
except ImportError:
    GITHUB_AVAILABLE = False
    GITHUB_AUTH_AVAILABLE = False

# Default system prompt for work log summary
_DEFAULT_SYSTEM_PROMPT = """你是一个专业的技术文档撰写助手。根据提供的 git commit 记录，生成一份结构化的中文工作总结。

要求：
1. 使用 Markdown 格式
2. 总结主要包括：
   - 今日工作概述（3-5句）
   - 主要完成内容（按模块分类）
   - 统计数据（提交数、代码变更、涉及文件等）
   - 技术亮点或重要改进
3. 语言简洁专业，避免过于冗长
4. 重点关注代码改进、功能增强、问题修复等技术性内容

【工作会话时间图要求】
如果提供的 commit 记录中包含工作会话统计信息（如"工作会话: X 个，总时长约 Y 分钟"），请基于这些会话信息绘制一个简洁的工作内容时间分布图。图应包含：
1. 各工作会话的起止时间范围
2. 每个会话涉及的提交数量或主要功能模块
3. 会话之间的时间间隔（便于识别工作节奏）
4. 如有跨项目提交，标注项目切换的时间点
5. **特别注意并行工作时间**：如果存在"跨项目并行工作时间段"标识，请在时间图中清晰标注同时在不同项目上工作的时段，这有助于准确评估实际投入时间（并行工作不应简单累加）

时间图可使用 Markdown 表格及 Mermaid 10分钟级甘特图形式呈现。

请根据提供的 commit 信息生成工作总结。"""

_PEI_PROMPT = """\n\n最后计算一下效率指数（PEI）：
        设：
* $N_c$ = 当日提交次数
* $L_{add}$ = 新增代码行数
* $L_{del}$ = 删除代码行数
* $T$ = 实际投入时间（小时，排除并行重叠）
* $P_{mod}$ = 修改文件数
* $C_{eff}$ = 编译通过率（或测试通过率，0~1）
* $C_{cmp}$ = 代码复杂度系数（0.5~1.5，可依据任务类型调整）
---
公式：
$$
\\text{PEI} = \\frac{(0.4 N_c + 0.3 \\log_{10}(L_{add}+L_{del}) + 0.2 \\log_{10}(P_{mod}+1)) \\times C_{eff} \\times C_{cmp}}{T/8}
$$
> 说明：
>
> * 对数项使得代码量和文件数带来递减效益，防止行数堆积造成虚高。
> * $T/8$ 用于时间归一化（以 8 小时为标准工作日）。
> * 系数可调：`0.4/0.3/0.2` 权重适合中型项目（如C++工程）。
参考解释表

| PEI 值 | 效率等级  | 特征描述           |
| ----- | ----- | -------------- |
| 0–3   | 💤 低效 | 频繁上下文切换、非核心任务  |
| 4–6   | ⚙️ 正常 | 持续推进、稳定产出      |
| 7–9   | 🚀 高效 | 模块重构、系统优化或关键修复 |
| ≥10   | 🧠 卓越 | 自动化、生成式任务、集中攻坚 |
        """


def _parse_git_log(raw: str) -> List[Dict[str, Any]]:
    """Parse git log output."""
    commits = []
    if not raw:
        return commits
    for entry in raw.strip("\x1e").split("\x1e"):
        parts = entry.split("\x1f")
        if len(parts) < 5:
            continue
        if len(parts) >= 6:
            sha, author_name, author_email, date_str, epoch_str, message = [p.strip() for p in parts[:6]]
            date_epoch = int(epoch_str) if epoch_str.isdigit() else None
        else:
            sha, author_name, author_email, date_str, message = [p.strip() for p in parts[:5]]
            date_epoch = None
        commits.append({
            "sha": sha,
            "author_name": author_name,
            "author_email": author_email,
            "date": date_str,
            "date_epoch": date_epoch,
            "message": message,
        })
    return commits


def _get_commits_between(repo_path: str, since_dt: datetime, until_dt: datetime) -> List[Dict[str, Any]]:
    """Get commits between two dates from a local repository."""
    repo = Repo(repo_path)
    since = since_dt.isoformat(sep=" ")
    until = until_dt.isoformat(sep=" ")
    raw = repo.git.log(
        f"--since={since}",
        f"--until={until}",
        "--pretty=format:%H%x1f%an%x1f%ae%x1f%ad%x1f%at%x1f%s%x1e",
        date="iso",
    )
    return _parse_git_log(raw)


def _get_commit_numstat(repo_path: str, sha: str) -> Tuple[List[str], int, int]:
    """Get commit statistics: (files, insertions, deletions)."""
    repo = Repo(repo_path)
    output = repo.git.show(sha, "--numstat", "--pretty=tformat:")
    files: List[str] = []
    insertions_total = 0
    deletions_total = 0
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            add_str, del_str, path = parts
            try:
                add = int(add_str) if add_str.isdigit() else 0
            except ValueError:
                add = 0
            try:
                dele = int(del_str) if del_str.isdigit() else 0
            except ValueError:
                dele = 0
            insertions_total += add
            deletions_total += dele
            files.append(path)
    return files, insertions_total, deletions_total


def _get_commit_body(repo_path: str, sha: str) -> str:
    """Get full commit message body."""
    repo = Repo(repo_path)
    body = repo.git.show(sha, "-s", "--format=%B")
    return body.strip("\n")


def _get_pull_operations(repo_path: str, since_dt: datetime, until_dt: datetime) -> List[datetime]:
    """Get git pull/fetch operations within time range."""
    try:
        repo = Repo(repo_path)
        since_iso = since_dt.isoformat(sep=" ")
        until_iso = until_dt.isoformat(sep=" ")
        try:
            reflog_output = repo.git.reflog("--date=iso", f"--since={since_iso}", f"--until={until_iso}")
        except Exception:
            return []

        if not reflog_output:
            return []

        pull_times: List[datetime] = []
        for line in reflog_output.splitlines():
            if not line.strip():
                continue
            match = re.search(r"HEAD@\{([^\}]+)\}:\s*([^:]+):", line)
            if match:
                date_str = match.group(1).strip()
                operation = match.group(2).strip().lower()
                is_pull_related = any(keyword in operation for keyword in ["pull", "fetch", "merge", "update", "rebase"])
                excluded_keywords = ["checkout", "commit", "reset", "branch", "switch"]
                if any(keyword in operation for keyword in excluded_keywords):
                    is_pull_related = False

                if is_pull_related:
                    try:
                        pull_time = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S %z")
                        pull_time = pull_time.astimezone().replace(tzinfo=None)
                        since_local = since_dt.replace(tzinfo=None) if since_dt.tzinfo else since_dt
                        until_local = until_dt.replace(tzinfo=None) if until_dt.tzinfo else until_dt
                        if since_local <= pull_time <= until_local:
                            pull_times.append(pull_time)
                    except Exception:
                        continue

        return sorted(list(set(pull_times)))
    except Exception:
        return []


def _get_github_events(
    repo_full_name: str, token: str, since_dt: datetime, until_dt: datetime
) -> List[Dict[str, Any]]:
    """Get commits and PRs from GitHub within time range."""
    if not GITHUB_AVAILABLE:
        raise ImportError("PyGithub 未安装，请运行: pip install PyGithub")

    events: List[Dict[str, Any]] = []
    if GITHUB_AUTH_AVAILABLE:
        auth = Auth.Token(token)
        g = Github(auth=auth)
    else:
        g = Github(token)

    try:
        repo = g.get_repo(repo_full_name)
    except Exception as e:
        error_msg = str(e)
        if "403" in error_msg or "Forbidden" in error_msg:
            raise Exception(
                f"无法访问仓库 {repo_full_name}。可能原因：\n"
                f"1. 仓库是私有的，且 token 没有访问权限\n"
                f"2. token 权限不足（需要 'repo' 权限来访问私有仓库）\n"
                f"3. 仓库不存在或 token 无效\n"
                f"请检查 token 权限设置：https://github.com/settings/tokens"
            )
        raise

    since_utc = since_dt.replace(tzinfo=timezone.utc) if since_dt.tzinfo is None else since_dt.astimezone(timezone.utc)
    until_utc = until_dt.replace(tzinfo=timezone.utc) if until_dt.tzinfo is None else until_dt.astimezone(timezone.utc)

    # Get commits
    try:
        commits_iter = repo.get_commits(since=since_utc, until=until_utc)
        for c in commits_iter:
            commit_date = c.commit.author.date
            if commit_date.tzinfo is None:
                commit_date = commit_date.replace(tzinfo=timezone.utc)

            if since_utc <= commit_date <= until_utc:
                message = c.commit.message.splitlines()[0] if c.commit.message else ""
                author_name = getattr(c.commit.author, "name", None)
                if not author_name:
                    try:
                        author_name = c.commit.committer.login if hasattr(c.commit.committer, "login") else "Unknown"
                    except Exception:
                        author_name = "Unknown"
                events.append({
                    "sha": c.sha,
                    "author_name": author_name or "Unknown",
                    "author_email": "",
                    "date": commit_date.isoformat(),
                    "date_epoch": int(commit_date.timestamp()),
                    "message": message,
                    "type": "commit",
                })
    except Exception as e:
        # Log warning but continue
        pass

    # Get PRs
    try:
        query = f"repo:{repo_full_name} is:pr updated:{since_utc.date()}..{until_utc.date()}"
        for pr in g.search_issues(query=query):
            pr_updated = pr.updated_at
            if pr_updated.tzinfo is None:
                pr_updated = pr_updated.replace(tzinfo=timezone.utc)

            if since_utc <= pr_updated <= until_utc:
                events.append({
                    "sha": f"PR#{pr.number}",
                    "author_name": pr.user.login if pr.user else "Unknown",
                    "author_email": "",
                    "date": pr_updated.isoformat(),
                    "date_epoch": int(pr_updated.timestamp()),
                    "message": pr.title,
                    "type": "pr",
                })
    except Exception:
        pass

    events.sort(key=lambda e: e["date_epoch"])
    return events


def _get_gitee_events(
    repo_full_name: str, token: str, since_dt: datetime, until_dt: datetime
) -> List[Dict[str, Any]]:
    """Get commits and PRs from Gitee within time range."""
    events: List[Dict[str, Any]] = []
    since_utc = since_dt.replace(tzinfo=timezone.utc) if since_dt.tzinfo is None else since_dt.astimezone(timezone.utc)
    until_utc = until_dt.replace(tzinfo=timezone.utc) if until_dt.tzinfo is None else until_dt.astimezone(timezone.utc)

    owner, repo_name = repo_full_name.split("/", 1)
    base_url = "https://gitee.com/api/v5"
    headers = {"Authorization": f"token {token}"} if token else {}

    # Get commits
    try:
        commits_url = f"{base_url}/repos/{owner}/{repo_name}/commits"
        page = 1
        while True:
            params = {
                "since": since_utc.isoformat(),
                "until": until_utc.isoformat(),
                "per_page": 100,
                "page": page,
            }
            resp = requests.get(commits_url, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            commits_data = resp.json()

            if not commits_data:
                break

            for c in commits_data:
                commit_date_str = c.get("commit", {}).get("author", {}).get("date", "")
                if commit_date_str:
                    try:
                        commit_date = datetime.fromisoformat(commit_date_str.replace("Z", "+00:00"))
                        if commit_date.tzinfo is None:
                            commit_date = commit_date.replace(tzinfo=timezone.utc)

                        if since_utc <= commit_date <= until_utc:
                            message = c.get("commit", {}).get("message", "").splitlines()[0] if c.get("commit", {}).get("message") else ""
                            author_info = c.get("commit", {}).get("author", {})
                            author_name = author_info.get("name", "Unknown")

                            events.append({
                                "sha": c.get("sha", "")[:40],
                                "author_name": author_name,
                                "author_email": author_info.get("email", ""),
                                "date": commit_date.isoformat(),
                                "date_epoch": int(commit_date.timestamp()),
                                "message": message,
                                "type": "commit",
                            })
                    except Exception:
                        continue

            if len(commits_data) < 100:
                break
            page += 1
    except Exception:
        pass

    events.sort(key=lambda e: e["date_epoch"])
    return events


def _group_commits_by_date(commits: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Group commits by date."""
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for c in commits:
        date_part = c["date"].split(" ")[0]
        groups[date_part].append(c)
    for k in groups:
        groups[k].sort(key=lambda x: x["date"])
    return dict(sorted(groups.items(), key=lambda x: x[0]))


def _commit_time_dt(c: Dict[str, Any]) -> datetime:
    """Convert commit dict to datetime."""
    if c.get("date_epoch"):
        try:
            return datetime.fromtimestamp(int(c["date_epoch"]))
        except Exception:
            pass
    ds = c.get("date", "")
    try:
        return datetime.strptime(ds, "%Y-%m-%d %H:%M:%S %z").astimezone().replace(tzinfo=None)
    except Exception:
        try:
            return datetime.strptime(ds, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return datetime.fromisoformat(ds.replace(" ", "T").split(" +")[0])


def _compute_work_sessions(
    commits: List[Dict[str, Any]], gap_minutes: int = 60, pull_times: Optional[List[datetime]] = None
) -> List[Dict[str, Any]]:
    """Compute work sessions from commits."""
    if not commits:
        return []
    items = sorted(commits, key=lambda c: _commit_time_dt(c))
    sessions: List[Dict[str, Any]] = []
    gap = timedelta(minutes=gap_minutes)

    pull_times_sorted = sorted(pull_times) if pull_times else []

    current = {
        "start": _commit_time_dt(items[0]),
        "end": _commit_time_dt(items[0]),
        "commits": [items[0]],
    }

    first_commit_time = _commit_time_dt(items[0])
    if pull_times_sorted:
        for pull_time in reversed(pull_times_sorted):
            if pull_time <= first_commit_time:
                time_diff = (first_commit_time - pull_time).total_seconds() / 60
                if time_diff > 0 and time_diff <= 120:
                    current["start"] = pull_time
                    break

    for c in items[1:]:
        t = _commit_time_dt(c)
        if t - _commit_time_dt(current["commits"][-1]) <= gap:
            current["end"] = t
            current["commits"].append(c)
        else:
            current["duration_minutes"] = max(1, int((current["end"] - current["start"]).total_seconds() // 60))
            sessions.append(current)

            current = {"start": t, "end": t, "commits": [c]}

            if pull_times_sorted:
                for pull_time in reversed(pull_times_sorted):
                    if pull_time <= t:
                        time_diff = (t - pull_time).total_seconds() / 60
                        if time_diff > 0 and time_diff <= 120:
                            current["start"] = pull_time
                            break

    current["duration_minutes"] = max(1, int((current["end"] - current["start"]).total_seconds() // 60))
    sessions.append(current)
    return sessions


def _detect_parallel_sessions(repo_to_sessions: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Detect parallel work sessions across repositories."""
    if len(repo_to_sessions) < 2:
        return []

    all_periods: List[Dict[str, Any]] = []
    for repo, sessions in repo_to_sessions.items():
        for s in sessions:
            all_periods.append({"start": s["start"], "end": s["end"], "repo": repo, "session": s})

    if not all_periods:
        return []

    all_periods.sort(key=lambda x: (x["start"], x["end"]))
    merged_overlaps: List[Dict[str, Any]] = []
    current_overlaps = []

    for period in all_periods:
        if not current_overlaps:
            current_overlaps = [period]
            continue

        can_merge = False
        for existing in current_overlaps:
            if not (period["end"] < existing["start"] or period["start"] > existing["end"]):
                can_merge = True
                break

        if can_merge:
            current_overlaps.append(period)
        else:
            if len(set(p["repo"] for p in current_overlaps)) > 1:
                overlap_start = min(p["start"] for p in current_overlaps)
                overlap_end = max(p["end"] for p in current_overlaps)
                overlap_repos = sorted(set(p["repo"] for p in current_overlaps))
                merged_overlaps.append({
                    "start": overlap_start,
                    "end": overlap_end,
                    "repos": overlap_repos,
                    "duration_minutes": int((overlap_end - overlap_start).total_seconds() // 60),
                })
            current_overlaps = [period]

    if len(set(p["repo"] for p in current_overlaps)) > 1:
        overlap_start = min(p["start"] for p in current_overlaps)
        overlap_end = max(p["end"] for p in current_overlaps)
        overlap_repos = sorted(set(p["repo"] for p in current_overlaps))
        merged_overlaps.append({
            "start": overlap_start,
            "end": overlap_end,
            "repos": overlap_repos,
            "duration_minutes": int((overlap_end - overlap_start).total_seconds() // 60),
        })

    if not merged_overlaps:
        return []

    final_merged: List[Dict[str, Any]] = []
    merged_overlaps.sort(key=lambda x: (x["start"], x["end"]))

    current = merged_overlaps[0]
    for next_period in merged_overlaps[1:]:
        gap = (next_period["start"] - current["end"]).total_seconds() / 60
        if gap <= 5 or not (next_period["end"] < current["start"] or next_period["start"] > current["end"]):
            current["start"] = min(current["start"], next_period["start"])
            current["end"] = max(current["end"], next_period["end"])
            current["repos"] = sorted(set(current["repos"]) | set(next_period["repos"]))
            current["duration_minutes"] = int((current["end"] - current["start"]).total_seconds() // 60)
        else:
            final_merged.append(current)
            current = next_period
    final_merged.append(current)

    return final_merged


def _build_commit_context_by_project(
    repo_to_grouped: Dict[str, Dict[str, List[Dict[str, Any]]]],
    repo_to_details: Dict[str, Dict[str, Tuple[List[str], int, int, str]]],
    gap_minutes: int,
    repo_to_pull_times: Optional[Dict[str, List[datetime]]],
) -> str:
    """Build commit context string for multi-project mode."""
    lines: List[str] = []

    # Calculate sessions for all repos
    repo_to_sessions: Dict[str, List[Dict[str, Any]]] = {}
    for repo_name, grouped in repo_to_grouped.items():
        flat_commits: List[Dict[str, Any]] = []
        for items in grouped.values():
            flat_commits.extend(items)
        pull_times = repo_to_pull_times.get(repo_name, []) if repo_to_pull_times else []
        sessions = _compute_work_sessions(flat_commits, gap_minutes, pull_times)
        repo_to_sessions[repo_name] = sessions

    # Detect parallel sessions
    parallel_periods = _detect_parallel_sessions(repo_to_sessions)
    if parallel_periods:
        lines.append("# 跨项目并行工作时间段")
        total_parallel_minutes = sum(p["duration_minutes"] for p in parallel_periods)
        lines.append(f"检测到 {len(parallel_periods)} 个并行工作时段，总重叠时长约 {total_parallel_minutes} 分钟")
        for idx, p in enumerate(parallel_periods, 1):
            repos_str = ", ".join(p["repos"])
            lines.append(
                f"- 并行时段{idx}: {p['start']} ~ {p['end']} ({p['duration_minutes']} 分钟, 涉及项目: {repos_str})"
            )
        lines.append("")

    # Detailed stats per project
    for repo_name, grouped in repo_to_grouped.items():
        if len(grouped) == 0:
            continue
        lines.append(f"\n# 项目：{repo_name}")
        sessions = repo_to_sessions[repo_name]
        if sessions:
            total_minutes = sum(s["duration_minutes"] for s in sessions)
            lines.append(f"工作会话: {len(sessions)} 个，总时长约 {total_minutes} 分钟")
            for idx, s in enumerate(sessions, 1):
                is_parallel = any(
                    not (s["end"] < pp["start"] or s["start"] > pp["end"])
                    for pp in parallel_periods
                    if repo_name in pp["repos"]
                )
                parallel_marker = " [并行]" if is_parallel else ""
                lines.append(
                    f"- 会话{idx}: {s['start']} ~ {s['end']} ({s['duration_minutes']} 分钟, {len(s['commits'])} 次提交){parallel_marker}"
                )
        for day, items in grouped.items():
            lines.append(f"\n## {day} ({len(items)} commits)")
            for c in items:
                sha = c["sha"]
                files, ins, dels, body = repo_to_details[repo_name].get(sha, ([], 0, 0, ""))
                short_sha = sha[:8]
                time_part = " ".join(c["date"].split(" ")[1:3]) if " " in c["date"] else c["date"]
                lines.append(f"\n- [{short_sha}] {time_part}")
                lines.append(f"  提交信息: {c['message']}")
                lines.append(f"  统计: {ins} 行新增, {dels} 行删除, {len(files)} 个文件")
                if body and body.strip() != c["message"]:
                    lines.append(f"  详细内容:\n{body}")
                if files:
                    lines.append(f"  修改的文件: {', '.join(files[:20])}{' ...' if len(files) > 20 else ''}")
    return "\n".join(lines)


def _build_commit_context_single(
    grouped: Dict[str, List[Dict[str, Any]]],
    details: Dict[str, Tuple[List[str], int, int, str]],
) -> str:
    """Build commit context string for single-project mode."""
    context_lines = []
    for day, items in grouped.items():
        context_lines.append(f"\n## {day} ({len(items)} commits)")
        for c in items:
            sha = c["sha"]
            files, ins, dels, body = details.get(sha, ([], 0, 0, ""))
            short_sha = sha[:8]
            time_part = " ".join(c["date"].split(" ")[1:3]) if " " in c["date"] else c["date"]
            context_lines.append(f"\n- [{short_sha}] {time_part}")
            context_lines.append(f"  提交信息: {c['message']}")
            context_lines.append(f"  统计: {ins} 行新增, {dels} 行删除, {len(files)} 个文件")
            if body and body.strip() != c["message"]:
                context_lines.append(f"  详细内容:\n{body}")
            if files:
                context_lines.append(f"  修改的文件: {', '.join(files[:20])}{' ...' if len(files) > 20 else ''}")
    return "\n".join(context_lines)


def _generate_summary_with_llm(
    grouped: Dict[str, Any],
    details: Dict[str, Any],
    system_prompt: Optional[str],
    provider: WorkLogProvider,
    model: Optional[str],
    author: Optional[str],
    gap_minutes: int,
    repo_to_pull_times: Optional[Dict[str, List[datetime]]],
    temperature: float,
) -> str:
    """Generate AI summary using LLM."""
    # Build context
    if isinstance(grouped, dict) and grouped and all(isinstance(v, dict) for v in grouped.values()):
        # Multi-project mode
        commit_context = _build_commit_context_by_project(grouped, details, gap_minutes, repo_to_pull_times)  # type: ignore
    else:
        # Single-project mode
        commit_context = _build_commit_context_single(grouped, details)  # type: ignore

    if len(commit_context) < 10:
        return "今天无工作，无法生成工作总结。"

    system_msg = system_prompt or _DEFAULT_SYSTEM_PROMPT
    if not system_prompt:
        system_msg += "\n此外，请按项目分别估算投入时间（根据提交时间密度与连续性），并给出每个项目的主要产出。"

    if author:
        system_msg += f"\n此外，请基于作者姓名或邮箱包含\"{author}\"的提交进行工作总结，并在摘要开头显式标注：作者：{author}。"
        user_msg = f"请根据以下 commit 记录生成{author}工作总结：\n\n{commit_context}"
        user_msg += _PEI_PROMPT
    else:
        user_msg = f"请根据以下 commit 记录生成工作总结：\n\n{commit_context}"

    if provider == WorkLogProvider.openai:
        if not OPENAI_AVAILABLE:
            return "错误：未安装 openai 包。请运行: pip install openai"

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return "错误：未提供 OpenAI API key。请设置环境变量 OPENAI_API_KEY"

        client = OpenAI(api_key=api_key)
        chosen_model = model or "gpt-4o-mini"

        try:
            response = client.chat.completions.create(
                model=chosen_model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                temperature=temperature,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"错误：调用 OpenAI API 失败: {str(e)}"

    else:  # DeepSeek
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            return "错误：未提供 DeepSeek API key。请设置环境变量 DEEPSEEK_API_KEY"

        chosen_model = model or "deepseek-chat"

        try:
            url = "https://api.deepseek.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": chosen_model,
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": temperature,
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except requests.exceptions.HTTPError as e:
            error_detail = ""
            try:
                error_detail = f" - {resp.text}"
            except Exception:
                pass
            return f"错误：调用 DeepSeek API 失败: {str(e)}{error_detail}"
        except Exception as e:
            return f"错误：调用 DeepSeek API 失败: {str(e)}"


def _render_markdown_gitwork(
    title: str,
    grouped: Dict[str, List[Dict[str, Any]]],
    details: Dict[str, Tuple[List[str], int, int, str]],
    summary_text: Optional[str] = None,
) -> str:
    """Render work log as Markdown."""
    lines: List[str] = []
    lines.append(f"# {title}")
    lines.append("")
    total_commits = sum(len(v) for v in grouped.values())
    lines.append(f"总计 {total_commits} 个提交")
    lines.append("")

    for day, items in grouped.items():
        lines.append(f"## {day} ({len(items)} commits)")
        lines.append("")
        for c in items:
            sha = c["sha"]
            short_sha = sha[:8]
            files, ins, dels, body = details.get(sha, ([], 0, 0, ""))
            time_part = " ".join(c["date"].split(" ")[1:3]) if " " in c["date"] else c["date"]
            lines.append(f"- [{short_sha}] {time_part} | {c['message']} ({ins}+/{dels}-; {len(files)} files)")
            if files:
                lines.append(f"  - files: {', '.join(files[:10])}{' ...' if len(files) > 10 else ''}")
            if body:
                lines.append("  - message:")
                lines.append("```")
                lines.extend(body.splitlines())
                lines.append("```")
        lines.append("")

    if summary_text:
        lines.append(summary_text)

    return "\n".join(lines)


def _render_multi_project_gitwork(
    title: str,
    repo_to_grouped: Dict[str, Dict[str, List[Dict[str, Any]]]],
    repo_to_details: Dict[str, Dict[str, Tuple[List[str], int, int, str]]],
    add_summary: bool,
    summary_text: Optional[str],
    gap_minutes: int,
    repo_to_pull_times: Optional[Dict[str, List[datetime]]],
) -> str:
    """Render multi-project work log as Markdown."""
    lines: List[str] = []
    lines.append(f"# {title}")
    lines.append("")
    total_commits = sum(sum(len(v) for v in grouped.values()) for grouped in repo_to_grouped.values())
    lines.append(f"总计 {total_commits} 个提交，项目数 {len(repo_to_grouped)}")
    lines.append("")

    # Calculate parallel work sessions
    repo_to_sessions: Dict[str, List[Dict[str, Any]]] = {}
    for repo_name, grouped in repo_to_grouped.items():
        flat_commits: List[Dict[str, Any]] = []
        for items in grouped.values():
            flat_commits.extend(items)
        pull_times = repo_to_pull_times.get(repo_name, []) if repo_to_pull_times else []
        sessions = _compute_work_sessions(flat_commits, gap_minutes, pull_times)
        repo_to_sessions[repo_name] = sessions

    parallel_periods = _detect_parallel_sessions(repo_to_sessions)
    if parallel_periods:
        lines.append("## 跨项目并行工作时间统计")
        total_parallel_minutes = sum(p["duration_minutes"] for p in parallel_periods)
        lines.append(f"检测到 **{len(parallel_periods)} 个并行工作时段**，总重叠时长约 **{total_parallel_minutes} 分钟**")
        lines.append("")
        for idx, p in enumerate(parallel_periods, 1):
            repos_str = ", ".join(p["repos"])
            lines.append(f"- **并行时段 {idx}**：{p['start'].strftime('%Y-%m-%d %H:%M')} ~ {p['end'].strftime('%Y-%m-%d %H:%M')} ({p['duration_minutes']} 分钟)")
            lines.append(f"  - 涉及项目：{repos_str}")
        lines.append("")
        lines.append("> 注意：并行工作时间不应简单累加，实际投入时间以重叠时段的最大值为准。")
        lines.append("")

    # Time stats per project
    lines.append("## 各项目时间统计")
    for repo_name, grouped in repo_to_grouped.items():
        sessions = repo_to_sessions[repo_name]
        if sessions:
            total_minutes = sum(s["duration_minutes"] for s in sessions)
            lines.append(f"### {repo_name}")
            lines.append(f"- 工作会话：{len(sessions)} 个，总时长约 {total_minutes} 分钟")
            for idx, s in enumerate(sessions, 1):
                is_parallel = any(
                    not (s["end"] < pp["start"] or s["start"] > pp["end"])
                    for pp in parallel_periods
                    if repo_name in pp["repos"]
                )
                parallel_marker = " **[并行]**" if is_parallel else ""
                lines.append(
                    f"  - 会话{idx}：{s['start'].strftime('%H:%M')} ~ {s['end'].strftime('%H:%M')} ({s['duration_minutes']} 分钟, {len(s['commits'])} 次提交){parallel_marker}"
                )
    lines.append("")

    # Detailed commits per project
    for repo_name, grouped in repo_to_grouped.items():
        lines.append(f"# 项目：{repo_name}")
        lines.append("")
        for day, items in grouped.items():
            lines.append(f"## {day} ({len(items)} commits)")
            lines.append("")
            for c in items:
                sha = c["sha"]
                short_sha = sha[:8]
                files, ins, dels, body = repo_to_details[repo_name].get(sha, ([], 0, 0, ""))
                time_part = " ".join(c["date"].split(" ")[1:3]) if " " in c["date"] else c["date"]
                lines.append(f"- [{short_sha}] {time_part} | {c['message']} ({ins}+/{dels}-; {len(files)} files)")
                if files:
                    lines.append(f"  - files: {', '.join(files[:10])}{' ...' if len(files) > 10 else ''}")
                if body:
                    lines.append("  - message:")
                    lines.append("```")
                    lines.extend(body.splitlines())
                    lines.append("```")
            lines.append("")
        lines.append("")

    if add_summary and summary_text:
        lines.append(summary_text)

    return "\n".join(lines)


def _parse_date_input(value: Optional[str], default_dt: Optional[datetime]) -> Optional[datetime]:
    """Parse date string to datetime."""
    if value is None:
        return default_dt
    try:
        return datetime.fromisoformat(value)
    except Exception:
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except Exception:
            raise ValueError(f"无法解析日期: {value}")


def execute_work_log_command(payload: WorkLogInput) -> str:
    """Execute work log generation and return JSON result.

    Args:
        payload: Validated work log input

    Returns:
        JSON string with exit_code, stdout (markdown content), stderr
    """
    try:
        # Parse date inputs
        now = datetime.now()
        if payload.days is not None and payload.days > 0:
            start = (now - timedelta(days=payload.days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = now.replace(hour=23, minute=59, second=59, microsecond=0)
        else:
            start = _parse_date_input(
                payload.since, now.replace(hour=0, minute=0, second=0, microsecond=0)
            )
            end = _parse_date_input(
                payload.until, now.replace(hour=23, minute=59, second=59, microsecond=0)
            )
            if start is not None:
                start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            if end is not None:
                end = end.replace(hour=23, minute=59, second=59, microsecond=0)

        if start is None or end is None:
            return json.dumps({
                "exit_code": 1,
                "stdout": "",
                "stderr": "无法确定时间范围：请提供 since/until 或 days 参数",
            })

        # Determine if multi-project mode
        total_repos = len(payload.repo_paths) + len(payload.github_repos) + len(payload.gitee_repos)
        multi_project = (
            len(payload.repo_paths) > 1
            or len(payload.github_repos) > 1
            or len(payload.gitee_repos) > 1
            or total_repos > 1
        )

        # Collect commits
        if not multi_project:
            commits: List[Dict[str, Any]] = []
            details: Dict[str, Tuple[List[str], int, int, str]] = {}
            pull_times: List[datetime] = []

            # Local repos
            if payload.repo_paths:
                repo = payload.repo_paths[0]
                commits = _get_commits_between(repo, start, end)
                if payload.author:
                    author_lower = payload.author.lower()
                    commits = [
                        c
                        for c in commits
                        if author_lower in c["author_name"].lower() or author_lower in c["author_email"].lower()
                    ]
                pull_times = _get_pull_operations(repo, start, end)
                for c in commits:
                    files, ins, dels = _get_commit_numstat(repo, c["sha"])
                    body = _get_commit_body(repo, c["sha"])
                    details[c["sha"]] = (files, ins, dels, body)

            # GitHub repos
            github_token = os.getenv("GITHUB_TOKEN")
            if payload.github_repos and github_token:
                repo_name = payload.github_repos[0]
                try:
                    remote_commits = _get_github_events(repo_name, github_token, start, end)
                    if payload.author:
                        author_lower = payload.author.lower()
                        remote_commits = [c for c in remote_commits if author_lower in c["author_name"].lower()]
                    commits.extend(remote_commits)
                    for c in remote_commits:
                        details[c["sha"]] = ([], 0, 0, c["message"])
                except Exception as e:
                    return json.dumps({
                        "exit_code": 1,
                        "stdout": "",
                        "stderr": f"获取 GitHub 仓库 {repo_name} 失败: {str(e)}",
                    })

            # Gitee repos
            gitee_token = os.getenv("GITEE_TOKEN")
            if payload.gitee_repos and gitee_token:
                repo_name = payload.gitee_repos[0]
                try:
                    remote_commits = _get_gitee_events(repo_name, gitee_token, start, end)
                    if payload.author:
                        author_lower = payload.author.lower()
                        remote_commits = [c for c in remote_commits if author_lower in c["author_name"].lower()]
                    commits.extend(remote_commits)
                    for c in remote_commits:
                        details[c["sha"]] = ([], 0, 0, c["message"])
                except Exception as e:
                    return json.dumps({
                        "exit_code": 1,
                        "stdout": "",
                        "stderr": f"获取 Gitee 仓库 {repo_name} 失败: {str(e)}",
                    })

            commits.sort(key=lambda c: _commit_time_dt(c))
            grouped = _group_commits_by_date(commits)

            # Generate summary if needed
            summary_text = None
            if payload.add_summary:
                summary_text = _generate_summary_with_llm(
                    grouped,
                    details,
                    payload.system_prompt,
                    payload.provider,
                    payload.model,
                    payload.author,
                    payload.session_gap_minutes,
                    None,
                    payload.temperature,
                )

            # Generate markdown
            title = payload.title or (f"Work Log: {start.date()} to {end.date()}" if start and end else "Work Log")
            md = _render_markdown_gitwork(title, grouped, details, summary_text)

            return json.dumps({
                "exit_code": 0,
                "stdout": md,
                "stderr": "",
            })

        else:
            # Multi-project mode
            repo_to_commits: Dict[str, List[Dict[str, Any]]] = {}
            repo_to_details: Dict[str, Dict[str, Tuple[List[str], int, int, str]]] = {}
            repo_to_grouped: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
            repo_to_pull_times: Dict[str, List[datetime]] = {}

            # Process local repos
            for repo in payload.repo_paths:
                commits = _get_commits_between(repo, start, end)
                if payload.author:
                    author_lower = payload.author.lower()
                    commits = [
                        c
                        for c in commits
                        if author_lower in c["author_name"].lower() or author_lower in c["author_email"].lower()
                    ]
                pull_times = _get_pull_operations(repo, start, end)
                repo_to_pull_times[repo] = pull_times
                repo_to_commits[repo] = commits
                details_map: Dict[str, Tuple[List[str], int, int, str]] = {}
                for c in commits:
                    files, ins, dels = _get_commit_numstat(repo, c["sha"])
                    body = _get_commit_body(repo, c["sha"])
                    details_map[c["sha"]] = (files, ins, dels, body)
                repo_to_details[repo] = details_map
                repo_to_grouped[repo] = _group_commits_by_date(commits)

            # Process GitHub repos
            github_token = os.getenv("GITHUB_TOKEN")
            if payload.github_repos and github_token:
                for repo_name in payload.github_repos:
                    try:
                        commits = _get_github_events(repo_name, github_token, start, end)
                        if payload.author:
                            author_lower = payload.author.lower()
                            commits = [c for c in commits if author_lower in c["author_name"].lower()]
                        repo_to_commits[repo_name] = commits
                        details_map: Dict[str, Tuple[List[str], int, int, str]] = {}
                        for c in commits:
                            details_map[c["sha"]] = ([], 0, 0, c["message"])
                        repo_to_details[repo_name] = details_map
                        repo_to_grouped[repo_name] = _group_commits_by_date(commits)
                    except Exception as e:
                        return json.dumps({
                            "exit_code": 1,
                            "stdout": "",
                            "stderr": f"获取 GitHub 仓库 {repo_name} 失败: {str(e)}",
                        })

            # Process Gitee repos
            gitee_token = os.getenv("GITEE_TOKEN")
            if payload.gitee_repos and gitee_token:
                for repo_name in payload.gitee_repos:
                    try:
                        commits = _get_gitee_events(repo_name, gitee_token, start, end)
                        if payload.author:
                            author_lower = payload.author.lower()
                            commits = [c for c in commits if author_lower in c["author_name"].lower()]
                        repo_to_commits[repo_name] = commits
                        details_map: Dict[str, Tuple[List[str], int, int, str]] = {}
                        for c in commits:
                            details_map[c["sha"]] = ([], 0, 0, c["message"])
                        repo_to_details[repo_name] = details_map
                        repo_to_grouped[repo_name] = _group_commits_by_date(commits)
                    except Exception as e:
                        return json.dumps({
                            "exit_code": 1,
                            "stdout": "",
                            "stderr": f"获取 Gitee 仓库 {repo_name} 失败: {str(e)}",
                        })

            # Generate summary if needed
            summary_text = None
            if payload.add_summary:
                summary_text = _generate_summary_with_llm(
                    repo_to_grouped,
                    repo_to_details,
                    payload.system_prompt,
                    payload.provider,
                    payload.model,
                    payload.author,
                    payload.session_gap_minutes,
                    repo_to_pull_times,
                    payload.temperature,
                )

            # Generate markdown
            title = payload.title or (f"Work Log: {start.date()} to {end.date()}" if start and end else "Work Log")
            md = _render_multi_project_gitwork(
                title, repo_to_grouped, repo_to_details, payload.add_summary, summary_text, payload.session_gap_minutes, repo_to_pull_times
            )

            return json.dumps({
                "exit_code": 0,
                "stdout": md,
                "stderr": "",
            })

    except ValueError as e:
        return json.dumps({
            "exit_code": 1,
            "stdout": "",
            "stderr": f"参数验证错误: {str(e)}",
        })
    except Exception as e:
        import traceback

        error_details = traceback.format_exc()
        return json.dumps({
            "exit_code": 1,
            "stdout": "",
            "stderr": f"执行错误: {type(e).__name__}: {str(e)}\n详细信息: {error_details[-500:]}",
        })

