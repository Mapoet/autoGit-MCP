#! /usr/bin/env python3

import os
import sys
import subprocess
import argparse
import re
import json
import requests
from collections import defaultdict
from typing import Dict, List, Optional, Tuple
from git import Repo
from datetime import datetime, timedelta, timezone
# API Keys - 仅从环境变量读取，不提供默认值以确保安全
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITEE_TOKEN = os.getenv("GITEE_TOKEN")
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    print("Warning: openai package not installed. Please run: pip install openai")
try:
    from github import Github
    try:
        from github import Auth
        GITHUB_AUTH_AVAILABLE = True
    except ImportError:
        GITHUB_AUTH_AVAILABLE = False  # 旧版本 PyGithub
    GITHUB_AVAILABLE = True
except ImportError:
    GITHUB_AVAILABLE = False
    GITHUB_AUTH_AVAILABLE = False
    print("Warning: PyGithub package not installed. Please run: pip install PyGithub")


# 默认系统提示词
default_system_prompt = """你是一个专业的技术文档撰写助手。根据提供的 git commit 记录，生成一份结构化的中文工作总结。

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

PEI="""\n\n最后计算一下效率指数（PEI）：
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

def get_github_events(repo_full_name: str, token: str, since_dt: datetime, until_dt: datetime) -> List[Dict]:
    """
    从 GitHub 获取指定时间范围内的 commits 和 PRs。
    
    Args:
        repo_full_name: 仓库全名，格式为 "OWNER/REPO"
        token: GitHub Personal Access Token
        since_dt: 起始时间（datetime，建议带时区）
        until_dt: 结束时间（datetime，建议带时区）
    
    Returns:
        事件列表，格式与本地 commit 兼容：
        [{
            "sha": commit_sha 或 "PR#123",
            "author_name": author_name,
            "author_email": "" (远程仓库通常没有email),
            "date": date_str (ISO格式字符串),
            "date_epoch": epoch_seconds,
            "message": commit_message 或 pr_title,
            "type": "commit" 或 "pr"
        }, ...]
    """
    if not GITHUB_AVAILABLE:
        raise ImportError("PyGithub 未安装，请运行: pip install PyGithub")
    
    events: List[Dict] = []
    # 使用新的认证方式（避免 deprecation warning）
    if GITHUB_AUTH_AVAILABLE:
        auth = Auth.Token(token)
        g = Github(auth=auth)
    else:
        # 旧版本 PyGithub，使用旧的方式
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
    
    # 确保时区为 UTC
    since_utc = since_dt.replace(tzinfo=timezone.utc) if since_dt.tzinfo is None else since_dt.astimezone(timezone.utc)
    until_utc = until_dt.replace(tzinfo=timezone.utc) if until_dt.tzinfo is None else until_dt.astimezone(timezone.utc)
    
    # 1) 获取 Commits
    try:
        commits_iter = repo.get_commits(since=since_utc, until=until_utc)
        for c in commits_iter:
            commit_date = c.commit.author.date
            if commit_date.tzinfo is None:
                commit_date = commit_date.replace(tzinfo=timezone.utc)
            
            # 只包含指定时间范围内的提交
            if since_utc <= commit_date <= until_utc:
                message = c.commit.message.splitlines()[0] if c.commit.message else ""
                # 尝试获取作者名称：先尝试 name，再尝试 committer 的 login
                author_name = getattr(c.commit.author, "name", None)
                if not author_name:
                    try:
                        author_name = c.commit.committer.login if hasattr(c.commit.committer, "login") else "Unknown"
                    except:
                        author_name = "Unknown"
                events.append({
                    "sha": c.sha,
                    "author_name": author_name or "Unknown",
                    "author_email": "",  # GitHub API 通常不提供邮箱
                    "date": commit_date.isoformat(),
                    "date_epoch": int(commit_date.timestamp()),
                    "message": message,
                    "type": "commit"
                })
    except Exception as e:
        error_msg = str(e)
        if "403" in error_msg or "Forbidden" in error_msg:
            print(f"Warning: 获取 GitHub 仓库 {repo_full_name} 的 commits 失败: 权限不足")
            print(f"提示: 如果是私有仓库，请确保 token 具有 'repo' 权限")
            print(f"提示: 如果是公开仓库，可能是 token 权限问题，或仓库不存在")
        elif "404" in error_msg or "Not Found" in error_msg:
            print(f"Warning: 获取 GitHub 仓库 {repo_full_name} 的 commits 失败: 仓库未找到")
        else:
            print(f"Warning: 获取 GitHub commits 失败: {e}")
    
    # 2) 获取 PRs（通过搜索接口，按 updated 时间范围）
    try:
        query = f"repo:{repo_full_name} is:pr updated:{since_utc.date()}..{until_utc.date()}"
        for pr in g.search_issues(query=query):
            pr_updated = pr.updated_at
            if pr_updated.tzinfo is None:
                pr_updated = pr_updated.replace(tzinfo=timezone.utc)
            
            # 检查是否在时间范围内
            if since_utc <= pr_updated <= until_utc:
                events.append({
                    "sha": f"PR#{pr.number}",
                    "author_name": pr.user.login if pr.user else "Unknown",
                    "author_email": "",
                    "date": pr_updated.isoformat(),
                    "date_epoch": int(pr_updated.timestamp()),
                    "message": pr.title,
                    "type": "pr"
                })
    except Exception as e:
        error_msg = str(e)
        if "403" in error_msg or "Forbidden" in error_msg:
            print(f"Warning: 获取 GitHub 仓库 {repo_full_name} 的 PRs 失败: 权限不足")
            print(f"提示: 请确保 token 具有访问仓库的权限")
        elif "404" in error_msg or "Not Found" in error_msg:
            print(f"Warning: 获取 GitHub 仓库 {repo_full_name} 的 PRs 失败: 仓库未找到")
        else:
            print(f"Warning: 获取 GitHub PRs 失败: {e}")
    
    # 按时间排序
    events.sort(key=lambda e: e["date_epoch"])
    return events

