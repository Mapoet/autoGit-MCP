"""Implementation of git_catalog command execution."""
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dateutil import parser as dateparser
from github import Auth, Github, GithubException, RateLimitExceededException

from .models import (
    AuthorsByRepoArgs,
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
# 统一入口函数
# ──────────────────────────────────────────────────────────────


def execute_git_catalog_command(payload: GitCatalogInput) -> str:
    """
    执行 git_catalog 命令。

    Returns:
        JSON 字符串，包含 exit_code、count（记录条数）、rows（表格型数据）
    """
    try:
        g = gh_client()
        cmd = payload.cmd
        args = payload.args

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

        return json.dumps({
            "exit_code": 0,
            "count": len(rows),
            "rows": rows,
        }, ensure_ascii=False)

    except ValueError as e:
        # 参数验证错误
        return json.dumps({
            "exit_code": 1,
            "count": 0,
            "rows": [],
            "stderr": f"参数验证错误: {str(e)}",
        }, ensure_ascii=False)

    except GithubException as e:
        # GitHub API 错误
        return json.dumps({
            "exit_code": 1,
            "count": 0,
            "rows": [],
            "stderr": f"GitHub API 错误: {str(e)}",
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

