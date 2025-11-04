"""Implementation of git_catalog command execution."""
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from dateutil import parser as dateparser
from github import Auth, Github, GithubException, RateLimitExceededException

from .models import (
    AuthorsByRepoArgs,
    CatalogProvider,
    CrossReposArgs,
    CmdCatalog,
    GitCatalogInput,
    OrgReposArgs,
    RepoAuthorsArgs,
    ReposByAuthorArgs,
    SearchReposArgs,
    UserReposArgs,
)


# ──────────────────────────────────────────────────────────────
# 通用工具
# ──────────────────────────────────────────────────────────────


def gh_client() -> Github:
    """从环境变量 GITHUB_TOKEN 读取 token；未设置则匿名（速率 60/h）。"""
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return Github(per_page=100)
    # 使用新的 Auth API 避免弃用警告
    auth = Auth.Token(token)
    return Github(auth=auth, per_page=100)


def gitee_client() -> Dict[str, Any]:
    """创建 Gitee API 客户端（返回配置字典）。
    
    Gitee API v5 支持两种认证方式：
    1. Header: Authorization: token {token}
    2. URL 参数: ?access_token={token}
    
    某些 token 类型可能只支持 URL 参数方式，所以我们在每个请求中都使用 URL 参数方式。
    """
    token = os.getenv("GITEE_TOKEN")
    base_url = "https://gitee.com/api/v5"
    headers = {}
    # 注意：Gitee API v5 可能要求使用 URL 参数 access_token 而不是 Header
    # 我们在实际请求时会将 token 添加到 URL 参数中
    return {
        "base_url": base_url,
        "headers": headers,
        "token": token,
    }


def _check_gitee_token_error(resp: requests.Response) -> Optional[str]:
    """检查 Gitee API 响应中的 token 相关错误，返回友好的错误消息。"""
    if resp.status_code == 401:
        try:
            error_data = resp.json()
            error_msg = error_data.get("message", "")
            if "token" in error_msg.lower() or "unauthorized" in error_msg.lower():
                if "wrong type" in error_msg.lower():
                    return "Gitee API token 类型错误。请确保使用 Personal Access Token（私人令牌），而不是 OAuth token。\n可以在 https://gitee.com/profile/personal_access_tokens 创建 Personal Access Token"
                return "Gitee API 认证失败。请检查：\n1. GITEE_TOKEN 环境变量是否正确设置\n2. Token 是否有效（可能已过期）\n3. Token 是否有足够的权限\n4. 确保使用的是 Personal Access Token（私人令牌）\n\n可以在 https://gitee.com/profile/personal_access_tokens 创建或更新 token"
        except Exception:
            pass
    return None


def _add_gitee_auth(params: Dict[str, Any], client: Dict[str, Any]) -> Dict[str, Any]:
    """为 Gitee API 请求添加认证参数。
    
    Gitee API v5 支持两种认证方式：
    1. URL 参数: access_token={token}（推荐，兼容性更好）
    2. Header: Authorization: token {token}
    
    我们优先使用 URL 参数方式，因为某些 token 类型只支持这种方式。
    """
    result_params = params.copy()
    if client.get("token"):
        # 使用 URL 参数方式（兼容性更好）
        result_params["access_token"] = client["token"]
    return result_params


def gitlab_client() -> Dict[str, Any]:
    """创建 GitLab API 客户端（返回配置字典）。
    
    GitLab API v4 认证方式：
    - Header: PRIVATE-TOKEN: {token}
    - 或使用 OAuth token: Authorization: Bearer {token}
    
    支持自定义 GitLab 实例 URL（通过 GITLAB_URL 环境变量）。
    """
    token = os.getenv("GITLAB_TOKEN") or os.getenv("GITLAB_PRIVATE_TOKEN")
    base_url = os.getenv("GITLAB_URL", "https://gitlab.com/api/v4")
    headers = {}
    if token:
        # GitLab 使用 PRIVATE-TOKEN header
        headers["PRIVATE-TOKEN"] = token
    return {
        "base_url": base_url,
        "headers": headers,
        "token": token,
    }


def _check_gitlab_token_error(resp: requests.Response) -> Optional[str]:
    """检查 GitLab API 响应中的 token 相关错误，返回友好的错误消息。"""
    if resp.status_code == 401:
        try:
            error_data = resp.json()
            error_msg = error_data.get("message", "")
            if "unauthorized" in error_msg.lower() or "token" in error_msg.lower():
                return "GitLab API 认证失败。请检查：\n1. GITLAB_TOKEN 或 GITLAB_PRIVATE_TOKEN 环境变量是否正确设置\n2. Token 是否有效（可能已过期）\n3. Token 是否有足够的权限（至少需要 read_api 权限）\n4. 如果使用自定义 GitLab 实例，请确保 GITLAB_URL 环境变量正确设置\n\n可以在 GitLab 设置中创建 Personal Access Token：https://gitlab.com/-/profile/personal_access_tokens"
        except Exception:
            pass
    return None


def parse_dt(s: Optional[str]) -> Optional[datetime]:
    """解析时间字符串为 datetime 对象（带 UTC 时区）。"""
    if not s:
        return None
    dt = dateparser.parse(s)
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def rate_limit_guard(g: Github, min_core_remaining: int = 5, min_sleep_sec: int = 8) -> None:
    """简单速率保护：配额接近耗尽时休眠到 reset。"""
    try:
        rl = g.get_rate_limit()
        core_rem = rl.core.remaining
        if core_rem <= min_core_remaining:
            reset = rl.core.reset.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            wait = max((reset - now).total_seconds(), min_sleep_sec)
            time.sleep(wait)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────
# 具体实现函数
# ──────────────────────────────────────────────────────────────


def _fetch_user_activity_across_repos(g: Github, args: CrossReposArgs) -> List[Dict[str, Any]]:
    """不同仓库同一作者（明细）。"""
    rows: List[Dict[str, Any]] = []

    # 枚举仓库列表
    if args.owner:
        try:
            user_or_org = g.get_user(args.owner)
        except GithubException:
            user_or_org = g.get_organization(args.owner)
        repos = user_or_org.get_repos(type=args.repo_type, sort="updated")
    else:
        if not args.author_login:
            raise ValueError("未提供 owner 时，必须提供 author_login 才能枚举其仓库。")
        user = g.get_user(args.author_login)
        repos = user.get_repos(type=args.repo_type, sort="updated")

    since = parse_dt(args.since)
    until = parse_dt(args.until)

    for repo in repos:
        rate_limit_guard(g)
        full = repo.full_name
        try:
            if args.author_login:
                commits = repo.get_commits(author=args.author_login, since=since, until=until)
            else:
                commits = repo.get_commits(since=since, until=until)

            cnt = 0
            for c in commits:
                if args.author_email:
                    au = getattr(c.commit, "author", None)
                    em = getattr(au, "email", None) if au else None
                    if not em or em.lower() != args.author_email.lower():
                        continue

                rows.append({
                    "repo": full,
                    "sha": c.sha,
                    "date": str(getattr(c.commit.author, "date", "")),
                    "author_login": c.author.login if c.author else "",
                    "author_name": getattr(getattr(c.commit, "author", None), "name", ""),
                    "author_email": getattr(getattr(c.commit, "author", None), "email", ""),
                    "committer_login": c.committer.login if c.committer else "",
                    "title": (c.commit.message or "").splitlines()[0],
                    "url": c.html_url,
                })
                cnt += 1
                if cnt >= args.max_per_repo:
                    break
        except RateLimitExceededException:
            rate_limit_guard(g, min_core_remaining=100, min_sleep_sec=30)
            continue
        except GithubException as e:
            print(f"[warn] 跳过 {full}: {e}", file=sys.stderr)
            continue

    return rows


def _fetch_repo_activity_across_authors(g: Github, args: RepoAuthorsArgs) -> List[Dict[str, Any]]:
    """同一仓库不同作者（明细）。"""
    rows: List[Dict[str, Any]] = []
    repo = g.get_repo(args.repo_full)
    since = parse_dt(args.since)
    until = parse_dt(args.until)

    authors_login = list({x for x in (args.authors_login or []) if x})
    authors_emails = [x.lower() for x in (args.authors_emails or []) if x]

    # 无作者清单 → 时间窗内全量
    if not authors_login and not authors_emails:
        commits = repo.get_commits(since=since, until=until)
        for c in commits:
            rows.append({
                "repo": args.repo_full,
                "sha": c.sha,
                "date": str(getattr(c.commit.author, "date", "")),
                "author_login": c.author.login if c.author else "",
                "author_name": getattr(getattr(c.commit, "author", None), "name", ""),
                "author_email": getattr(getattr(c.commit, "author", None), "email", ""),
                "committer_login": c.committer.login if c.committer else "",
                "title": (c.commit.message or "").splitlines()[0],
                "url": c.html_url,
            })
        return rows

    # 按 login 拉
    for login in (authors_login or []):
        rate_limit_guard(g)
        try:
            commits = repo.get_commits(author=login, since=since, until=until)
            cnt = 0
            for c in commits:
                if authors_emails:
                    au = getattr(c.commit, "author", None)
                    em = getattr(au, "email", None) if au else None
                    if not em or (em.lower() not in authors_emails):
                        continue

                rows.append({
                    "repo": args.repo_full,
                    "sha": c.sha,
                    "date": str(getattr(c.commit.author, "date", "")),
                    "author_login": c.author.login if c.author else "",
                    "author_name": getattr(getattr(c.commit, "author", None), "name", ""),
                    "author_email": getattr(getattr(c.commit, "author", None), "email", ""),
                    "committer_login": c.committer.login if c.committer else "",
                    "title": (c.commit.message or "").splitlines()[0],
                    "url": c.html_url,
                })
                cnt += 1
                if cnt >= args.max_per_author:
                    break
        except GithubException as e:
            print(f"[warn] login {login}: {e}", file=sys.stderr)

    # 2) 仅邮箱（补漏）
    if authors_emails:
        try:
            commits = repo.get_commits(since=since, until=until)
            cnt = 0
            for c in commits:
                au = getattr(c.commit, "author", None)
                em = getattr(au, "email", None) if au else None
                if not em or (em.lower() not in authors_emails):
                    continue
                rows.append({
                    "repo": args.repo_full,
                    "sha": c.sha,
                    "date": str(getattr(c.commit, "date", "")),
                    "author_login": c.author.login if c.author else "",
                    "author_name": getattr(au, "name", ""),
                    "author_email": em,
                    "committer_login": c.committer.login if c.committer else "",
                    "title": (c.commit.message or "").splitlines()[0],
                    "url": c.html_url,
                })
                cnt += 1
                if cnt >= args.max_per_author:
                    break
        except GithubException as e:
            print(f"[warn] email pass: {e}", file=sys.stderr)

    return rows