def get_gitee_events(repo_full_name: str, token: str, since_dt: datetime, until_dt: datetime) -> List[Dict]:
    """
    从 Gitee 获取指定时间范围内的 commits 和 PRs（MRs）。
    
    Args:
        repo_full_name: 仓库全名，格式为 "OWNER/REPO"
        token: Gitee Personal Access Token
        since_dt: 起始时间（datetime，建议带时区）
        until_dt: 结束时间（datetime，建议带时区）
    
    Returns:
        事件列表，格式与本地 commit 兼容
    """
    events: List[Dict] = []
    
    # 确保时区为 UTC
    since_utc = since_dt.replace(tzinfo=timezone.utc) if since_dt.tzinfo is None else since_dt.astimezone(timezone.utc)
    until_utc = until_dt.replace(tzinfo=timezone.utc) if until_dt.tzinfo is None else until_dt.astimezone(timezone.utc)
    
    owner, repo_name = repo_full_name.split("/", 1)
    base_url = "https://gitee.com/api/v5"
    headers = {"Authorization": f"token {token}"} if token else {}
    
    # 1) 获取 Commits
    try:
        commits_url = f"{base_url}/repos/{owner}/{repo_name}/commits"
        params = {
            "since": since_utc.isoformat(),
            "until": until_utc.isoformat(),
            "per_page": 100,
            "page": 1
        }
        
        page = 1
        while True:
            params["page"] = page
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
                        
                        # 只包含指定时间范围内的提交
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
                                "type": "commit"
                            })
                    except Exception as e:
                        print(f"Warning: 解析 Gitee commit 时间失败: {e}")
                        continue
            
            if len(commits_data) < 100:
                break
            page += 1
    except Exception as e:
        print(f"Warning: 获取 Gitee commits 失败: {e}")
    
    # 2) 获取 Pull Requests (MRs)
    try:
        mrs_url = f"{base_url}/repos/{owner}/{repo_name}/pulls"
        params = {
            "state": "all",
            "sort": "updated",
            "direction": "desc",
            "per_page": 100,
            "page": 1
        }
        
        page = 1
        while True:
            params["page"] = page
            resp = requests.get(mrs_url, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            mrs_data = resp.json()
            
            if not mrs_data:
                break
            
            for mr in mrs_data:
                updated_str = mr.get("updated_at", "")
                if updated_str:
                    try:
                        updated_date = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
                        if updated_date.tzinfo is None:
                            updated_date = updated_date.replace(tzinfo=timezone.utc)
                        
                        # 只包含指定时间范围内的 PR
                        if since_utc <= updated_date <= until_utc:
                            events.append({
                                "sha": f"PR#{mr.get('number', '')}",
                                "author_name": mr.get("user", {}).get("login", "Unknown"),
                                "author_email": "",
                                "date": updated_date.isoformat(),
                                "date_epoch": int(updated_date.timestamp()),
                                "message": mr.get("title", ""),
                                "type": "pr"
                            })
                    except Exception as e:
                        print(f"Warning: 解析 Gitee PR 时间失败: {e}")
                        continue
            
            # 如果最早的 PR 更新时间早于查询范围，可以提前退出
            try:
                earliest_updated = min(
                    (datetime.fromisoformat(mr.get("updated_at", "").replace("Z", "+00:00")) 
                     for mr in mrs_data if mr.get("updated_at")),
                    default=None
                )
                if earliest_updated and earliest_updated < since_utc:
                    break
            except Exception:
                pass  # 如果解析失败，继续下一页
            
            if len(mrs_data) < 100:
                break
            page += 1
    except Exception as e:
        print(f"Warning: 获取 Gitee PRs 失败: {e}")
    
    # 按时间排序
    events.sort(key=lambda e: e["date_epoch"])
    return events

def parse_git_log(raw):
    # 我们使用 git log 输出以 \x1e（record sep）分割 commit，以 \x1f 字段分割
    commits = []
    if not raw:
        return commits
    for entry in raw.strip("\x1e").split("\x1e"):
        parts = entry.split("\x1f")
        if len(parts) < 5:
            continue
        # 支持两种格式：5字段(旧) 或 6字段(含 %at epoch)
        if len(parts) >= 6:
            sha, author_name, author_email, date_str, epoch_str, message = [p.strip() for p in parts[:6]]
            date_epoch = int(epoch_str) if epoch_str.isdigit() else None
        else:
            sha, author_name, author_email, date_str, message = [p.strip() for p in parts[:5]]
            date_epoch = None
        # date_str 示例: 2025-10-20 12:34:56 +0800 （取决于 --date=iso）
        commits.append({
            "sha": sha,
            "author_name": author_name,
            "author_email": author_email,
            "date": date_str,
            "date_epoch": date_epoch,
            "message": message,
        })
    return commits

def get_commits_between(repo_path, since_dt, until_dt, max_count=None):
    """
    since_dt / until_dt: python datetime（最好带时区或者是本地时间）
    返回 commit 对象列表（可以转换为 dict）
    """
    repo = Repo(repo_path)
    # gitpython 没有直接参数用来筛选日期，所以使用 git directly via repo.git.log 更方便：
    since = since_dt.isoformat(sep=' ')
    until = until_dt.isoformat(sep=' ')
    # 增加 %at（author epoch 秒）便于稳定时间统计
    raw = repo.git.log(
        f'--since={since}',
        f'--until={until}',
        '--pretty=format:%H%x1f%an%x1f%ae%x1f%ad%x1f%at%x1f%s%x1e',
        date='iso'
    )
    # 复用上面 parse 函数
    return parse_git_log(raw)

def get_commit_numstat(repo_path: str, sha: str) -> Tuple[List[str], int, int]:
    """
    返回 (files, insertions, deletions)
    通过 `git show --numstat` 解析每个 commit 修改的文件与增删行数。
    """
    repo = Repo(repo_path)
    # --pretty=tformat: 只输出文件变更（避免重复元信息）
    output = repo.git.show(sha, '--numstat', '--pretty=tformat:')
    files: List[str] = []
    insertions_total = 0
    deletions_total = 0
    for line in output.splitlines():
        parts = line.split('\t')
        if len(parts) == 3:
            add_str, del_str, path = parts
            # 二进制文件会显示 '-'，此时计为 0
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

def get_commit_body(repo_path: str, sha: str) -> str:
    """
    获取完整 commit message（含主题与正文）。
    """
    repo = Repo(repo_path)
    body = repo.git.show(sha, '-s', '--format=%B')
    return body.strip('\n')

def get_pull_operations(repo_path: str, since_dt: datetime, until_dt: datetime) -> List[datetime]:
    """
    获取指定时间范围内的 git pull/fetch 操作时间。
    
    使用 git reflog 获取操作历史，查找 pull、fetch、merge 等操作。
    reflog 格式: <hash> HEAD@{<timestamp>}: <operation>: <message>
    
    Args:
        repo_path: Git 仓库路径
        since_dt: 起始时间
        until_dt: 结束时间
    
    Returns:
        pull 操作时间列表（按时间排序）
    """
    try:
        repo = Repo(repo_path)
        
        since_iso = since_dt.isoformat(sep=' ')
        until_iso = until_dt.isoformat(sep=' ')
        
        # 获取所有 reflog 记录（使用 HEAD@{date} 格式）
        try:
            # 尝试获取 reflog（某些仓库可能没有 reflog）
            # reflog 输出格式: <hash> HEAD@{2025-11-03 01:26:20 +0800}: pull: Fast-forward
            reflog_output = repo.git.reflog(
                '--date=iso',
                f'--since={since_iso}',
                f'--until={until_iso}'
            )
        except Exception:
            # 如果没有 reflog 或获取失败，返回空列表
            return []
        
        if not reflog_output:
            return []
        
        pull_times: List[datetime] = []
        
        # 解析 reflog 输出
        # 格式: <hash> HEAD@{<timestamp>}: <operation>: <message>
        # 示例: 9bef194 HEAD@{2025-11-03 01:26:20 +0800}: pull: Fast-forward
        for line in reflog_output.splitlines():
            if not line.strip():
                continue
            
            # 使用正则表达式提取时间戳和操作信息
            # 匹配格式: HEAD@{YYYY-MM-DD HH:MM:SS +TZ}: <operation>:
            match = re.search(r'HEAD@\{([^\}]+)\}:\s*([^:]+):', line)
            if match:
                date_str = match.group(1).strip()
                operation = match.group(2).strip().lower()
                
                # 检查是否是 pull/fetch 相关操作
                # pull 通常包含 "pull", "fetch", "merge" 等关键词
                is_pull_related = any(keyword in operation for keyword in [
                    'pull', 'fetch', 'merge', 'update', 'rebase'
                ])
                
                # 排除一些不相关的操作（如 checkout, commit, reset 等）
                excluded_keywords = ['checkout', 'commit', 'reset', 'branch', 'switch']
                if any(keyword in operation for keyword in excluded_keywords):
                    is_pull_related = False
                
                if is_pull_related:
                    try:
                        # 解析日期字符串（ISO 格式：2025-11-03 01:26:20 +0800）
                        pull_time = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S %z")
                        # 转换为本地时间（去掉时区信息以便比较）
                        pull_time = pull_time.astimezone().replace(tzinfo=None)
                        
                        # 确保在时间范围内
                        since_local = since_dt.replace(tzinfo=None) if since_dt.tzinfo else since_dt
                        until_local = until_dt.replace(tzinfo=None) if until_dt.tzinfo else until_dt
                        
                        if since_local <= pull_time <= until_local:
                            pull_times.append(pull_time)
                    except Exception as e:
                        # 解析失败，跳过
                        continue
        
        # 去重并排序（从早到晚）
        pull_times = sorted(list(set(pull_times)))
        return pull_times
        
    except Exception as e:
        # 获取失败，返回空列表（不阻断主流程）
        print(f"Warning: 获取仓库 {repo_path} 的 pull 记录失败: {e}")
        return []

def group_commits_by_date(commits: List[Dict]) -> Dict[str, List[Dict]]:
    groups: Dict[str, List[Dict]] = defaultdict(list)
    for c in commits:
        # date 字符串形如 "2025-10-20 12:34:56 +0800"
        date_part = c['date'].split(' ')[0]
        groups[date_part].append(c)
    # 对每组按时间排序（从早到晚）
    for k in groups:
        groups[k].sort(key=lambda x: x['date'])
    return dict(sorted(groups.items(), key=lambda x: x[0]))

def commit_time_dt(c: Dict) -> datetime:
    if c.get('date_epoch'):
        try:
            return datetime.fromtimestamp(int(c['date_epoch']))
        except Exception:
            pass
    # Fallback parse from string
    ds = c.get('date', '')
    try:
        return datetime.strptime(ds, "%Y-%m-%d %H:%M:%S %z").astimezone().replace(tzinfo=None)
    except Exception:
        try:
            return datetime.strptime(ds, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return datetime.fromisoformat(ds.replace(' ', 'T').split(' +')[0])

def compute_work_sessions(commits: List[Dict], gap_minutes: int = 60, pull_times: Optional[List[datetime]] = None) -> List[Dict]:
    """
    计算工作会话，支持使用 pull 时间作为会话开始时间。
    
    Args:
        commits: commit 列表
        gap_minutes: 会话间隔（分钟）
        pull_times: pull 操作时间列表（可选）
    
    Returns:
        工作会话列表
    """
    if not commits:
        return []
    items = sorted(commits, key=lambda c: commit_time_dt(c))
    sessions: List[Dict] = []
    gap = timedelta(minutes=gap_minutes)
    
    # 如果有 pull 记录，用于调整会话开始时间
    pull_times_sorted = sorted(pull_times) if pull_times else []
    
    current = {
        'start': commit_time_dt(items[0]),
        'end': commit_time_dt(items[0]),
        'commits': [items[0]],
    }
    
    # 为第一个会话查找对应的 pull 时间
    # 如果第一个 commit 之前有 pull 操作，且时间间隔合理（不超过 gap_minutes），使用 pull 时间作为开始
    first_commit_time = commit_time_dt(items[0])
    if pull_times_sorted:
        # 查找第一个 commit 之前最近的 pull 操作
        # 找到第一个在 commit 时间之前的 pull（如果有的话）
        for pull_time in reversed(pull_times_sorted):
            if pull_time <= first_commit_time:
                # 检查时间间隔是否合理（pull 时间应该在 commit 之前，但不要相隔太久）
                time_diff = (first_commit_time - pull_time).total_seconds() / 60
                # 如果 pull 在 commit 之前且在合理范围内（比如 2 小时内），使用 pull 时间
                if time_diff > 0 and time_diff <= 120:  # 2 小时内的 pull 视为有效
                    current['start'] = pull_time
                    break
    
    for c in items[1:]:
        t = commit_time_dt(c)
        if t - commit_time_dt(current['commits'][-1]) <= gap:
            current['end'] = t
            current['commits'].append(c)
        else:
            # 会话结束，计算时长
            current['duration_minutes'] = max(1, int((current['end'] - current['start']).total_seconds() // 60))
            sessions.append(current)
            
            # 开始新会话
            current = {'start': t, 'end': t, 'commits': [c]}
            
            # 为新会话查找对应的 pull 时间
            if pull_times_sorted:
                # 查找这个 commit 之前最近的 pull 操作
                for pull_time in reversed(pull_times_sorted):
                    if pull_time <= t:
                        time_diff = (t - pull_time).total_seconds() / 60
                        if time_diff > 0 and time_diff <= 120:  # 2 小时内的 pull 视为有效
                            current['start'] = pull_time
                            break
    
    # 处理最后一个会话
    current['duration_minutes'] = max(1, int((current['end'] - current['start']).total_seconds() // 60))
    sessions.append(current)
    return sessions

def compute_feature_windows(commits: List[Dict]) -> Dict[str, Dict]:
    # Group by leading token of commit message (e.g., feat, fix, docs, build, refactor)
    windows: Dict[str, Dict] = {}
    for c in commits:
        msg = c.get('message', '').strip()
        token = msg.split(':', 1)[0].lower().split(' ')[0]
        key = token if token in ['feat', 'fix', 'docs', 'build', 'refactor', 'chore', 'perf', 'test'] else 'other'
        t = commit_time_dt(c)
        w = windows.get(key)
        if not w:
            windows[key] = {'start': t, 'end': t, 'count': 1}
        else:
            if t < w['start']:
                w['start'] = t
            if t > w['end']:
                w['end'] = t
            w['count'] += 1
    return windows

def detect_parallel_sessions(repo_to_sessions: Dict[str, List[Dict]]) -> List[Dict]:
    """
    检测跨项目的并行工作时段。
    返回重叠的时间段及其涉及的项目列表。
    """
    if len(repo_to_sessions) < 2:
        return []  # 单项目不需要检测并行
    
    all_periods: List[Dict] = []
    for repo, sessions in repo_to_sessions.items():
        for s in sessions:
            all_periods.append({
                'start': s['start'],
                'end': s['end'],
                'repo': repo,
                'session': s
            })
    
    if not all_periods:
        return []
    
    # 合并重叠时段算法：按时间线扫描，合并连续或重叠的时段
    all_periods.sort(key=lambda x: (x['start'], x['end']))
    merged_overlaps: List[Dict] = []
    
    current_overlaps = []  # 当前正在重叠的时段组
    
    for period in all_periods:
        if not current_overlaps:
            current_overlaps = [period]
            continue
        
        # 检查当前时段是否与已有重叠组有时间重叠
        can_merge = False
        for existing in current_overlaps:
            if not (period['end'] < existing['start'] or period['start'] > existing['end']):
                can_merge = True
                break
        
        if can_merge:
            current_overlaps.append(period)
        else:
            # 结束当前重叠组，开始新的
            if len(set(p['repo'] for p in current_overlaps)) > 1:
                overlap_start = min(p['start'] for p in current_overlaps)
                overlap_end = max(p['end'] for p in current_overlaps)
                overlap_repos = sorted(set(p['repo'] for p in current_overlaps))
                merged_overlaps.append({
                    'start': overlap_start,
                    'end': overlap_end,
                    'repos': overlap_repos,
                    'duration_minutes': int((overlap_end - overlap_start).total_seconds() // 60)
                })
            current_overlaps = [period]
    
    # 处理最后一组
    if len(set(p['repo'] for p in current_overlaps)) > 1:
        overlap_start = min(p['start'] for p in current_overlaps)
        overlap_end = max(p['end'] for p in current_overlaps)
        overlap_repos = sorted(set(p['repo'] for p in current_overlaps))
        merged_overlaps.append({
            'start': overlap_start,
            'end': overlap_end,
            'repos': overlap_repos,
            'duration_minutes': int((overlap_end - overlap_start).total_seconds() // 60)
        })
    
    # 再次合并可能连续或部分重叠的时段
    if not merged_overlaps:
        return []
    
    final_merged: List[Dict] = []
    merged_overlaps.sort(key=lambda x: (x['start'], x['end']))
    
    current = merged_overlaps[0]
    for next_period in merged_overlaps[1:]:
        # 如果时间有重叠或连续（间隔小于5分钟视为连续），且涉及相同项目，则合并
        gap = (next_period['start'] - current['end']).total_seconds() / 60
        if gap <= 5 or not (next_period['end'] < current['start'] or next_period['start'] > current['end']):
            # 合并
            current['start'] = min(current['start'], next_period['start'])
            current['end'] = max(current['end'], next_period['end'])
            current['repos'] = sorted(set(current['repos']) | set(next_period['repos']))
            current['duration_minutes'] = int((current['end'] - current['start']).total_seconds() // 60)
        else:
            final_merged.append(current)
            current = next_period
    final_merged.append(current)
    
    return final_merged

def build_commit_context_by_project(repo_to_grouped: Dict[str, Dict[str, List[Dict]]], repo_to_details: Dict[str, Dict[str, Tuple[List[str], int, int, str]]], gap_minutes: int = 60, repo_to_pull_times: Optional[Dict[str, List[datetime]]] = None) -> str:
    lines: List[str] = []
    
    # 先计算所有项目的会话，用于检测并行工作
    repo_to_sessions: Dict[str, List[Dict]] = {}
    for repo_name, grouped in repo_to_grouped.items():
        flat_commits: List[Dict] = []
        for items in grouped.values():
            flat_commits.extend(items)
        # 获取该仓库的 pull 时间（如果是本地仓库）
        pull_times = repo_to_pull_times.get(repo_name, []) if repo_to_pull_times else []
        sessions = compute_work_sessions(flat_commits, gap_minutes, pull_times)
        repo_to_sessions[repo_name] = sessions
    
    # 检测跨项目并行工作时间
    parallel_periods = detect_parallel_sessions(repo_to_sessions)
    if parallel_periods:
        lines.append("# 跨项目并行工作时间段")
        total_parallel_minutes = sum(p['duration_minutes'] for p in parallel_periods)
        lines.append(f"检测到 {len(parallel_periods)} 个并行工作时段，总重叠时长约 {total_parallel_minutes} 分钟")
        for idx, p in enumerate(parallel_periods, 1):
            repos_str = ', '.join(p['repos'])
            lines.append(f"- 并行时段{idx}: {p['start']} ~ {p['end']} ({p['duration_minutes']} 分钟, 涉及项目: {repos_str})")
        lines.append("")
    
    # 各项目详细统计
    for repo_name, grouped in repo_to_grouped.items():
        if len(grouped) ==0:
            continue
        lines.append(f"\n# 项目：{repo_name}")
        sessions = repo_to_sessions[repo_name]
        if sessions:
            total_minutes = sum(s['duration_minutes'] for s in sessions)
            lines.append(f"工作会话: {len(sessions)} 个，总时长约 {total_minutes} 分钟")
            for idx, s in enumerate(sessions, 1):
                # 标记是否为并行时段
                is_parallel = any(
                    not (s['end'] < pp['start'] or s['start'] > pp['end'])
                    for pp in parallel_periods
                    if repo_name in pp['repos']
                )
                parallel_marker = " [并行]" if is_parallel else ""
                lines.append(f"- 会话{idx}: {s['start']} ~ {s['end']} ({s['duration_minutes']} 分钟, {len(s['commits'])} 次提交){parallel_marker}")
        # Feature windows
        flat_commits: List[Dict] = []
        for items in grouped.values():
            flat_commits.extend(items)
        fw = compute_feature_windows(flat_commits)
        if fw:
            lines.append("功能窗口:")
            for k, v in fw.items():
                duration = int((v['end'] - v['start']).total_seconds() // 60)
                lines.append(f"- {k}: {v['start']} ~ {v['end']} ({duration} 分钟, {v['count']} 次提交)")
        for day, items in grouped.items():
            lines.append(f"\n## {day} ({len(items)} commits)")
            for c in items:
                sha = c['sha']
                files, ins, dels, body = repo_to_details[repo_name].get(sha, ([], 0, 0, ""))
                short_sha = sha[:8]
                time_part = ' '.join(c['date'].split(' ')[1:3]) if ' ' in c['date'] else c['date']
                lines.append(f"\n- [{short_sha}] {time_part}")
                lines.append(f"  提交信息: {c['message']}")
                lines.append(f"  统计: {ins} 行新增, {dels} 行删除, {len(files)} 个文件")
                if body and body.strip() != c['message']:
                    lines.append(f"  详细内容:\n{body}")
                if files:
                    lines.append(f"  修改的文件: {', '.join(files[:20])}{' ...' if len(files) > 20 else ''}")
    return "\n".join(lines)

def generate_summary_with_openai(
    grouped: Dict[str, List[Dict]], 
    details: Dict[str, Tuple[List[str], int, int, str]],
    system_prompt: Optional[str] = None,
    openai_api_key: Optional[str] = None,
    model: str = "gpt-4o-mini",
    author: Optional[str] = None,
    gap_minutes: int = 60,
    repo_to_pull_times: Optional[Dict[str, List[datetime]]] = None
) -> str:
    """
    使用 OpenAI API 生成工作总结。
    如果没有提供 API key，会尝试从环境变量 OPENAI_API_KEY 获取。
    """
    if not OPENAI_AVAILABLE:
        return "错误：未安装 openai 包。请运行: pip install openai"
    
    api_key = openai_api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return "错误：未提供 OpenAI API key。请设置环境变量 OPENAI_API_KEY 或使用 --openai-key 参数"
    
    client = OpenAI(api_key=api_key)
    
    # 兼容单项目或多项目上下文
    if isinstance(grouped, dict) and grouped and all(isinstance(v, dict) for v in grouped.values()):
        # 多项目：grouped: repo -> {day -> commits}
        repo_to_grouped = grouped  # type: ignore
        repo_to_details = details  # type: ignore
        # 使用传入的 repo_to_pull_times（如果提供）
        commit_context = build_commit_context_by_project(repo_to_grouped, repo_to_details, gap_minutes, repo_to_pull_times)  # type: ignore
    else:
        context_lines = []
        for day, items in grouped.items():
            context_lines.append(f"\n## {day} ({len(items)} commits)")
            for c in items:
                sha = c['sha']
                files, ins, dels, body = details.get(sha, ([], 0, 0, ""))
                short_sha = sha[:8]
                time_part = ' '.join(c['date'].split(' ')[1:3]) if ' ' in c['date'] else c['date']
                context_lines.append(f"\n- [{short_sha}] {time_part}")
                context_lines.append(f"  提交信息: {c['message']}")
                context_lines.append(f"  统计: {ins} 行新增, {dels} 行删除, {len(files)} 个文件")
                if body and body.strip() != c['message']:
                    context_lines.append(f"  详细内容:\n{body}")
                if files:
                    context_lines.append(f"  修改的文件: {', '.join(files[:20])}{' ...' if len(files) > 20 else ''}")
        commit_context = "\n".join(context_lines)
    if len(commit_context) <10:
        return "今天无工作，无法生成工作总结。"
    system_msg = system_prompt or default_system_prompt + "\n此外，请按项目分别估算投入时间（根据提交时间密度与连续性），并给出每个项目的主要产出。"
    if author:
        system_msg += f"\n此外，请基于作者姓名或邮箱包含“{author}”的提交进行工作总结，并在摘要开头显式标注：作者：{author}。"
        user_msg = f"请根据以下 commit 记录生成{author}工作总结：\n\n{commit_context}"
        user_msg += PEI
    else:
        user_msg = f"请根据以下 commit 记录生成工作总结：\n\n{commit_context}"
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg}
            ],
            temperature=0.3
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"错误：调用 OpenAI API 失败: {str(e)}"

def generate_summary_with_deepseek(
    grouped: Dict[str, List[Dict]],
    details: Dict[str, Tuple[List[str], int, int, str]],
    system_prompt: Optional[str] = None,
    deepseek_api_key: Optional[str] = None,
    model: str = "deepseek-chat",
    author: Optional[str] = None,
    gap_minutes: int = 60,
    repo_to_pull_times: Optional[Dict[str, List[datetime]]] = None
) -> str:
    """
    使用 DeepSeek API 生成工作总结（OpenAI 兼容的 Chat Completions 格式）。
    """
    final_key = deepseek_api_key or os.getenv("DEEPSEEK_API_KEY")
    if not final_key:
        return "错误：未提供 DeepSeek API key。请设置环境变量 DEEPSEEK_API_KEY 或使用 --deepseek-key 参数"

    # 构建上下文（支持多项目）
    if isinstance(grouped, dict) and grouped and all(isinstance(v, dict) for v in grouped.values()):
        # 使用传入的 repo_to_pull_times（如果提供）
        commit_context = build_commit_context_by_project(grouped, details, gap_minutes, repo_to_pull_times)  # type: ignore
    else:
        context_lines = []
        for day, items in grouped.items():
            context_lines.append(f"\n## {day} ({len(items)} commits)")
            for c in items:
                sha = c['sha']
                files, ins, dels, body = details.get(sha, ([], 0, 0, ""))
                short_sha = sha[:8]
                time_part = ' '.join(c['date'].split(' ')[1:3]) if ' ' in c['date'] else c['date']
                context_lines.append(f"\n- [{short_sha}] {time_part}")
                context_lines.append(f"  提交信息: {c['message']}")
                context_lines.append(f"  统计: {ins} 行新增, {dels} 行删除, {len(files)} 个文件")
                if body and body.strip() != c['message']:
                    context_lines.append(f"  详细内容:\n{body}")
                if files:
                    context_lines.append(f"  修改的文件: {', '.join(files[:20])}{' ...' if len(files) > 20 else ''}")
        commit_context = "\n".join(context_lines)
    if len(commit_context) <10:
        return "今天无工作，无法生成工作总结。"
    system_msg = system_prompt or default_system_prompt + "\n此外，请按项目分别估算投入时间（根据提交时间密度与连续性），并给出每个项目的主要产出。"
    if author:
        system_msg += f"\n此外，请基于作者姓名或邮箱包含“{author}”的提交进行工作总结，并在摘要开头显式标注：作者：{author}。"
        user_msg = f"请根据以下 commit 记录生成{author}工作总结：\n\n{commit_context}"
        user_msg += PEI
    else:
        user_msg = f"请根据以下 commit 记录生成工作总结：\n\n{commit_context}"
    

    # 映射模型名称（DeepSeek 的正确模型名称）
    model_map = {
        "deepseek-chat": "deepseek-chat",
        "deepseek-reasoner": "deepseek-reasoner",
        "chat": "deepseek-chat"  # 默认
    }
    actual_model = model_map.get(model.lower(), "deepseek-chat")

    try:
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {final_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": actual_model,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg}
            ],
            "temperature": 0.3
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except requests.exceptions.HTTPError as e:
        error_detail = ""
        try:
            error_detail = f" - {resp.text}"
        except:
            pass
        return f"错误：调用 DeepSeek API 失败: {str(e)}{error_detail}"
    except Exception as e:
        return f"错误：调用 DeepSeek API 失败: {str(e)}"

def render_markdown_worklog(
    title: str, 
    grouped: Dict[str, List[Dict]], 
    details: Dict[str, Tuple[List[str], int, int, str]],
    add_summary: bool = False,
    summary_text: Optional[str] = None
) -> str:
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
            sha = c['sha']
            short_sha = sha[:8]
            files, ins, dels, body = details.get(sha, ([], 0, 0, ""))
            time_part = ' '.join(c['date'].split(' ')[1:3]) if ' ' in c['date'] else c['date']
            lines.append(f"- [{short_sha}] {time_part} | {c['message']} ({ins}+/{dels}-; {len(files)} files)")
            if files:
                lines.append(f"  - files: {', '.join(files[:10])}{' ...' if len(files) > 10 else ''}")
            if body:
                lines.append("  - message:")
                lines.append("```")
                lines.extend(body.splitlines())
                lines.append("```")
        lines.append("")
    
    # 添加总结
    if add_summary and summary_text:
        lines.append(summary_text)
    
    return "\n".join(lines)

def render_multi_project_worklog(title: str, repo_to_grouped: Dict[str, Dict[str, List[Dict]]], repo_to_details: Dict[str, Dict[str, Tuple[List[str], int, int, str]]], add_summary: bool = False, summary_text: Optional[str] = None, gap_minutes: int = 60, repo_to_pull_times: Optional[Dict[str, List[datetime]]] = None) -> str:
    lines: List[str] = []
    lines.append(f"# {title}")
    lines.append("")
    total_commits = sum(sum(len(v) for v in grouped.values()) for grouped in repo_to_grouped.values())
    lines.append(f"总计 {total_commits} 个提交，项目数 {len(repo_to_grouped)}")
    lines.append("")
    
    # 计算并行工作时间
    repo_to_sessions: Dict[str, List[Dict]] = {}
    for repo_name, grouped in repo_to_grouped.items():
        flat_commits: List[Dict] = []
        for items in grouped.values():
            flat_commits.extend(items)
        # 获取该仓库的 pull 时间（如果是本地仓库）
        pull_times = repo_to_pull_times.get(repo_name, []) if repo_to_pull_times else []
        sessions = compute_work_sessions(flat_commits, gap_minutes, pull_times)
        repo_to_sessions[repo_name] = sessions
    
    parallel_periods = detect_parallel_sessions(repo_to_sessions)
    if parallel_periods:
        lines.append("## 跨项目并行工作时间统计")
        total_parallel_minutes = sum(p['duration_minutes'] for p in parallel_periods)
        lines.append(f"检测到 **{len(parallel_periods)} 个并行工作时段**，总重叠时长约 **{total_parallel_minutes} 分钟**")
        lines.append("")
        for idx, p in enumerate(parallel_periods, 1):
            repos_str = ', '.join(p['repos'])
            lines.append(f"- **并行时段 {idx}**：{p['start'].strftime('%Y-%m-%d %H:%M')} ~ {p['end'].strftime('%Y-%m-%d %H:%M')} ({p['duration_minutes']} 分钟)")
            lines.append(f"  - 涉及项目：{repos_str}")
        lines.append("")
        lines.append("> 注意：并行工作时间不应简单累加，实际投入时间以重叠时段的最大值为准。")
        lines.append("")
    
    # 各项目时间统计
    lines.append("## 各项目时间统计")
    for repo_name, grouped in repo_to_grouped.items():
        sessions = repo_to_sessions[repo_name]
        if sessions:
            total_minutes = sum(s['duration_minutes'] for s in sessions)
            lines.append(f"### {repo_name}")
            lines.append(f"- 工作会话：{len(sessions)} 个，总时长约 {total_minutes} 分钟")
            for idx, s in enumerate(sessions, 1):
                is_parallel = any(
                    not (s['end'] < pp['start'] or s['start'] > pp['end'])
                    for pp in parallel_periods
                    if repo_name in pp['repos']
                )
                parallel_marker = " **[并行]**" if is_parallel else ""
                lines.append(f"  - 会话{idx}：{s['start'].strftime('%H:%M')} ~ {s['end'].strftime('%H:%M')} ({s['duration_minutes']} 分钟, {len(s['commits'])} 次提交){parallel_marker}")
    lines.append("")
    for repo_name, grouped in repo_to_grouped.items():
        lines.append(f"# 项目：{repo_name}")
        lines.append("")
        for day, items in grouped.items():
            lines.append(f"## {day} ({len(items)} commits)")
            lines.append("")
            for c in items:
                sha = c['sha']
                short_sha = sha[:8]
                files, ins, dels, body = repo_to_details[repo_name].get(sha, ([], 0, 0, ""))
                time_part = ' '.join(c['date'].split(' ')[1:3]) if ' ' in c['date'] else c['date']
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

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate work log from git commits")
    parser.add_argument('--repo', type=str, default=None, help='Path to git repository (single)')
    parser.add_argument('--repos', type=str, default=None, help='Multiple repositories, comma-separated')
    parser.add_argument('--github', type=str, default=None, help='GitHub repository (format: OWNER/REPO, comma-separated for multiple)')
    parser.add_argument('--gitee', type=str, default=None, help='Gitee repository (format: OWNER/REPO, comma-separated for multiple)')
    parser.add_argument('--github-token', type=str, default=None, help='GitHub token (or set GITHUB_TOKEN env var)')
    parser.add_argument('--gitee-token', type=str, default=None, help='Gitee token (or set GITEE_TOKEN env var)')
    parser.add_argument('--since', type=str, default=None, help='Start datetime (ISO or YYYY-MM-DD)')
    parser.add_argument('--until', type=str, default=None, help='End datetime (ISO or YYYY-MM-DD)')
    parser.add_argument('--days', type=int, default=None, help='If set, use last N days ending today')
    parser.add_argument('--session-gap-minutes', type=int, default=60, help='Gap minutes to split work sessions')
    parser.add_argument('--author', type=str, default=None, help='Filter by author name or email (optional)')
    parser.add_argument('--output', type=str, default=None, help='Output file path (.md). If not set, print to stdout')
    parser.add_argument('--title', type=str, default=None, help='Title for the work log document')
    parser.add_argument('--add-summary', action='store_true', help='Add AI-generated Chinese summary at the end')
    parser.add_argument('--openai-key', type=str, default=None, help='OpenAI API key (or set OPENAI_API_KEY env var)')
    parser.add_argument('--openai-model', type=str, default='gpt-4o-mini', help='OpenAI model to use (default: gpt-4o-mini)')
    parser.add_argument('--system-prompt-file', type=str, default=None, help='Path to custom system prompt file')
    # DeepSeek 支持
    parser.add_argument('--provider', type=str, default='openai', choices=['openai', 'deepseek'], help='LLM provider')
    parser.add_argument('--deepseek-key', type=str, default=None, help='DeepSeek API key (or set DEEPSEEK_API_KEY env var)')
    parser.add_argument('--deepseek-model', type=str, default='deepseek-chat', help='DeepSeek model (e.g., deepseek-chat, deepseek-reasoner)')
    return parser.parse_args()

def parse_date_input(value: Optional[str], default_dt: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return default_dt
    # try ISO then YYYY-MM-DD
    try:
        return datetime.fromisoformat(value)
    except Exception:
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except Exception:
            raise ValueError(f"无法解析日期: {value}")

def git2work():
    args = parse_args()
    repo_paths: List[str] = []
    github_repos: List[str] = []
    gitee_repos: List[str] = []
    
    # 解析本地仓库
    if args.repos:
        repo_paths = [p.strip() for p in args.repos.split(',') if p.strip()]
    elif args.repo:
        repo_paths = [args.repo]
    
    # 解析 GitHub 仓库
    if args.github:
        github_repos = [r.strip() for r in args.github.split(',') if r.strip()]
    
    # 解析 Gitee 仓库
    if args.gitee:
        gitee_repos = [r.strip() for r in args.gitee.split(',') if r.strip()]
    
    # 如果没有任何仓库指定，使用默认本地仓库
    if not repo_paths and not github_repos and not gitee_repos:
        repo_paths = ["/mnt/d/works/RayTracy"]

    now = datetime.now()
    if args.days is not None and args.days > 0:
        start = (now - timedelta(days=args.days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    else:
        start = parse_date_input(args.since, now.replace(hour=0, minute=0, second=0, microsecond=0))
        end = parse_date_input(args.until, now.replace(hour=23, minute=59, second=59, microsecond=0))
        if start is not None:
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        if end is not None:
            end = end.replace(hour=23, minute=59, second=59, microsecond=0)

    # 获取 GitHub token
    github_token = args.github_token or GITHUB_TOKEN
    if github_repos and not github_token:
        print("Warning: GitHub 仓库需要 token，请设置 --github-token 或环境变量 GITHUB_TOKEN")
        github_repos = []  # 跳过 GitHub 仓库
    
    # 获取 Gitee token
    gitee_token = args.gitee_token or GITEE_TOKEN
    if gitee_repos and not gitee_token:
        print("Warning: Gitee 仓库需要 token，请设置 --gitee-token 或环境变量 GITEE_TOKEN")
        gitee_repos = []  # 跳过 Gitee 仓库

    # 计算实际可用的仓库总数
    total_repos = len(repo_paths) + len(github_repos) + len(gitee_repos)
    # 如果任何一个类型有多个仓库，或者总仓库数大于1，都进入多项目模式
    multi_project = (len(repo_paths) > 1 or len(github_repos) > 1 or len(gitee_repos) > 1 or total_repos > 1)

    if not multi_project:
        # 单项目模式（只有单个仓库或单个来自不同位置的仓库）
        commits: List[Dict] = []
        details: Dict[str, Tuple[List[str], int, int, str]] = {}
        pull_times: List[datetime] = []  # 用于单项目模式的 pull 时间
        
        # 处理本地仓库（最多一个）
        if repo_paths:
            repo = repo_paths[0]
            commits = get_commits_between(repo, start, end)
            if args.author:
                author_lower = args.author.lower()
                commits = [c for c in commits if author_lower in c['author_name'].lower() or author_lower in c['author_email'].lower()]
            # 获取 pull 操作时间
            pull_times = get_pull_operations(repo, start, end)
            for c in commits:
                files, ins, dels = get_commit_numstat(repo, c['sha'])
                body = get_commit_body(repo, c['sha'])
                details[c['sha']] = (files, ins, dels, body)
        
        # 处理 GitHub 仓库（最多一个）
        if github_repos and github_token:
            repo_name = github_repos[0]
            try:
                remote_commits = get_github_events(repo_name, github_token, start, end)
                if args.author:
                    author_lower = args.author.lower()
                    remote_commits = [c for c in remote_commits if author_lower in c['author_name'].lower()]
                commits.extend(remote_commits)
                # 远程仓库无法获取 numstat，使用占位值
                for c in remote_commits:
                    details[c['sha']] = ([], 0, 0, c['message'])
            except Exception as e:
                print(f"Error: 获取 GitHub 仓库 {repo_name} 失败: {e}")
        
        # 处理 Gitee 仓库（最多一个）
        if gitee_repos and gitee_token:
            repo_name = gitee_repos[0]
            try:
                remote_commits = get_gitee_events(repo_name, gitee_token, start, end)
                if args.author:
                    author_lower = args.author.lower()
                    remote_commits = [c for c in remote_commits if author_lower in c['author_name'].lower()]
                commits.extend(remote_commits)
                # 远程仓库无法获取 numstat，使用占位值
                for c in remote_commits:
                    details[c['sha']] = ([], 0, 0, c['message'])
            except Exception as e:
                print(f"Error: 获取 Gitee 仓库 {repo_name} 失败: {e}")
        
        # 按时间排序所有 commits
        commits.sort(key=lambda c: commit_time_dt(c))
        grouped = group_commits_by_date(commits)
        
        # 为单项目模式初始化 repo_to_pull_times_multi
        # 注意：单项目模式不显示会话统计，但保留变量以便一致性
        repo_to_pull_times_multi = None  # type: ignore
    else:
        # 多项目模式
        repo_to_commits: Dict[str, List[Dict]] = {}
        repo_to_details: Dict[str, Dict[str, Tuple[List[str], int, int, str]]] = {}
        repo_to_grouped: Dict[str, Dict[str, List[Dict]]] = {}
        repo_to_pull_times: Dict[str, List[datetime]] = {}  # 存储每个本地仓库的 pull 时间
        
        # 处理本地仓库
        for repo in repo_paths:
            commits = get_commits_between(repo, start, end)
            if args.author:
                author_lower = args.author.lower()
                commits = [c for c in commits if author_lower in c['author_name'].lower() or author_lower in c['author_email'].lower()]
            # 获取 pull 操作时间（仅本地仓库）
            pull_times = get_pull_operations(repo, start, end)
            repo_to_pull_times[repo] = pull_times
            repo_to_commits[repo] = commits
            details_map: Dict[str, Tuple[List[str], int, int, str]] = {}
            for c in commits:
                files, ins, dels = get_commit_numstat(repo, c['sha'])
                body = get_commit_body(repo, c['sha'])
                details_map[c['sha']] = (files, ins, dels, body)
            repo_to_details[repo] = details_map
            repo_to_grouped[repo] = group_commits_by_date(commits)
        
        # 处理 GitHub 仓库
        for repo_name in github_repos:
            if github_token:
                try:
                    commits = get_github_events(repo_name, github_token, start, end)
                    if args.author:
                        author_lower = args.author.lower()
                        commits = [c for c in commits if author_lower in c['author_name'].lower()]
                    repo_to_commits[repo_name] = commits
                    details_map: Dict[str, Tuple[List[str], int, int, str]] = {}
                    # 远程仓库无法获取 numstat，使用占位值
                    for c in commits:
                        details_map[c['sha']] = ([], 0, 0, c['message'])
                    repo_to_details[repo_name] = details_map
                    repo_to_grouped[repo_name] = group_commits_by_date(commits)
                except Exception as e:
                    print(f"Error: 获取 GitHub 仓库 {repo_name} 失败: {e}")
        
        # 处理 Gitee 仓库
        for repo_name in gitee_repos:
            if gitee_token:
                try:
                    commits = get_gitee_events(repo_name, gitee_token, start, end)
                    if args.author:
                        author_lower = args.author.lower()
                        commits = [c for c in commits if author_lower in c['author_name'].lower()]
                    repo_to_commits[repo_name] = commits
                    details_map: Dict[str, Tuple[List[str], int, int, str]] = {}
                    # 远程仓库无法获取 numstat，使用占位值
                    for c in commits:
                        details_map[c['sha']] = ([], 0, 0, c['message'])
                    repo_to_details[repo_name] = details_map
                    repo_to_grouped[repo_name] = group_commits_by_date(commits)
                except Exception as e:
                    print(f"Error: 获取 Gitee 仓库 {repo_name} 失败: {e}")
        
        grouped = repo_to_grouped  # type: ignore
        details = repo_to_details  # type: ignore
        # 保存 repo_to_pull_times 以便后续使用
        repo_to_pull_times_multi = repo_to_pull_times  # type: ignore

    title = args.title or (f"Work Log: {start.date()} to {end.date()}" if start and end else "Work Log")
    
    # 生成总结（如果需要）
    summary_text = None
    if args.add_summary:
        print("正在生成 AI 总结...")
        # 读取自定义提示词（如果有）
        system_prompt = None
        if args.system_prompt_file and os.path.exists(args.system_prompt_file):
            with open(args.system_prompt_file, 'r', encoding='utf-8') as f:
                system_prompt = f.read()
        
        # 准备 repo_to_pull_times（仅在多项目模式下使用）
        repo_to_pull_times_for_summary = repo_to_pull_times_multi if multi_project else None
        
        if getattr(args, 'provider', 'openai') == 'deepseek':
            summary_text = generate_summary_with_deepseek(
                grouped,  # type: ignore
                details,  # type: ignore
                system_prompt=system_prompt,
                deepseek_api_key=args.deepseek_key,
                model=args.deepseek_model,
                author=args.author,
                gap_minutes=args.session_gap_minutes,
                repo_to_pull_times=repo_to_pull_times_for_summary
            )
        else:
            summary_text = generate_summary_with_openai(
                grouped,  # type: ignore
                details,  # type: ignore
                system_prompt=system_prompt,
                openai_api_key=args.openai_key,
                model=args.openai_model,
                author=args.author,
                gap_minutes=args.session_gap_minutes,
                repo_to_pull_times=repo_to_pull_times_for_summary
            )
        print("AI 总结生成完成")
    
    if not multi_project:
        md = render_markdown_worklog(title, grouped, details, add_summary=args.add_summary, summary_text=summary_text)  # type: ignore
    else:
        md = render_multi_project_worklog(title, grouped, details, add_summary=args.add_summary, summary_text=summary_text, gap_minutes=args.session_gap_minutes, repo_to_pull_times=repo_to_pull_times_multi)  # type: ignore

    if args.output:
        os.makedirs(os.path.dirname(args.output), exist_ok=True) if os.path.dirname(args.output) else None
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(md)
        print(f"已写入: {args.output}")
    else:
        print(md)

if __name__ == "__main__":
    git2work()