def _list_repos_for_author(g: Github, args: ReposByAuthorArgs) -> List[Dict[str, Any]]:
    """同一作者在哪些仓库（列表）。"""
    counts: Dict[str, int] = defaultdict(int)
    rows: List[Dict[str, Any]] = []

    # 枚举仓库
    if args.owner:
        try:
            user_or_org = g.get_user(args.owner)
        except GithubException:
            try:
                user_or_org = g.get_organization(args.owner)
            except GithubException as e:
                # 如果找不到用户或组织，返回空列表
                print(f"[warn] 无法找到用户或组织 '{args.owner}': {e}", file=sys.stderr)
                return []
        repos = user_or_org.get_repos(type=args.repo_type, sort="updated")
    else:
        if not args.author_login:
            raise ValueError("未提供 owner 时，必须提供 author_login。")
        user = g.get_user(args.author_login)
        repos = user.get_repos(type=args.repo_type, sort="updated")

    since = parse_dt(args.since)
    until = parse_dt(args.until)

    for repo in repos:
        rate_limit_guard(g)
        full = repo.full_name
        try:
            if args.author_login:
                commits = repo.get_commits(author=args.author_login, since=since, until=until)
            else:
                commits = repo.get_commits(since=since, until=until)
            cnt = 0
            for c in commits:
                if args.author_email:
                    au = getattr(c.commit, "author", None)
                    em = getattr(au, "email", None) if au else None
                    if not em or em.lower() != args.author_email.lower():
                        continue
                cnt += 1
            if cnt >= args.min_commits:
                counts[full] = cnt
        except RateLimitExceededException:
            rate_limit_guard(g, min_core_remaining=100, min_sleep_sec=30)
            continue
        except GithubException as e:
            print(f"[warn] 跳过 {full}: {e}", file=sys.stderr)
            continue

    for repo_full, cnt in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
        rows.append({"repo": repo_full, "commits": cnt})

    return rows


def _list_authors_for_repo(g: Github, args: AuthorsByRepoArgs) -> List[Dict[str, Any]]:
    """同一仓库活跃作者（列表）。"""
    repo = g.get_repo(args.repo_full)
    since = parse_dt(args.since)
    until = parse_dt(args.until)

    counter: Counter[str] = Counter()
    meta: Dict[str, tuple[str, str]] = {}  # key -> (login, email)

    commits = repo.get_commits(since=since, until=until)
    for c in commits:
        login = c.author.login if c.author else ""
        name = getattr(getattr(c.commit, "author", None), "name", "") or ""
        email = getattr(getattr(c.commit, "author", None), "email", "") or ""

        if args.prefer == "email" and email:
            key = email.lower()
        elif args.prefer == "name" and name:
            key = name
        else:
            key = login or email.lower() or name or "(unknown)"

        counter[key] += 1
        meta.setdefault(key, (login, email))

    rows: List[Dict[str, Any]] = []
    for key, cnt in counter.most_common():
        if cnt < args.min_commits:
            continue
        login, email = meta.get(key, ("", ""))
        rows.append({
            "repo": args.repo_full,
            "author_key": key,
            "author_login": login,
            "author_email": email,
            "commits": cnt,
        })

    return rows


def _search_repos_by_keyword(g: Github, args: SearchReposArgs) -> List[Dict[str, Any]]:
    """关键词检索仓库。"""
    q = [args.keyword, "in:name,description,readme"]
    if args.language:
        q.append(f"language:{args.language}")
    if args.min_stars is not None:
        q.append(f"stars:>={args.min_stars}")
    if args.pushed_since:
        ps_dt = parse_dt(args.pushed_since)
        if ps_dt:
            q.append(f"pushed:>={ps_dt.date().isoformat()}")
    if args.topic:
        q.append(f"topic:{args.topic}")
    if args.owner:
        q.append(f"user:{args.owner}")  # 或自改为 org:{args.owner}
    query = " ".join(q)

    rows: List[Dict[str, Any]] = []
    try:
        results = g.search_repositories(query=query, sort=args.sort, order=args.order)
        for i, repo in enumerate(results):
            if i >= args.limit:
                break
            rate_limit_guard(g)
            rows.append({
                "full_name": repo.full_name,
                "name": repo.name,
                "owner": repo.owner.login if repo.owner else "",
                "description": repo.description or "",
                "language": repo.language or "",
                "stargazers_count": repo.stargazers_count,
                "forks_count": repo.forks_count,
                "archived": repo.archived,
                "private": repo.private,
                "updated_at": str(getattr(repo, "updated_at", "")),
                "pushed_at": str(getattr(repo, "pushed_at", "")),
                "html_url": repo.html_url,
            })
    except GithubException as e:
        print(f"[warn] search error: {e}", file=sys.stderr)

    return rows


def _list_repos_for_org(g: Github, args: OrgReposArgs) -> List[Dict[str, Any]]:
    """组织仓库列表。"""
    rows: List[Dict[str, Any]] = []
    try:
        org = g.get_organization(args.org)
        repos = org.get_repos(type=args.repo_type, sort=args.sort)
        for i, repo in enumerate(repos):
            if i >= args.limit:
                break
            rate_limit_guard(g)
            if (not args.include_archived) and repo.archived:
                continue
            rows.append({
                "full_name": repo.full_name,
                "name": repo.name,
                "description": repo.description or "",
                "language": repo.language or "",
                "stargazers_count": repo.stargazers_count,
                "forks_count": repo.forks_count,
                "archived": repo.archived,
                "private": repo.private,
                "updated_at": str(getattr(repo, "updated_at", "")),
                "pushed_at": str(getattr(repo, "pushed_at", "")),
                "html_url": repo.html_url,
            })
    except GithubException as e:
        print(f"[warn] org list error: {e}", file=sys.stderr)

    return rows


def _list_user_repos(g: Github, args: UserReposArgs) -> List[Dict[str, Any]]:
    """
    列出某用户 owned / starred / both 的仓库列表，并可过滤、排序和限量。
    输出字段统一，增加 relation 标识来源。
    """
    rows: List[Dict[str, Any]] = []
    try:
        u = g.get_user(args.login)
    except GithubException as e:
        print(f"[warn] 无法找到用户 '{args.login}': {e}", file=sys.stderr)
        return []

    def add_repo(repo: Any, relation: str) -> bool:
        """添加仓库到结果列表（应用过滤条件）。返回 True 如果添加成功。"""
        # 过滤
        if (not args.include_archived) and repo.archived:
            return False
        if (not args.include_forks) and getattr(repo, "fork", False):
            return False
        if (not args.include_private) and repo.private:
            return False

        rows.append({
            "relation": relation,  # "owned" or "starred"
            "full_name": repo.full_name,
            "name": repo.name,
            "owner": repo.owner.login if repo.owner else "",
            "description": repo.description or "",
            "language": repo.language or "",
            "stargazers_count": repo.stargazers_count,
            "forks_count": repo.forks_count,
            "archived": repo.archived,
            "private": repo.private,
            "updated_at": str(getattr(repo, "updated_at", "")),
            "pushed_at": str(getattr(repo, "pushed_at", "")),
            "html_url": repo.html_url,
        })
        return True

    # owned
    if args.mode in ("owned", "both"):
        try:
            # 更激进的提前退出：只需要 limit + 一些缓冲即可
            max_collect = args.limit + 50 if args.mode == "both" else args.limit
            count = 0
            # PyGithub: user.get_repos(type="owner", sort="updated")
            for repo in u.get_repos(type="owner", sort="updated"):
                # 每10次检查一次速率限制，减少检查频率
                if count % 10 == 0:
                    pass #rate_limit_guard(g)
                count += 1
                try:
                    add_repo(repo, "owned")
                except Exception as e:
                    print(f"[warn] 跳过仓库 {getattr(repo, 'full_name', 'unknown')}: {e}", file=sys.stderr)
                    continue
                # 提前退出：如果已经收集足够多，立即停止
                if len(rows) >= max_collect:
                    break
        except GithubException as e:
            print(f"[warn] 获取 owned repos 失败: {e}", file=sys.stderr)

    # starred
    if args.mode in ("starred", "both"):
        try:
            # 更激进的提前退出：只需要 limit + 一些缓冲即可
            max_collect = args.limit + 50 if args.mode == "both" else args.limit
            # 设置最大处理数量，避免处理过多（如果用户 star 了上千个）
            max_process = min(args.limit * 3, 2000) if args.mode == "both" else args.limit * 2
            count = 0
            # Starred 没有服务端 sort，按 starred_at 顺序返回；我们本地再统一排序
            for repo in u.get_starred():
                # 每10次检查一次速率限制
                if count % 10 == 0:
                    pass #rate_limit_guard(g)
                count += 1
                try:
                    add_repo(repo, "starred")
                except Exception as e:
                    print(f"[warn] 跳过仓库 {getattr(repo, 'full_name', 'unknown')}: {e}", file=sys.stderr)
                    continue
                # 提前退出：如果已经收集足够多或处理太多，立即停止
                if len(rows) >= max_collect:
                    break
                if count >= max_process:
                    break
        except GithubException as e:
            print(f"[warn] 获取 starred repos 失败: {e}", file=sys.stderr)

    # 去重（如果同一个仓库同时出现在 owned 和 starred，保留 owned）
    # 使用字典记录索引，避免重复查找
    seen: Dict[str, int] = {}
    unique_rows: List[Dict[str, Any]] = []
    for r in rows:
        full_name = r["full_name"]
        if full_name not in seen:
            seen[full_name] = len(unique_rows)  # 记录索引位置
            unique_rows.append(r)
        elif unique_rows[seen[full_name]]["relation"] == "starred" and r["relation"] == "owned":
            # 如果已存在的是 starred，新的是 owned，替换（owned 优先级更高）
            unique_rows[seen[full_name]] = r

    rows = unique_rows

    # 统一排序（本地）
    key_map: Dict[str, Any] = {
        "updated": lambda r: r["updated_at"] or "",
        "pushed": lambda r: r["pushed_at"] or "",
        "full_name": lambda r: r["full_name"] or "",
        "stars": lambda r: r["stargazers_count"] or 0,
    }
    keyfunc = key_map.get(args.sort, key_map["updated"])
    rows.sort(key=keyfunc, reverse=(args.order == "desc"))

    # 限量（both 模式下合并后再截断）
    if len(rows) > args.limit:
        rows = rows[:args.limit]

    return rows


# ──────────────────────────────────────────────────────────────
# Gitee 实现函数
# ──────────────────────────────────────────────────────────────


def _fetch_user_activity_across_repos_gitee(
    client: Dict[str, Any], args: CrossReposArgs
) -> List[Dict[str, Any]]:
    """不同仓库同一作者（明细）- Gitee 版本。"""
    rows: List[Dict[str, Any]] = []
    base_url = client["base_url"]
    headers = client["headers"]

    since = parse_dt(args.since)
    until = parse_dt(args.until)

    # 枚举仓库列表
    owner = args.owner or args.author_login
    if not owner:
        raise ValueError("必须提供 owner 或 author_login")

    # 获取用户的仓库列表
    repos_url = f"{base_url}/users/{owner}/repos"
    page = 1
    repos = []
    while True:
        params = {"page": page, "per_page": 100, "type": args.repo_type}
        try:
            resp = requests.get(repos_url, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                break
            repos.extend(data)
            if len(data) < 100:
                break
            page += 1
        except Exception as e:
            print(f"[warn] 获取仓库列表失败: {e}", file=sys.stderr)
            break

    # 遍历仓库获取提交
    for repo_data in repos:
        full_name = repo_data.get("full_name", "")
        if not full_name:
            continue

        owner_name, repo_name = full_name.split("/", 1)
        commits_url = f"{base_url}/repos/{owner_name}/{repo_name}/commits"

        try:
            commit_page = 1
            cnt = 0
            while cnt < args.max_per_repo:
                params = {"page": commit_page, "per_page": 100}
                if since:
                    params["since"] = since.isoformat()
                if until:
                    params["until"] = until.isoformat()
                if args.author_login:
                    params["author"] = args.author_login

                resp = requests.get(commits_url, headers=headers, params=params, timeout=30)
                resp.raise_for_status()
                commits_data = resp.json()

                if not commits_data:
                    break

                for c in commits_data:
                    # 邮箱过滤
                    if args.author_email:
                        author_email = c.get("commit", {}).get("author", {}).get("email", "")
                        if author_email.lower() != args.author_email.lower():
                            continue

                    commit_date_str = c.get("commit", {}).get("author", {}).get("date", "")
                    if commit_date_str:
                        try:
                            commit_date = datetime.fromisoformat(commit_date_str.replace("Z", "+00:00"))
                            if commit_date.tzinfo is None:
                                commit_date = commit_date.replace(tzinfo=timezone.utc)

                            if since and commit_date < since:
                                continue
                            if until and commit_date > until:
                                continue
                        except Exception:
                            pass

                    rows.append({
                        "repo": full_name,
                        "sha": c.get("sha", "")[:40],
                        "date": commit_date_str,
                        "author_login": c.get("author", {}).get("login", "") if c.get("author") else "",
                        "author_name": c.get("commit", {}).get("author", {}).get("name", ""),
                        "author_email": c.get("commit", {}).get("author", {}).get("email", ""),
                        "committer_login": c.get("committer", {}).get("login", "") if c.get("committer") else "",
                        "title": (c.get("commit", {}).get("message", "") or "").splitlines()[0],
                        "url": c.get("html_url", ""),
                    })
                    cnt += 1
                    if cnt >= args.max_per_repo:
                        break

                if len(commits_data) < 100 or cnt >= args.max_per_repo:
                    break
                commit_page += 1

        except Exception as e:
            print(f"[warn] 跳过 {full_name}: {e}", file=sys.stderr)
            continue

    return rows


def _search_repos_by_keyword_gitee(
    client: Dict[str, Any], args: SearchReposArgs
) -> List[Dict[str, Any]]:
    """关键词检索仓库 - Gitee 版本。"""
    rows: List[Dict[str, Any]] = []
    base_url = client["base_url"]
    headers = client["headers"]

    # Gitee 搜索 API
    search_url = f"{base_url}/search/repositories"
    page = 1

    while len(rows) < args.limit:
        params = {
            "q": args.keyword,
            "page": page,
            "per_page": min(100, args.limit - len(rows)),
        }
        if args.language:
            params["language"] = args.language
        if args.min_stars:
            params["sort"] = "stars_count"
            params["order"] = "desc"
        
        # 使用 URL 参数方式添加认证
        params = _add_gitee_auth(params, client)

        try:
            resp = requests.get(search_url, headers=headers, params=params, timeout=30)
            
            # 检查 HTTP 状态码
            if resp.status_code == 401:
                token_error = _check_gitee_token_error(resp)
                if token_error:
                    raise ValueError(token_error)
                else:
                    raise ValueError("Gitee API 需要认证，请设置 GITEE_TOKEN 环境变量")
            elif resp.status_code != 200:
                resp.raise_for_status()
            
            data = resp.json()

            if not data or not isinstance(data, list):
                break

            for repo in data:
                # 过滤条件
                if args.min_stars and repo.get("stargazers_count", 0) < args.min_stars:
                    continue
                if args.owner and repo.get("owner", {}).get("login", "") != args.owner:
                    continue

                rows.append({
                    "full_name": repo.get("full_name", ""),
                    "name": repo.get("name", ""),
                    "owner": repo.get("owner", {}).get("login", "") if repo.get("owner") else "",
                    "description": repo.get("description", ""),
                    "language": repo.get("language", ""),
                    "stargazers_count": repo.get("stargazers_count", 0),
                    "forks_count": repo.get("forks_count", 0),
                    "archived": repo.get("archived", False),
                    "private": repo.get("private", False),
                    "updated_at": repo.get("updated_at", ""),
                    "pushed_at": repo.get("pushed_at", ""),
                    "html_url": repo.get("html_url", ""),
                })

                if len(rows) >= args.limit:
                    break

            if len(data) < 100:
                break
            page += 1

        except ValueError:
            # 认证错误等重要错误，重新抛出
            raise
        except requests.exceptions.HTTPError as e:
            # HTTP 错误应该抛出
            if e.response.status_code == 401:
                token_error = _check_gitee_token_error(e.response)
                if token_error:
                    raise ValueError(token_error)
                raise ValueError("Gitee API 需要认证，请设置 GITEE_TOKEN 环境变量")
            raise
        except Exception as e:
            # 其他错误也抛出，让顶层处理
            raise RuntimeError(f"搜索失败: {str(e)}") from e

    # 本地排序
    if args.sort == "stars":
        rows.sort(key=lambda r: r["stargazers_count"], reverse=(args.order == "desc"))
    elif args.sort == "updated":
        rows.sort(key=lambda r: r["updated_at"] or "", reverse=(args.order == "desc"))

    return rows[:args.limit]


def _list_repos_for_org_gitee(client: Dict[str, Any], args: OrgReposArgs) -> List[Dict[str, Any]]:
    """组织仓库列表 - Gitee 版本。"""
    rows: List[Dict[str, Any]] = []
    base_url = client["base_url"]
    headers = client["headers"]

    repos_url = f"{base_url}/orgs/{args.org}/repos"
    page = 1

    while len(rows) < args.limit:
        params = {
            "page": page,
            "per_page": min(100, args.limit - len(rows)),
        }
        # Gitee API: 如果有 token，可以使用 type 参数
        if client.get("token"):
            params["type"] = args.repo_type
        
        # 使用 URL 参数方式添加认证
        params = _add_gitee_auth(params, client)

        try:
            resp = requests.get(repos_url, headers=headers, params=params, timeout=30)
            
            # 检查 HTTP 状态码
            if resp.status_code == 401:
                token_error = _check_gitee_token_error(resp)
                if token_error:
                    raise ValueError(token_error)
                else:
                    raise ValueError("Gitee API 需要认证，请设置 GITEE_TOKEN 环境变量")
            elif resp.status_code != 200:
                resp.raise_for_status()
            
            data = resp.json()

            if not data:
                break

            for repo in data:
                if (not args.include_archived) and repo.get("archived", False):
                    continue

                rows.append({
                    "full_name": repo.get("full_name", ""),
                    "name": repo.get("name", ""),
                    "description": repo.get("description", ""),
                    "language": repo.get("language", ""),
                    "stargazers_count": repo.get("stargazers_count", 0),
                    "forks_count": repo.get("forks_count", 0),
                    "archived": repo.get("archived", False),
                    "private": repo.get("private", False),
                    "updated_at": repo.get("updated_at", ""),
                    "pushed_at": repo.get("pushed_at", ""),
                    "html_url": repo.get("html_url", ""),
                })

                if len(rows) >= args.limit:
                    break

            if len(data) < 100:
                break
            page += 1

        except ValueError:
            # 认证错误等重要错误，重新抛出
            raise
        except requests.exceptions.HTTPError as e:
            # HTTP 错误应该抛出
            if e.response.status_code == 401:
                token_error = _check_gitee_token_error(e.response)
                if token_error:
                    raise ValueError(token_error)
                raise ValueError("Gitee API 需要认证，请设置 GITEE_TOKEN 环境变量")
            raise
        except Exception as e:
            # 其他错误也抛出，让顶层处理
            raise RuntimeError(f"获取组织仓库失败: {str(e)}") from e

    return rows


def _fetch_repo_activity_across_authors_gitee(
    client: Dict[str, Any], args: RepoAuthorsArgs
) -> List[Dict[str, Any]]:
    """同一仓库不同作者（明细）- Gitee 版本。"""
    rows: List[Dict[str, Any]] = []
    base_url = client["base_url"]
    headers = client["headers"]

    since = parse_dt(args.since)
    until = parse_dt(args.until)

    owner, repo_name = args.repo_full.split("/", 1)
    commits_url = f"{base_url}/repos/{owner}/{repo_name}/commits"

    authors_login = list({a for a in (args.authors_login or []) if a})
    authors_emails = [e.lower() for e in (args.authors_emails or []) if e]

    # 无作者清单 = 拉时间窗内所有提交
    if not authors_login and not authors_emails:
        commit_page = 1
        while True:
            params = {"page": commit_page, "per_page": 100}
            if since:
                params["since"] = since.isoformat()
            if until:
                params["until"] = until.isoformat()

            try:
                resp = requests.get(commits_url, headers=headers, params=params, timeout=30)
                resp.raise_for_status()
                commits_data = resp.json()

                if not commits_data:
                    break

                for c in commits_data:
                    rows.append({
                        "repo": args.repo_full,
                        "sha": c.get("sha", "")[:40],
                        "date": c.get("commit", {}).get("author", {}).get("date", ""),
                        "author_login": c.get("author", {}).get("login", "") if c.get("author") else "",
                        "author_name": c.get("commit", {}).get("author", {}).get("name", ""),
                        "author_email": c.get("commit", {}).get("author", {}).get("email", ""),
                        "committer_login": c.get("committer", {}).get("login", "") if c.get("committer") else "",
                        "title": (c.get("commit", {}).get("message", "") or "").splitlines()[0],
                        "url": c.get("html_url", ""),
                    })

                if len(commits_data) < 100:
                    break
                commit_page += 1
            except ValueError:
                # 认证错误等重要错误，重新抛出
                raise
            except requests.exceptions.HTTPError as e:
                # HTTP 错误应该抛出
                if e.response.status_code == 401:
                    token_error = _check_gitee_token_error(e.response)
                    if token_error:
                        raise ValueError(token_error)
                    raise ValueError("Gitee API 需要认证，请设置 GITEE_TOKEN 环境变量")
                raise
            except Exception as e:
                # 其他错误也抛出，让顶层处理
                raise RuntimeError(f"获取提交失败: {str(e)}") from e
        return rows

    # 按 login 拉
    for login in authors_login:
        commit_page = 1
        cnt = 0
        while cnt < args.max_per_author:
            params = {"page": commit_page, "per_page": 100, "author": login}
            if since:
                params["since"] = since.isoformat()
            if until:
                params["until"] = until.isoformat()
            params = _add_gitee_auth(params, client)

            try:
                resp = requests.get(commits_url, headers=headers, params=params, timeout=30)
                if resp.status_code == 401:
                    token_error = _check_gitee_token_error(resp)
                    if token_error:
                        raise ValueError(token_error)
                if resp.status_code != 200:
                    resp.raise_for_status()
                commits_data = resp.json()
                # 处理不同的响应格式
                if isinstance(commits_data, dict):
                    if "message" in commits_data:
                        print(f"[warn] Gitee API 返回错误: {commits_data.get('message')}", file=sys.stderr)
                        break
                    if "data" in commits_data:
                        commits_data = commits_data["data"]
                    elif "items" in commits_data:
                        commits_data = commits_data["items"]
                    else:
                        commits_data = []

                if not commits_data:
                    break

                for c in commits_data:
                    if authors_emails:
                        author_email = c.get("commit", {}).get("author", {}).get("email", "")
                        if not author_email or author_email.lower() not in authors_emails:
                            continue

                    rows.append({
                        "repo": args.repo_full,
                        "sha": c.get("sha", "")[:40],
                        "date": c.get("commit", {}).get("author", {}).get("date", ""),
                        "author_login": c.get("author", {}).get("login", "") if c.get("author") else "",
                        "author_name": c.get("commit", {}).get("author", {}).get("name", ""),
                        "author_email": c.get("commit", {}).get("author", {}).get("email", ""),
                        "committer_login": c.get("committer", {}).get("login", "") if c.get("committer") else "",
                        "title": (c.get("commit", {}).get("message", "") or "").splitlines()[0],
                        "url": c.get("html_url", ""),
                    })
                    cnt += 1
                    if cnt >= args.max_per_author:
                        break

                if len(commits_data) < 100 or cnt >= args.max_per_author:
                    break
                commit_page += 1
            except ValueError:
                # 认证错误等重要错误，重新抛出
                raise
            except requests.exceptions.HTTPError as e:
                # HTTP 错误应该抛出
                if e.response.status_code == 401:
                    token_error = _check_gitee_token_error(e.response)
                    if token_error:
                        raise ValueError(token_error)
                    raise ValueError("Gitee API 需要认证，请设置 GITEE_TOKEN 环境变量")
                raise
            except Exception as e:
                # 其他错误也抛出，让顶层处理
                raise RuntimeError(f"获取 login {login} 的提交失败: {str(e)}") from e

    # 仅邮箱（补漏）
    if authors_emails:
        commit_page = 1
        cnt = 0
        while cnt < args.max_per_author:
            params = {"page": commit_page, "per_page": 100}
            if since:
                params["since"] = since.isoformat()
            if until:
                params["until"] = until.isoformat()

            try:
                resp = requests.get(commits_url, headers=headers, params=params, timeout=30)
                resp.raise_for_status()
                commits_data = resp.json()

                if not commits_data:
                    break

                for c in commits_data:
                    author_email = c.get("commit", {}).get("author", {}).get("email", "")
                    if not author_email or author_email.lower() not in authors_emails:
                        continue

                    rows.append({
                        "repo": args.repo_full,
                        "sha": c.get("sha", "")[:40],
                        "date": c.get("commit", {}).get("author", {}).get("date", ""),
                        "author_login": c.get("author", {}).get("login", "") if c.get("author") else "",
                        "author_name": c.get("commit", {}).get("author", {}).get("name", ""),
                        "author_email": author_email,
                        "committer_login": c.get("committer", {}).get("login", "") if c.get("committer") else "",
                        "title": (c.get("commit", {}).get("message", "") or "").splitlines()[0],
                        "url": c.get("html_url", ""),
                    })
                    cnt += 1
                    if cnt >= args.max_per_author:
                        break

                if len(commits_data) < 100 or cnt >= args.max_per_author:
                    break
                commit_page += 1
            except ValueError:
                # 认证错误等重要错误，重新抛出
                raise
            except requests.exceptions.HTTPError as e:
                # HTTP 错误应该抛出
                if e.response.status_code == 401:
                    token_error = _check_gitee_token_error(e.response)
                    if token_error:
                        raise ValueError(token_error)
                    raise ValueError("Gitee API 需要认证，请设置 GITEE_TOKEN 环境变量")
                raise
            except Exception as e:
                # 其他错误也抛出，让顶层处理
                raise RuntimeError(f"获取邮箱相关提交失败: {str(e)}") from e

    return rows


def _list_repos_for_author_gitee(
    client: Dict[str, Any], args: ReposByAuthorArgs
) -> List[Dict[str, Any]]:
    """同一作者在哪些仓库（列表）- Gitee 版本。"""
    results: List[Dict[str, Any]] = []
    counts: Dict[str, int] = defaultdict(int)
    base_url = client["base_url"]
    headers = client["headers"]

    owner = args.owner or args.author_login
    if not owner:
        raise ValueError("必须提供 owner 或 author_login")

    since = parse_dt(args.since)
    until = parse_dt(args.until)

    # 获取用户的仓库列表
    repos_url = f"{base_url}/users/{owner}/repos"
    page = 1
    repos = []
    while True:
        params = {"page": page, "per_page": 100, "type": args.repo_type}
        try:
            resp = requests.get(repos_url, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                break
            repos.extend(data)
            if len(data) < 100:
                break
            page += 1
        except ValueError:
            # 认证错误等重要错误，重新抛出
            raise
        except requests.exceptions.HTTPError as e:
            # HTTP 错误应该抛出
            if e.response.status_code == 401:
                token_error = _check_gitee_token_error(e.response)
                if token_error:
                    raise ValueError(token_error)
                raise ValueError("Gitee API 需要认证，请设置 GITEE_TOKEN 环境变量")
            raise
        except Exception as e:
            # 其他错误也抛出，让顶层处理
            raise RuntimeError(f"获取仓库列表失败: {str(e)}") from e

    # 遍历仓库统计提交
    for repo_data in repos:
        full_name = repo_data.get("full_name", "")
        if not full_name:
            continue

        owner_name, repo_name = full_name.split("/", 1)
        commits_url = f"{base_url}/repos/{owner_name}/{repo_name}/commits"

        try:
            commit_page = 1
            repo_count = 0
            while True:
                params = {"page": commit_page, "per_page": 100}
                if since:
                    params["since"] = since.isoformat()
                if until:
                    params["until"] = until.isoformat()
                if args.author_login:
                    params["author"] = args.author_login

                resp = requests.get(commits_url, headers=headers, params=params, timeout=30)
                resp.raise_for_status()
                commits_data = resp.json()

                if not commits_data:
                    break

                for c in commits_data:
                    if args.author_email:
                        author_email = c.get("commit", {}).get("author", {}).get("email", "")
                        if not author_email or author_email.lower() != args.author_email.lower():
                            continue
                    repo_count += 1

                if len(commits_data) < 100:
                    break
                commit_page += 1

            if repo_count >= args.min_commits:
                counts[full_name] = repo_count

        except Exception as e:
            print(f"[warn] 跳过 {full_name}: {e}", file=sys.stderr)
            continue

    for repo_full, cnt in sorted(counts.items(), key=lambda x: x[1], reverse=True):
        results.append({"repo": repo_full, "commits": cnt})

    return results


def _list_authors_for_repo_gitee(
    client: Dict[str, Any], args: AuthorsByRepoArgs
) -> List[Dict[str, Any]]:
    """同一仓库活跃作者（列表）- Gitee 版本。"""
    rows: List[Dict[str, Any]] = []
    base_url = client["base_url"]
    headers = client["headers"]

    owner, repo_name = args.repo_full.split("/", 1)
    commits_url = f"{base_url}/repos/{owner}/{repo_name}/commits"

    since = parse_dt(args.since)
    until = parse_dt(args.until)

    counter: Counter[str] = Counter()
    meta: Dict[str, Tuple[str, str]] = {}

    commit_page = 1
    while True:
        params = {"page": commit_page, "per_page": 100}
        if since:
            params["since"] = since.isoformat()
        if until:
            params["until"] = until.isoformat()
        params = _add_gitee_auth(params, client)

        try:
            resp = requests.get(commits_url, headers=headers, params=params, timeout=30)
            if resp.status_code == 401:
                token_error = _check_gitee_token_error(resp)
                if token_error:
                    raise ValueError(token_error)
            if resp.status_code != 200:
                resp.raise_for_status()
            commits_data = resp.json()
            # 处理不同的响应格式
            if isinstance(commits_data, dict):
                if "message" in commits_data:
                    print(f"[warn] Gitee API 返回错误: {commits_data.get('message')}", file=sys.stderr)
                    break
                if "data" in commits_data:
                    commits_data = commits_data["data"]
                elif "items" in commits_data:
                    commits_data = commits_data["items"]
                else:
                    commits_data = []

            if not commits_data:
                break

            for c in commits_data:
                login = c.get("author", {}).get("login", "") if c.get("author") else ""
                name = c.get("commit", {}).get("author", {}).get("name", "") or ""
                email = c.get("commit", {}).get("author", {}).get("email", "") or ""

                if args.prefer == "email" and email:
                    key = email.lower()
                elif args.prefer == "name" and name:
                    key = name
                else:
                    key = login or email.lower() or name or "(unknown)"

                counter[key] += 1
                meta.setdefault(key, (login, email))

            if len(commits_data) < 100:
                break
            commit_page += 1
        except ValueError:
            # 认证错误等重要错误，重新抛出
            raise
        except requests.exceptions.HTTPError as e:
            # HTTP 错误应该抛出
            if e.response.status_code == 401:
                token_error = _check_gitee_token_error(e.response)
                if token_error:
                    raise ValueError(token_error)
                raise ValueError("Gitee API 需要认证，请设置 GITEE_TOKEN 环境变量")
            raise
        except Exception as e:
            # 其他错误也抛出，让顶层处理
            raise RuntimeError(f"获取提交失败: {str(e)}") from e

    for key, cnt in counter.most_common():
        if cnt >= args.min_commits:
            login, email = meta.get(key, ("", ""))
            rows.append({
                "repo": args.repo_full,
                "author_key": key,
                "author_login": login,
                "author_email": email,
                "commits": cnt,
            })

    return rows


def _list_user_repos_gitee(client: Dict[str, Any], args: UserReposArgs) -> List[Dict[str, Any]]:
    """列出某用户 owned / starred / both 的仓库列表 - Gitee 版本。"""
    rows: List[Dict[str, Any]] = []
    base_url = client["base_url"]
    headers = client["headers"]

    def add_repo(repo_data: Dict[str, Any], relation: str) -> bool:
        """添加仓库到结果列表（应用过滤条件）。"""
        # Gitee API 可能返回 None，需要处理
        archived = repo_data.get("archived") or False
        is_fork = repo_data.get("fork") or False
        is_private = repo_data.get("private") or False
        
        if (not args.include_archived) and archived:
            return False
        if (not args.include_forks) and is_fork:
            return False
        if (not args.include_private) and is_private:
            return False

        # 处理 html_url，移除 .git 后缀（如果有）
        html_url = repo_data.get("html_url", "")
        if html_url.endswith(".git"):
            html_url = html_url[:-4]

        rows.append({
            "relation": relation,
            "full_name": repo_data.get("full_name", ""),
            "name": repo_data.get("name", ""),
            "owner": repo_data.get("owner", {}).get("login", "") if repo_data.get("owner") else "",
            "description": repo_data.get("description", "") or "",
            "language": repo_data.get("language", "") or "",
            "stargazers_count": repo_data.get("stargazers_count", 0) or 0,
            "forks_count": repo_data.get("forks_count", 0) or 0,
            "archived": archived,
            "private": is_private,
            "updated_at": repo_data.get("updated_at", "") or "",
            "pushed_at": repo_data.get("pushed_at", "") or "",
            "html_url": html_url,
        })
        return True

    # owned
    if args.mode in ("owned", "both"):
        try:
            repos_url = f"{base_url}/users/{args.login}/repos"
            page = 1
            max_collect = args.limit + 50 if args.mode == "both" else args.limit

            while len(rows) < max_collect:
                # Gitee API: 不带 type 参数时返回公开仓库，需要认证
                # 如果有 token，可以使用 type=owner/all 获取更多仓库
                params = {"page": page, "per_page": 100}
                if client.get("token"):
                    # 有 token 时，根据 include_private 决定 type
                    if args.include_private:
                        params["type"] = "all"
                    else:
                        params["type"] = "owner"
                # 使用 URL 参数方式添加认证（兼容性更好）
                params = _add_gitee_auth(params, client)
                
                resp = requests.get(repos_url, headers=headers, params=params, timeout=30)
                
                # 检查 HTTP 状态码
                if resp.status_code == 401:
                    # 需要认证或 token 无效
                    token_error = _check_gitee_token_error(resp)
                    if token_error:
                        raise ValueError(token_error)
                    else:
                        raise ValueError("Gitee API 需要认证，请设置 GITEE_TOKEN 环境变量")
                elif resp.status_code == 404:
                    # 用户不存在
                    print(f"[warn] 用户 '{args.login}' 不存在或无法访问", file=sys.stderr)
                    break
                elif resp.status_code != 200:
                    resp.raise_for_status()
                
                data = resp.json()
                
                # Gitee API 可能返回不同的格式
                if isinstance(data, dict):
                    # 如果返回的是字典，可能包含错误信息或不同的数据结构
                    if "message" in data:
                        print(f"[warn] Gitee API 返回错误: {data.get('message')}", file=sys.stderr)
                        break
                    # 可能数据在某个字段中
                    if "data" in data:
                        data = data["data"]
                    elif "items" in data:
                        data = data["items"]
                    else:
                        data = []

                if not data or not isinstance(data, list):
                    break

                for repo in data:
                    add_repo(repo, "owned")
                    if len(rows) >= max_collect:
                        break

                if len(data) < 100 or len(rows) >= max_collect:
                    break
                page += 1
        except ValueError:
            # 认证错误等重要错误，重新抛出
            raise
        except requests.exceptions.HTTPError as e:
            # HTTP 错误：404 可以继续（用户不存在），其他错误应该抛出
            if e.response.status_code == 404:
                print(f"[warn] 用户 '{args.login}' 可能不存在或无法访问", file=sys.stderr)
            elif e.response.status_code == 401:
                # 401 应该已经被上面的代码处理并抛出 ValueError，但以防万一
                token_error = _check_gitee_token_error(e.response) if hasattr(e, 'response') else None
                if token_error:
                    raise ValueError(token_error)
                raise ValueError("Gitee API 需要认证，请设置 GITEE_TOKEN 环境变量")
            else:
                # 其他 HTTP 错误应该抛出
                raise
        except Exception as e:
            # 其他未知错误也抛出，让顶层处理
            raise RuntimeError(f"获取 owned repos 失败: {str(e)}") from e

    # starred
    if args.mode in ("starred", "both"):
        try:
            starred_url = f"{base_url}/users/{args.login}/starred"
            page = 1
            max_collect = args.limit + 50 if args.mode == "both" else args.limit

            while len(rows) < max_collect:
                params = {"page": page, "per_page": 100}
                # 使用 URL 参数方式添加认证
                params = _add_gitee_auth(params, client)
                resp = requests.get(starred_url, headers=headers, params=params, timeout=30)
                
                # 检查 HTTP 状态码
                if resp.status_code == 401:
                    # 需要认证或 token 无效
                    token_error = _check_gitee_token_error(resp)
                    if token_error:
                        raise ValueError(token_error)
                    else:
                        raise ValueError("Gitee API 需要认证，请设置 GITEE_TOKEN 环境变量")
                elif resp.status_code == 404:
                    print(f"[warn] 用户 '{args.login}' 的 starred repos 无法访问", file=sys.stderr)
                    break
                elif resp.status_code != 200:
                    resp.raise_for_status()
                
                data = resp.json()
                
                # Gitee API 可能返回不同的格式
                if isinstance(data, dict):
                    if "message" in data:
                        print(f"[warn] Gitee API 返回错误: {data.get('message')}", file=sys.stderr)
                        break
                    if "data" in data:
                        data = data["data"]
                    elif "items" in data:
                        data = data["items"]
                    else:
                        data = []

                if not data or not isinstance(data, list):
                    break

                for repo in data:
                    add_repo(repo, "starred")
                    if len(rows) >= max_collect:
                        break

                if len(data) < 100 or len(rows) >= max_collect:
                    break
                page += 1
        except ValueError:
            # 认证错误等重要错误，重新抛出
            raise
        except requests.exceptions.HTTPError as e:
            # HTTP 错误：404 可以继续（资源不存在），其他错误应该抛出
            if e.response.status_code == 404:
                print(f"[warn] 用户 '{args.login}' 的 starred repos 无法访问", file=sys.stderr)
            elif e.response.status_code == 401:
                # 401 应该已经被上面的代码处理并抛出 ValueError，但以防万一
                token_error = _check_gitee_token_error(e.response) if hasattr(e, 'response') else None
                if token_error:
                    raise ValueError(token_error)
                raise ValueError("Gitee API 需要认证，请设置 GITEE_TOKEN 环境变量")
            else:
                # 其他 HTTP 错误应该抛出
                raise
        except Exception as e:
            # 其他未知错误也抛出，让顶层处理
            raise RuntimeError(f"获取 starred repos 失败: {str(e)}") from e

    # 去重
    seen: Dict[str, int] = {}
    unique_rows: List[Dict[str, Any]] = []
    for r in rows:
        full_name = r["full_name"]
        if full_name not in seen:
            seen[full_name] = len(unique_rows)
            unique_rows.append(r)
        elif unique_rows[seen[full_name]]["relation"] == "starred" and r["relation"] == "owned":
            unique_rows[seen[full_name]] = r

    rows = unique_rows

    # 统一排序
    key_map: Dict[str, Any] = {
        "updated": lambda r: r["updated_at"] or "",
        "pushed": lambda r: r["pushed_at"] or "",
        "full_name": lambda r: r["full_name"] or "",
        "stars": lambda r: r["stargazers_count"] or 0,
    }
    keyfunc = key_map.get(args.sort, key_map["updated"])
    rows.sort(key=keyfunc, reverse=(args.order == "desc"))

    # 限量
    if len(rows) > args.limit:
        rows = rows[:args.limit]

    return rows


# ──────────────────────────────────────────────────────────────
# GitLab 实现函数
# ──────────────────────────────────────────────────────────────


def _encode_gitlab_path(path: str) -> str:
    """对 GitLab 路径进行 URL 编码（将 / 编码为 %2F）。"""
    import urllib.parse
    return urllib.parse.quote(path, safe="")


def _fetch_user_activity_across_repos_gitlab(
    client: Dict[str, Any], args: CrossReposArgs
) -> List[Dict[str, Any]]:
    """不同仓库同一作者（明细）- GitLab 版本。"""
    rows: List[Dict[str, Any]] = []
    base_url = client["base_url"]
    headers = client["headers"]

    since = parse_dt(args.since)
    until = parse_dt(args.until)

    # 枚举仓库列表
    owner = args.owner or args.author_login
    if not owner:
        raise ValueError("必须提供 owner 或 author_login")

    # 获取用户的仓库列表
    # GitLab: 使用 /projects?owned=true&username=username 或 /users/:id/projects
    projects_url = f"{base_url}/projects"
    page = 1
    repos = []
    while True:
        params = {"page": page, "per_page": 100, "owned": "true", "username": owner}
        try:
            resp = requests.get(projects_url, headers=headers, params=params, timeout=30)
            if resp.status_code == 401:
                token_error = _check_gitlab_token_error(resp)
                if token_error:
                    raise ValueError(token_error)
                raise ValueError("GitLab API 需要认证，请设置 GITLAB_TOKEN 环境变量")
            if resp.status_code != 200:
                resp.raise_for_status()
            data = resp.json()
            if not data or not isinstance(data, list):
                break
            repos.extend(data)
            if len(data) < 100:
                break
            page += 1
        except ValueError:
            raise
        except Exception as e:
            print(f"[warn] 获取仓库列表失败: {e}", file=sys.stderr)
            break

    # 遍历仓库获取提交
    for repo_data in repos:
        repo_path = repo_data.get("path_with_namespace", "")
        if not repo_path:
            continue

        # GitLab 项目 ID 可以是数字或路径（需要 URL 编码）
        repo_id = _encode_gitlab_path(repo_path)
        commits_url = f"{base_url}/projects/{repo_id}/repository/commits"

        try:
            commit_page = 1
            cnt = 0
            while cnt < args.max_per_repo:
                params = {"page": commit_page, "per_page": 100}
                if since:
                    params["since"] = since.isoformat()
                if until:
                    params["until"] = until.isoformat()
                if args.author_login:
                    params["author"] = args.author_login

                resp = requests.get(commits_url, headers=headers, params=params, timeout=30)
                if resp.status_code == 401:
                    token_error = _check_gitlab_token_error(resp)
                    if token_error:
                        raise ValueError(token_error)
                if resp.status_code != 200:
                    resp.raise_for_status()
                commits_data = resp.json()

                if not commits_data or not isinstance(commits_data, list):
                    break

                for c in commits_data:
                    # 邮箱过滤
                    if args.author_email:
                        author_email = c.get("author_email", "")
                        if author_email.lower() != args.author_email.lower():
                            continue

                    commit_date_str = c.get("created_at", "")
                    if commit_date_str:
                        try:
                            commit_date = datetime.fromisoformat(commit_date_str.replace("Z", "+00:00"))
                            if commit_date.tzinfo is None:
                                commit_date = commit_date.replace(tzinfo=timezone.utc)

                            if since and commit_date < since:
                                continue
                            if until and commit_date > until:
                                continue
                        except Exception:
                            pass

                    rows.append({
                        "repo": repo_path,
                        "sha": c.get("id", "")[:40],
                        "date": commit_date_str,
                        "author_login": c.get("author_name", ""),  # GitLab 使用 author_name
                        "author_name": c.get("author_name", ""),
                        "author_email": c.get("author_email", ""),
                        "committer_login": c.get("committer_name", ""),
                        "title": (c.get("message", "") or "").splitlines()[0],
                        "url": c.get("web_url", ""),
                    })
                    cnt += 1
                    if cnt >= args.max_per_repo:
                        break

                if len(commits_data) < 100 or cnt >= args.max_per_repo:
                    break
                commit_page += 1

        except ValueError:
            raise
        except Exception as e:
            print(f"[warn] 跳过 {repo_path}: {e}", file=sys.stderr)
            continue

    return rows


def _fetch_repo_activity_across_authors_gitlab(
    client: Dict[str, Any], args: RepoAuthorsArgs
) -> List[Dict[str, Any]]:
    """同一仓库不同作者（明细）- GitLab 版本。"""
    rows: List[Dict[str, Any]] = []
    base_url = client["base_url"]
    headers = client["headers"]

    since = parse_dt(args.since)
    until = parse_dt(args.until)

    # GitLab 项目 ID 可以是数字或路径（需要 URL 编码）
    repo_id = _encode_gitlab_path(args.repo_full)
    commits_url = f"{base_url}/projects/{repo_id}/repository/commits"

    authors_login = list({a for a in (args.authors_login or []) if a})
    authors_emails = [e.lower() for e in (args.authors_emails or []) if e]

    # 无作者清单 = 拉时间窗内所有提交
    if not authors_login and not authors_emails:
        commit_page = 1
        while True:
            params = {"page": commit_page, "per_page": 100}
            if since:
                params["since"] = since.isoformat()
            if until:
                params["until"] = until.isoformat()

            try:
                resp = requests.get(commits_url, headers=headers, params=params, timeout=30)
                if resp.status_code == 401:
                    token_error = _check_gitlab_token_error(resp)
                    if token_error:
                        raise ValueError(token_error)
                if resp.status_code != 200:
                    resp.raise_for_status()
                commits_data = resp.json()

                if not commits_data or not isinstance(commits_data, list):
                    break

                for c in commits_data:
                    rows.append({
                        "repo": args.repo_full,
                        "sha": c.get("id", "")[:40],
                        "date": c.get("created_at", ""),
                        "author_login": c.get("author_name", ""),
                        "author_name": c.get("author_name", ""),
                        "author_email": c.get("author_email", ""),
                        "committer_login": c.get("committer_name", ""),
                        "title": (c.get("message", "") or "").splitlines()[0],
                        "url": c.get("web_url", ""),
                    })

                if len(commits_data) < 100:
                    break
                commit_page += 1
            except ValueError:
                raise
            except Exception as e:
                print(f"[warn] 获取提交失败: {e}", file=sys.stderr)
                break
        return rows

    # 按 author 参数拉（GitLab 支持 author 参数）
    for login in authors_login:
        commit_page = 1
        cnt = 0
        while cnt < args.max_per_author:
            params = {"page": commit_page, "per_page": 100, "author": login}
            if since:
                params["since"] = since.isoformat()
            if until:
                params["until"] = until.isoformat()

            try:
                resp = requests.get(commits_url, headers=headers, params=params, timeout=30)
                if resp.status_code == 401:
                    token_error = _check_gitlab_token_error(resp)
                    if token_error:
                        raise ValueError(token_error)
                if resp.status_code != 200:
                    resp.raise_for_status()
                commits_data = resp.json()

                if not commits_data or not isinstance(commits_data, list):
                    break

                for c in commits_data:
                    if authors_emails:
                        author_email = c.get("author_email", "")
                        if not author_email or author_email.lower() not in authors_emails:
                            continue

                    rows.append({
                        "repo": args.repo_full,
                        "sha": c.get("id", "")[:40],
                        "date": c.get("created_at", ""),
                        "author_login": c.get("author_name", ""),
                        "author_name": c.get("author_name", ""),
                        "author_email": c.get("author_email", ""),
                        "committer_login": c.get("committer_name", ""),
                        "title": (c.get("message", "") or "").splitlines()[0],
                        "url": c.get("web_url", ""),
                    })
                    cnt += 1
                    if cnt >= args.max_per_author:
                        break

                if len(commits_data) < 100 or cnt >= args.max_per_author:
                    break
                commit_page += 1
            except ValueError:
                raise
            except Exception as e:
                print(f"[warn] login {login}: {e}", file=sys.stderr)
                break

    # 仅邮箱（补漏）
    if authors_emails:
        commit_page = 1
        cnt = 0
        while cnt < args.max_per_author:
            params = {"page": commit_page, "per_page": 100}
            if since:
                params["since"] = since.isoformat()
            if until:
                params["until"] = until.isoformat()

            try:
                resp = requests.get(commits_url, headers=headers, params=params, timeout=30)
                if resp.status_code == 401:
                    token_error = _check_gitlab_token_error(resp)
                    if token_error:
                        raise ValueError(token_error)
                if resp.status_code != 200:
                    resp.raise_for_status()
                commits_data = resp.json()

                if not commits_data or not isinstance(commits_data, list):
                    break

                for c in commits_data:
                    author_email = c.get("author_email", "")
                    if not author_email or author_email.lower() not in authors_emails:
                        continue

                    rows.append({
                        "repo": args.repo_full,
                        "sha": c.get("id", "")[:40],
                        "date": c.get("created_at", ""),
                        "author_login": c.get("author_name", ""),
                        "author_name": c.get("author_name", ""),
                        "author_email": author_email,
                        "committer_login": c.get("committer_name", ""),
                        "title": (c.get("message", "") or "").splitlines()[0],
                        "url": c.get("web_url", ""),
                    })
                    cnt += 1
                    if cnt >= args.max_per_author:
                        break

                if len(commits_data) < 100 or cnt >= args.max_per_author:
                    break
                commit_page += 1
            except ValueError:
                raise
            except Exception as e:
                print(f"[warn] email pass: {e}", file=sys.stderr)
                break

    return rows


def _list_repos_for_author_gitlab(
    client: Dict[str, Any], args: ReposByAuthorArgs
) -> List[Dict[str, Any]]:
    """同一作者在哪些仓库（列表）- GitLab 版本。"""
    results: List[Dict[str, Any]] = []
    counts: Dict[str, int] = defaultdict(int)
    base_url = client["base_url"]
    headers = client["headers"]

    owner = args.owner or args.author_login
    if not owner:
        raise ValueError("必须提供 owner 或 author_login")

    since = parse_dt(args.since)
    until = parse_dt(args.until)

    # 获取用户的仓库列表
    projects_url = f"{base_url}/projects"
    page = 1
    repos = []
    while True:
        params = {"page": page, "per_page": 100, "owned": "true", "username": owner}
        try:
            resp = requests.get(projects_url, headers=headers, params=params, timeout=30)
            if resp.status_code == 401:
                token_error = _check_gitlab_token_error(resp)
                if token_error:
                    raise ValueError(token_error)
                raise ValueError("GitLab API 需要认证，请设置 GITLAB_TOKEN 环境变量")
            if resp.status_code != 200:
                resp.raise_for_status()
            data = resp.json()
            if not data or not isinstance(data, list):
                break
            repos.extend(data)
            if len(data) < 100:
                break
            page += 1
        except ValueError:
            raise
        except Exception as e:
            print(f"[warn] 获取仓库列表失败: {e}", file=sys.stderr)
            break

    # 遍历仓库统计提交
    for repo_data in repos:
        repo_path = repo_data.get("path_with_namespace", "")
        if not repo_path:
            continue

        repo_id = _encode_gitlab_path(repo_path)
        commits_url = f"{base_url}/projects/{repo_id}/repository/commits"

        try:
            commit_page = 1
            repo_count = 0
            while True:
                params = {"page": commit_page, "per_page": 100}
                if since:
                    params["since"] = since.isoformat()
                if until:
                    params["until"] = until.isoformat()
                if args.author_login:
                    params["author"] = args.author_login

                resp = requests.get(commits_url, headers=headers, params=params, timeout=30)
                if resp.status_code == 401:
                    token_error = _check_gitlab_token_error(resp)
                    if token_error:
                        raise ValueError(token_error)
                if resp.status_code != 200:
                    resp.raise_for_status()
                commits_data = resp.json()

                if not commits_data or not isinstance(commits_data, list):
                    break

                for c in commits_data:
                    if args.author_email:
                        author_email = c.get("author_email", "")
                        if not author_email or author_email.lower() != args.author_email.lower():
                            continue
                    repo_count += 1

                if len(commits_data) < 100:
                    break
                commit_page += 1

            if repo_count >= args.min_commits:
                counts[repo_path] = repo_count

        except ValueError:
            raise
        except Exception as e:
            print(f"[warn] 跳过 {repo_path}: {e}", file=sys.stderr)
            continue

    for repo_full, cnt in sorted(counts.items(), key=lambda x: x[1], reverse=True):
        results.append({"repo": repo_full, "commits": cnt})

    return results


def _list_authors_for_repo_gitlab(
    client: Dict[str, Any], args: AuthorsByRepoArgs
) -> List[Dict[str, Any]]:
    """同一仓库活跃作者（列表）- GitLab 版本。"""
    rows: List[Dict[str, Any]] = []
    base_url = client["base_url"]
    headers = client["headers"]

    repo_id = _encode_gitlab_path(args.repo_full)
    commits_url = f"{base_url}/projects/{repo_id}/repository/commits"

    since = parse_dt(args.since)
    until = parse_dt(args.until)

    counter: Counter[str] = Counter()
    meta: Dict[str, Tuple[str, str]] = {}

    commit_page = 1
    while True:
        params = {"page": commit_page, "per_page": 100}
        if since:
            params["since"] = since.isoformat()
        if until:
            params["until"] = until.isoformat()

        try:
            resp = requests.get(commits_url, headers=headers, params=params, timeout=30)
            if resp.status_code == 401:
                token_error = _check_gitlab_token_error(resp)
                if token_error:
                    raise ValueError(token_error)
            if resp.status_code != 200:
                resp.raise_for_status()
            commits_data = resp.json()

            if not commits_data or not isinstance(commits_data, list):
                break

            for c in commits_data:
                login = c.get("author_name", "") or ""
                name = c.get("author_name", "") or ""
                email = c.get("author_email", "") or ""

                if args.prefer == "email" and email:
                    key = email.lower()
                elif args.prefer == "name" and name:
                    key = name
                else:
                    key = login or email.lower() or name or "(unknown)"

                counter[key] += 1
                meta.setdefault(key, (login, email))

            if len(commits_data) < 100:
                break
            commit_page += 1
        except ValueError:
            raise
        except Exception as e:
            print(f"[warn] 获取提交失败: {e}", file=sys.stderr)
            break

    for key, cnt in counter.most_common():
        if cnt >= args.min_commits:
            login, email = meta.get(key, ("", ""))
            rows.append({
                "repo": args.repo_full,
                "author_key": key,
                "author_login": login,
                "author_email": email,
                "commits": cnt,
            })

    return rows


def _search_repos_by_keyword_gitlab(
    client: Dict[str, Any], args: SearchReposArgs
) -> List[Dict[str, Any]]:
    """关键词检索仓库 - GitLab 版本。"""
    rows: List[Dict[str, Any]] = []
    base_url = client["base_url"]
    headers = client["headers"]

    # GitLab 搜索 API
    search_url = f"{base_url}/projects"
    page = 1

    while len(rows) < args.limit:
        params = {
            "search": args.keyword,
            "page": page,
            "per_page": min(100, args.limit - len(rows)),
        }
        if args.owner:
            params["username"] = args.owner

        try:
            resp = requests.get(search_url, headers=headers, params=params, timeout=30)
            
            if resp.status_code == 401:
                token_error = _check_gitlab_token_error(resp)
                if token_error:
                    raise ValueError(token_error)
                raise ValueError("GitLab API 需要认证，请设置 GITLAB_TOKEN 环境变量")
            elif resp.status_code != 200:
                resp.raise_for_status()
            
            data = resp.json()

            if not data or not isinstance(data, list):
                break

            for repo in data:
                # 过滤条件
                if args.min_stars and repo.get("star_count", 0) < args.min_stars:
                    continue
                if args.owner and repo.get("owner", {}).get("username", "") != args.owner:
                    continue
                if args.language and repo.get("default_branch"):
                    # GitLab 没有直接的语言字段，可以通过仓库内容判断（这里简化处理）
                    pass

                rows.append({
                    "full_name": repo.get("path_with_namespace", ""),
                    "name": repo.get("name", ""),
                    "owner": repo.get("namespace", {}).get("path", "") if repo.get("namespace") else "",
                    "description": repo.get("description", "") or "",
                    "language": repo.get("default_branch", "") or "",  # GitLab 没有直接的语言字段
                    "stargazers_count": repo.get("star_count", 0),
                    "forks_count": repo.get("forks_count", 0),
                    "archived": repo.get("archived", False),
                    "private": repo.get("visibility", "private") != "public",
                    "updated_at": repo.get("last_activity_at", "") or repo.get("updated_at", ""),
                    "pushed_at": repo.get("last_activity_at", "") or "",
                    "html_url": repo.get("web_url", ""),
                })

                if len(rows) >= args.limit:
                    break

            if len(data) < 100:
                break
            page += 1

        except ValueError:
            raise
        except Exception as e:
            print(f"[warn] 搜索失败: {e}", file=sys.stderr)
            break

    # 本地排序
    if args.sort == "stars":
        rows.sort(key=lambda r: r["stargazers_count"], reverse=(args.order == "desc"))
    elif args.sort == "updated":
        rows.sort(key=lambda r: r["updated_at"] or "", reverse=(args.order == "desc"))

    return rows[:args.limit]


def _list_repos_for_org_gitlab(client: Dict[str, Any], args: OrgReposArgs) -> List[Dict[str, Any]]:
    """组织仓库列表 - GitLab 版本。"""
    rows: List[Dict[str, Any]] = []
    base_url = client["base_url"]
    headers = client["headers"]

    # GitLab 使用 groups 而不是 orgs
    # 组 ID 可以是数字或路径（需要 URL 编码）
    group_id = _encode_gitlab_path(args.org)
    repos_url = f"{base_url}/groups/{group_id}/projects"
    page = 1

    while len(rows) < args.limit:
        params = {
            "page": page,
            "per_page": min(100, args.limit - len(rows)),
        }
        # GitLab 的 repo_type 映射：all/public/private
        if args.repo_type == "public":
            params["visibility"] = "public"
        elif args.repo_type == "private":
            params["visibility"] = "private"

        try:
            resp = requests.get(repos_url, headers=headers, params=params, timeout=30)
            
            if resp.status_code == 401:
                token_error = _check_gitlab_token_error(resp)
                if token_error:
                    raise ValueError(token_error)
                raise ValueError("GitLab API 需要认证，请设置 GITLAB_TOKEN 环境变量")
            elif resp.status_code != 200:
                resp.raise_for_status()
            
            data = resp.json()

            if not data or not isinstance(data, list):
                break

            for repo in data:
                if (not args.include_archived) and repo.get("archived", False):
                    continue

                rows.append({
                    "full_name": repo.get("path_with_namespace", ""),
                    "name": repo.get("name", ""),
                    "description": repo.get("description", "") or "",
                    "language": repo.get("default_branch", "") or "",
                    "stargazers_count": repo.get("star_count", 0),
                    "forks_count": repo.get("forks_count", 0),
                    "archived": repo.get("archived", False),
                    "private": repo.get("visibility", "private") != "public",
                    "updated_at": repo.get("last_activity_at", "") or repo.get("updated_at", ""),
                    "pushed_at": repo.get("last_activity_at", "") or "",
                    "html_url": repo.get("web_url", ""),
                })

                if len(rows) >= args.limit:
                    break

            if len(data) < 100:
                break
            page += 1

        except ValueError:
            raise
        except Exception as e:
            print(f"[warn] 获取组织仓库失败: {e}", file=sys.stderr)
            break

    return rows


def _list_user_repos_gitlab(client: Dict[str, Any], args: UserReposArgs) -> List[Dict[str, Any]]:
    """列出某用户 owned / starred / both 的仓库列表 - GitLab 版本。"""
    rows: List[Dict[str, Any]] = []
    base_url = client["base_url"]
    headers = client["headers"]

    def add_repo(repo_data: Dict[str, Any], relation: str) -> bool:
        """添加仓库到结果列表（应用过滤条件）。"""
        archived = repo_data.get("archived") or False
        is_fork = repo_data.get("forked_from_project") is not None
        is_private = repo_data.get("visibility", "private") != "public"
        
        if (not args.include_archived) and archived:
            return False
        if (not args.include_forks) and is_fork:
            return False
        if (not args.include_private) and is_private:
            return False

        rows.append({
            "relation": relation,
            "full_name": repo_data.get("path_with_namespace", ""),
            "name": repo_data.get("name", ""),
            "owner": repo_data.get("namespace", {}).get("path", "") if repo_data.get("namespace") else "",
            "description": repo_data.get("description", "") or "",
            "language": repo_data.get("default_branch", "") or "",
            "stargazers_count": repo_data.get("star_count", 0) or 0,
            "forks_count": repo_data.get("forks_count", 0) or 0,
            "archived": archived,
            "private": is_private,
            "updated_at": repo_data.get("last_activity_at", "") or repo_data.get("updated_at", "") or "",
            "pushed_at": repo_data.get("last_activity_at", "") or "",
            "html_url": repo_data.get("web_url", ""),
        })
        return True

    # owned
    if args.mode in ("owned", "both"):
        try:
            projects_url = f"{base_url}/projects"
            page = 1
            max_collect = args.limit + 50 if args.mode == "both" else args.limit

            while len(rows) < max_collect:
                params = {"page": page, "per_page": 100, "owned": "true", "username": args.login}
                if not args.include_private:
                    params["visibility"] = "public"

                resp = requests.get(projects_url, headers=headers, params=params, timeout=30)
                
                if resp.status_code == 401:
                    token_error = _check_gitlab_token_error(resp)
                    if token_error:
                        raise ValueError(token_error)
                    raise ValueError("GitLab API 需要认证，请设置 GITLAB_TOKEN 环境变量")
                elif resp.status_code == 404:
                    print(f"[warn] 用户 '{args.login}' 不存在或无法访问", file=sys.stderr)
                    break
                elif resp.status_code != 200:
                    resp.raise_for_status()
                
                data = resp.json()
                
                if not data or not isinstance(data, list):
                    break

                for repo in data:
                    add_repo(repo, "owned")
                    if len(rows) >= max_collect:
                        break

                if len(data) < 100 or len(rows) >= max_collect:
                    break
                page += 1
        except ValueError:
            raise
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                print(f"[warn] 用户 '{args.login}' 可能不存在或无法访问", file=sys.stderr)
            elif e.response.status_code == 401:
                token_error = _check_gitlab_token_error(e.response) if hasattr(e, 'response') else None
                if token_error:
                    raise ValueError(token_error)
                raise ValueError("GitLab API 需要认证，请设置 GITLAB_TOKEN 环境变量")
            else:
                raise
        except Exception as e:
            raise RuntimeError(f"获取 owned repos 失败: {str(e)}") from e

    # starred
    if args.mode in ("starred", "both"):
        try:
            projects_url = f"{base_url}/projects"
            page = 1
            max_collect = args.limit + 50 if args.mode == "both" else args.limit

            while len(rows) < max_collect:
                params = {"page": page, "per_page": 100, "starred": "true"}
                if not args.include_private:
                    params["visibility"] = "public"

                resp = requests.get(projects_url, headers=headers, params=params, timeout=30)
                
                if resp.status_code == 401:
                    token_error = _check_gitlab_token_error(resp)
                    if token_error:
                        raise ValueError(token_error)
                    raise ValueError("GitLab API 需要认证，请设置 GITLAB_TOKEN 环境变量")
                elif resp.status_code == 404:
                    print(f"[warn] 用户 '{args.login}' 的 starred repos 无法访问", file=sys.stderr)
                    break
                elif resp.status_code != 200:
                    resp.raise_for_status()
                
                data = resp.json()
                
                if not data or not isinstance(data, list):
                    break

                for repo in data:
                    add_repo(repo, "starred")
                    if len(rows) >= max_collect:
                        break

                if len(data) < 100 or len(rows) >= max_collect:
                    break
                page += 1
        except ValueError:
            raise
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                print(f"[warn] 用户 '{args.login}' 的 starred repos 无法访问", file=sys.stderr)
            elif e.response.status_code == 401:
                token_error = _check_gitlab_token_error(e.response) if hasattr(e, 'response') else None
                if token_error:
                    raise ValueError(token_error)
                raise ValueError("GitLab API 需要认证，请设置 GITLAB_TOKEN 环境变量")
            else:
                raise
        except Exception as e:
            raise RuntimeError(f"获取 starred repos 失败: {str(e)}") from e

    # 去重
    seen: Dict[str, int] = {}
    unique_rows: List[Dict[str, Any]] = []
    for r in rows:
        full_name = r["full_name"]
        if full_name not in seen:
            seen[full_name] = len(unique_rows)
            unique_rows.append(r)
        elif unique_rows[seen[full_name]]["relation"] == "starred" and r["relation"] == "owned":
            unique_rows[seen[full_name]] = r

    rows = unique_rows

    # 统一排序
    key_map: Dict[str, Any] = {
        "updated": lambda r: r["updated_at"] or "",
        "pushed": lambda r: r["pushed_at"] or "",
        "full_name": lambda r: r["full_name"] or "",
        "stars": lambda r: r["stargazers_count"] or 0,
    }
    keyfunc = key_map.get(args.sort, key_map["updated"])
    rows.sort(key=keyfunc, reverse=(args.order == "desc"))

    # 限量
    if len(rows) > args.limit:
        rows = rows[:args.limit]

    return rows


# ──────────────────────────────────────────────────────────────
# 统一入口函数
# ──────────────────────────────────────────────────────────────


def execute_git_catalog_command(payload: GitCatalogInput) -> str:
    """
    执行 git_catalog 命令。

    Returns:
        JSON 字符串，包含 exit_code、count（记录条数）、rows（表格型数据）
    """
    try:
        provider = payload.provider
        cmd = payload.cmd
        args = payload.args

        if provider == CatalogProvider.github:
            g = gh_client()
            if cmd == CmdCatalog.cross_repos:
                rows = _fetch_user_activity_across_repos(g, args)  # type: ignore
            elif cmd == CmdCatalog.repo_authors:
                rows = _fetch_repo_activity_across_authors(g, args)  # type: ignore
            elif cmd == CmdCatalog.repos_by_author:
                rows = _list_repos_for_author(g, args)  # type: ignore
            elif cmd == CmdCatalog.authors_by_repo:
                rows = _list_authors_for_repo(g, args)  # type: ignore
            elif cmd == CmdCatalog.search_repos:
                rows = _search_repos_by_keyword(g, args)  # type: ignore
            elif cmd == CmdCatalog.org_repos:
                rows = _list_repos_for_org(g, args)  # type: ignore
            elif cmd == CmdCatalog.user_repos:
                rows = _list_user_repos(g, args)  # type: ignore
            else:
                raise ValueError(f"不支持的 cmd: {cmd}")

        elif provider == CatalogProvider.gitee:
            client = gitee_client()
            if cmd == CmdCatalog.cross_repos:
                rows = _fetch_user_activity_across_repos_gitee(client, args)  # type: ignore
            elif cmd == CmdCatalog.repo_authors:
                rows = _fetch_repo_activity_across_authors_gitee(client, args)  # type: ignore
            elif cmd == CmdCatalog.repos_by_author:
                rows = _list_repos_for_author_gitee(client, args)  # type: ignore
            elif cmd == CmdCatalog.authors_by_repo:
                rows = _list_authors_for_repo_gitee(client, args)  # type: ignore
            elif cmd == CmdCatalog.search_repos:
                rows = _search_repos_by_keyword_gitee(client, args)  # type: ignore
            elif cmd == CmdCatalog.org_repos:
                rows = _list_repos_for_org_gitee(client, args)  # type: ignore
            elif cmd == CmdCatalog.user_repos:
                rows = _list_user_repos_gitee(client, args)  # type: ignore
            else:
                raise ValueError(f"不支持的 cmd: {cmd}")

        elif provider == CatalogProvider.gitlab:
            client = gitlab_client()
            if cmd == CmdCatalog.cross_repos:
                rows = _fetch_user_activity_across_repos_gitlab(client, args)  # type: ignore
            elif cmd == CmdCatalog.repo_authors:
                rows = _fetch_repo_activity_across_authors_gitlab(client, args)  # type: ignore
            elif cmd == CmdCatalog.repos_by_author:
                rows = _list_repos_for_author_gitlab(client, args)  # type: ignore
            elif cmd == CmdCatalog.authors_by_repo:
                rows = _list_authors_for_repo_gitlab(client, args)  # type: ignore
            elif cmd == CmdCatalog.search_repos:
                rows = _search_repos_by_keyword_gitlab(client, args)  # type: ignore
            elif cmd == CmdCatalog.org_repos:
                rows = _list_repos_for_org_gitlab(client, args)  # type: ignore
            elif cmd == CmdCatalog.user_repos:
                rows = _list_user_repos_gitlab(client, args)  # type: ignore
            else:
                raise ValueError(f"不支持的 cmd: {cmd}")

        else:
            raise ValueError(f"不支持的 provider: {provider}")

        return json.dumps({
            "exit_code": 0,
            "count": len(rows),
            "rows": rows,
        }, ensure_ascii=False)

    except ValueError as e:
        # 参数验证错误或认证错误（ValueError 也用于认证错误）
        error_msg = str(e)
        # 检查是否是认证相关错误
        if "认证" in error_msg or "token" in error_msg.lower() or "unauthorized" in error_msg.lower() or "GITEE_TOKEN" in error_msg or "GITHUB_TOKEN" in error_msg or "GITLAB_TOKEN" in error_msg:
            return json.dumps({
                "exit_code": 1,
                "count": 0,
                "rows": [],
                "stderr": error_msg,  # 直接返回认证错误信息
            }, ensure_ascii=False)
        else:
            return json.dumps({
                "exit_code": 1,
                "count": 0,
                "rows": [],
                "stderr": f"参数验证错误: {error_msg}",
            }, ensure_ascii=False)
    
    except RuntimeError as e:
        # RuntimeError 通常是业务逻辑错误
        return json.dumps({
            "exit_code": 1,
            "count": 0,
            "rows": [],
            "stderr": str(e),
        }, ensure_ascii=False)

    except GithubException as e:
        # GitHub API 错误
        return json.dumps({
            "exit_code": 1,
            "count": 0,
            "rows": [],
            "stderr": f"GitHub API 错误: {str(e)}",
        }, ensure_ascii=False)

    except requests.exceptions.HTTPError as e:
        # Gitee/GitLab API HTTP 错误（包含状态码信息）
        error_msg = f"API HTTP 错误: {e.response.status_code}"
        if e.response.status_code == 404:
            error_msg += " (用户不存在或资源未找到)"
        elif e.response.status_code == 401:
            # 401 错误应该提供更详细的认证信息
            # 尝试检测是 Gitee 还是 GitLab
            try:
                error_detail = e.response.json()
                error_text = str(error_detail.get("message", "")).lower()
                if "gitlab" in error_text or "private-token" in error_text:
                    token_error = _check_gitlab_token_error(e.response)
                    if token_error:
                        error_msg = token_error
                    else:
                        error_msg = "GitLab API 需要认证，请设置 GITLAB_TOKEN 环境变量"
                else:
                    token_error = _check_gitee_token_error(e.response)
                    if token_error:
                        error_msg = token_error
                    else:
                        error_msg += " (需要认证，请设置 GITEE_TOKEN 环境变量)"
            except Exception:
                token_error = _check_gitlab_token_error(e.response)
                if token_error:
                    error_msg = token_error
                else:
                    token_error = _check_gitee_token_error(e.response)
                    if token_error:
                        error_msg = token_error
                    else:
                        error_msg += " (需要认证)"
        try:
            error_detail = e.response.json()
            if "message" in error_detail:
                error_msg += f" - {error_detail['message']}"
        except Exception:
            error_msg += f" - {str(e)}"
        return json.dumps({
            "exit_code": 1,
            "count": 0,
            "rows": [],
            "stderr": error_msg,
        }, ensure_ascii=False)
    
    except requests.exceptions.RequestException as e:
        # API 网络错误
        return json.dumps({
            "exit_code": 1,
            "count": 0,
            "rows": [],
            "stderr": f"API 网络错误: {str(e)}",
        }, ensure_ascii=False)

    except Exception as e:
        # 其他未知错误
        import traceback
        error_details = traceback.format_exc()
        return json.dumps({
            "exit_code": 1,
            "count": 0,
            "rows": [],
            "stderr": f"git_catalog 工具执行错误: {type(e).__name__}: {str(e)}\n详细信息: {error_details[-500:]}",
        }, ensure_ascii=False)

