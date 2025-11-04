"""MCP server exposing a structured Git tool.

The implementation follows the guidelines laid out in ``guide.md`` and the
accompanying documentation under ``docs/``.
"""
import json
import subprocess
import urllib.error
from typing import Annotated, Any, Dict, Optional

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .config import get_settings, mask_secret, reload_settings
from .git_commands import execute_git_command
from .git_flow_commands import execute_git_flow_command
from .git_gitwork_commands import execute_work_log_command
from .git_catalog_commands import execute_git_catalog_command
from .models import (
    CatalogProvider,
    Cmd,
    CmdCatalog,
    DiffScope,
    FlowAction,
    FlowProvider,
    GitCatalogInput,
    GitFlowInput,
    GitInput,
    WorkLogInput,
    WorkLogProvider,
)

# Load settings at startup
SETTINGS = get_settings()

server = FastMCP("git-mcp")
# SSE 模式（用于标准 MCP 客户端，需要 session ID）
mcp_app = server.streamable_http_app()

# 添加简单的 REST API（无需 session ID，用于直接 HTTP 调用）
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

rest_router = APIRouter(prefix="/api", tags=["REST API"])


@rest_router.post("/git")
async def rest_git(request: Request):
    """REST API 端点：调用 git 工具（无需 session ID）"""
    try:
        body = await request.json()
        # 确保所有必需参数都有默认值
        result = git(
            repo_path=body.get("repo_path"),
            cmd=body.get("cmd"),
            args=body.get("args", {}),
            dry_run=body.get("dry_run", False),
            allow_destructive=body.get("allow_destructive", False),
            timeout_sec=body.get("timeout_sec", 120),
        )
        result_dict = json.loads(result)
        return JSONResponse(content=result_dict)
    except Exception as e:
        import traceback
        return JSONResponse(
            status_code=500,
            content={"exit_code": 1, "stdout": "", "stderr": f"错误: {str(e)}\n{traceback.format_exc()[-300:]}"}
        )


@rest_router.post("/git_flow")
async def rest_git_flow(request: Request):
    """REST API 端点：调用 git_flow 工具（无需 session ID）"""
    try:
        body = await request.json()
        # 直接传递所有参数（git_flow 函数会处理默认值）
        result = git_flow(**body)
        result_dict = json.loads(result)
        return JSONResponse(content=result_dict)
    except Exception as e:
        import traceback
        return JSONResponse(
            status_code=500,
            content={"exit_code": 1, "stdout": "", "stderr": f"错误: {str(e)}\n{traceback.format_exc()[-300:]}"}
        )


@rest_router.post("/git_work")
async def rest_work_log(request: Request):
    """REST API 端点：调用 git_work 工具（无需 session ID）"""
    try:
        body = await request.json()
        # git_work 函数需要所有参数，提供默认值
        result = git_work(
            repo_paths=body.get("repo_paths"),
            github_repos=body.get("github_repos"),
            gitee_repos=body.get("gitee_repos"),
            since=body.get("since"),
            until=body.get("until"),
            days=body.get("days"),
            author=body.get("author"),
            session_gap_minutes=body.get("session_gap_minutes", 60),
            title=body.get("title"),
            add_summary=body.get("add_summary", False),
            provider=body.get("provider", "deepseek"),
            model=body.get("model"),
            system_prompt=body.get("system_prompt"),
            temperature=body.get("temperature", 0.3),
        )
        result_dict = json.loads(result)
        return JSONResponse(content=result_dict)
    except Exception as e:
        import traceback
        return JSONResponse(
            status_code=500,
            content={"exit_code": 1, "stdout": "", "stderr": f"错误: {str(e)}\n{traceback.format_exc()[-300:]}"}
        )


@rest_router.post("/git_catalog")
async def rest_git_catalog(request: Request):
    """REST API 端点：调用 git_catalog 工具（无需 session ID）"""
    try:
        body = await request.json()
        result = git_catalog(
            provider=body.get("provider", "github"),
            cmd=body.get("cmd"),
            args=body.get("args", {}),
        )
        result_dict = json.loads(result)
        return JSONResponse(content=result_dict)
    except Exception as e:
        import traceback
        return JSONResponse(
            status_code=500,
            content={"exit_code": 1, "count": 0, "rows": [], "stderr": f"错误: {str(e)}\n{traceback.format_exc()[-300:]}"}
        )


@rest_router.get("/tools")
async def rest_list_tools():
    """REST API 端点：列出所有可用工具"""
    return JSONResponse(content={
        "tools": [
            {"name": "git", "endpoint": "/api/git"},
            {"name": "git_flow", "endpoint": "/api/git_flow"},
            {"name": "git_work", "endpoint": "/api/git_work"},
            {"name": "git_catalog", "endpoint": "/api/git_catalog"}
        ]
    })


# 合并应用：将 MCP 子应用作为顶层（其内部管理 lifespan），在 /api 挂载 REST 应用
from fastapi import FastAPI

# 1) MCP 作为顶层（其自身暴露 /mcp 路由）
app = mcp_app

# 2) 在 /api 挂载独立 REST 应用
rest_api_app = FastAPI(title="Git MCP REST API")
rest_api_app.include_router(rest_router)
app.mount("/api", rest_api_app)


@server.tool()
def git(
    repo_path: Annotated[
        str,
        Field(description="The path to the Git repository directory. Must contain a .git subdirectory. This is the working directory where Git commands will be executed.")
    ],
    cmd: Annotated[
        str,
        Field(description="The Git subcommand to execute. Supported commands: status, add, commit, pull, push, fetch, merge, rebase, diff, log, branch, switch, tag, reset, revert, clean, remote, stash, submodule, cherry-pick.")
    ],
    args: Annotated[
        dict,
        Field(
            default_factory=dict,
            description=(
                "Optional dictionary of command-specific arguments. The structure depends on the cmd value. "
                "Supported cmd-specific args: "
                "'status': {short: bool, branch: bool} - short/branch any true uses -sb; "
                "'add': {paths: str|List[str], all: bool, patch: bool} - default stages current dir, all=true equals -A; "
                "'commit': {message: str* (required), all: bool, amend: bool, no_verify: bool, signoff: bool} - message required, all=true equals -a; "
                "'pull': {remote: str, branch: str, rebase: bool} - default remote=origin, rebase=true; "
                "'push': {remote: str, branch: str, set_upstream: bool, force_with_lease: bool, force: bool (requires allow_destructive), tags: bool}; "
                "'fetch': {remote: str, all: bool, prune: bool} - default prune=true; "
                "'merge': {branch: str* (required), no_ff: bool, ff_only: bool, squash: bool} - default no_ff=true; "
                "'rebase': {upstream: str* (required), interactive: bool, autosquash: bool, continue: bool, abort: bool} - continue/abort mutually exclusive, default autosquash=true; "
                "'diff': {cached: bool, name_only: bool, against: str} - default compares with HEAD; "
                "'log': {oneline: bool, graph: bool, decorate: bool, all: bool, max_count: int} - defaults enable oneline/graph/decorate; "
                "'branch': {create: str, delete: str, force: bool, verbose: bool} - default lists branches with tracking info; "
                "'switch': {branch: str* (required), create: bool} - create=true equals 'git switch -c'; "
                "'tag': {name: str, annotate: bool, message: str, delete: str, list: bool} - annotate=true requires message; "
                "'reset': {mode: 'soft'|'mixed'|'hard', target: str} - mode=hard requires allow_destructive; "
                "'revert': {commit: str* (required), no_edit: bool} - default --no-edit; "
                "'clean': {force: bool, dirs: bool, interactive: bool} - force/dirs any true requires allow_destructive; "
                "'remote': {action: 'list'|'add'|'remove'|'rename'|'set_url'|'prune', name: str, url: str, new_name: str, verbose: bool} - action=list defaults to -v; "
                "'stash': {action: 'list'|'push'|'apply'|'pop'|'drop'|'clear', message: str, include_untracked: bool, all: bool, pathspec, ref: str} - drop/clear require allow_destructive; "
                "'submodule': {action: 'update'|'sync'|'status', init: bool, recursive: bool, path: str} - update defaults to --init --recursive; "
                "'cherry-pick': {commits: str|List[str]* (required, or use 'commit'), continue: bool, abort: bool, quit: bool, skip: bool, no_commit: bool, edit: bool, signoff: bool} - commits/commit mutually exclusive with continue/abort/quit/skip. "
                "Fields marked with * are required. All parameters are validated with type checking and provide friendly error messages."
            ),
        ),
    ],
    dry_run: Annotated[
        bool,
        Field(default=False, description="If True, returns the command that would be executed without actually running it. Only applies to potentially destructive commands (commit, merge, reset, revert, clean). Returns a DRY-RUN message with the command string.")
    ],
    allow_destructive: Annotated[
        bool,
        Field(default=False, description="If True, allows execution of destructive Git operations such as 'reset --hard', 'clean -fd', 'push --force'. By default, these operations are blocked for safety. Must be explicitly set to True to enable.")
    ],
    timeout_sec: Annotated[
        int,
        Field(default=120, description="Maximum execution time in seconds for the Git command. If the command exceeds this timeout, it will be terminated and return a non-zero exit_code. Default is 120 seconds.")
    ],
) -> str:
    """Run a controlled Git command.
    
    Executes a Git subcommand with structured arguments, validation, and safety checks.
    Returns a JSON string containing exit_code, stdout, stderr, and optionally parsed results.
    """
    try:
        payload = GitInput(
            repo_path=repo_path,
            cmd=Cmd(cmd),
            args=args or {},
            dry_run=dry_run,
            allow_destructive=allow_destructive,
            timeout_sec=timeout_sec,
        )
        return execute_git_command(payload)
    except ValueError as e:
        # 参数验证错误（如 cmd 不是有效的枚举值）
        return json.dumps({
            "exit_code": 1,
            "stdout": "",
            "stderr": f"参数验证错误: {str(e)}",
        })


@server.tool()
def git_flow(
    repo_path: Annotated[
        str,
        Field(description="The path to the Git repository directory. Must contain a .git subdirectory. This repository's README and Git changes will be used as context for LLM processing.")
    ],
    action: Annotated[
        str,
        Field(default="generate_commit_message", description="The automation action to perform. Supported values: 'generate_commit_message' (generate a conventional commit message from changes) or 'combo_plan' (generate an execution plan for a Git combo command template). Default is 'generate_commit_message'.")
    ],
    provider: Annotated[
        str,
        Field(default="deepseek", description="The LLM provider to use for processing. Supported values: 'opengpt' (OpenGPT API) or 'deepseek' (DeepSeek API). Requires corresponding API key environment variable (OPENGPT_API_KEY or DEEPSEEK_API_KEY). Default is 'deepseek'.")
    ],
    model: Annotated[
        Optional[str],
        Field(default=None, description="Optional model name to override the default model for the selected provider. If not provided, uses the default model for the provider (deepseek-chat for DeepSeek, gpt-4.1-mini for OpenGPT) or the value from provider-specific environment variable (DEEPSEEK_MODEL or OPENGPT_MODEL).")
    ],
    system_prompt: Annotated[
        Optional[str],
        Field(default=None, description="Optional custom system prompt to override the default. If provided, this will be used instead of the default system prompt. For combo_plan action, this can customize how the LLM generates execution plans.")
    ],
    user_prompt: Annotated[
        Optional[str],
        Field(default=None, description="Optional custom user prompt to override the default. If provided, this will be used instead of the default user prompt. This allows fine-tuning the exact instructions given to the LLM.")
    ],
    prompt_profile: Annotated[
        Optional[str],
        Field(default=None, description="Optional predefined prompt profile to use. Supported values: 'software_engineering', 'devops', 'product_analysis', 'documentation', 'data_analysis'. Each profile provides specialized system and user prompts tailored to different use cases. If both prompt_profile and custom prompts (system_prompt/user_prompt) are provided, custom prompts take precedence.")
    ],
    diff_scope: Annotated[
        str,
        Field(default="staged", description="The scope of Git diff to collect for context. Supported values: 'staged' (git diff --cached, default), 'workspace' (git diff, working directory changes), or 'head' (git diff HEAD, changes since HEAD). Default is 'staged'.")
    ],
    diff_target: Annotated[
        Optional[str],
        Field(default=None, description="Optional Git reference (commit hash, branch name, or tag) to compare against when diff_scope is 'head'. If not provided and diff_scope is 'head', defaults to 'HEAD'. Ignored for other diff_scope values.")
    ],
    include_readme: Annotated[
        bool,
        Field(default=True, description="Whether to include the repository README file content as context. The README is truncated to max_readme_chars if provided. Helps the LLM understand project context and conventions. Default is True.")
    ],
    include_diff: Annotated[
        bool,
        Field(default=True, description="Whether to include Git diff output as context. The diff is truncated to max_diff_chars if provided. This is the primary source of change information for generating commit messages. Default is True.")
    ],
    include_status: Annotated[
        bool,
        Field(default=True, description="Whether to include Git status output as context. The status is truncated to max_status_chars if provided. Provides information about untracked files and branch state. Default is True.")
    ],
    max_readme_chars: Annotated[
        int,
        Field(default=4000, description="Maximum number of characters to include from the README file. If the README exceeds this limit, it will be truncated. This helps manage token usage while preserving important context. Default is 4000 characters.")
    ],
    max_diff_chars: Annotated[
        int,
        Field(default=8000, description="Maximum number of characters to include from the Git diff output. If the diff exceeds this limit, it will be truncated. Larger diffs may provide more context but increase token costs. Default is 8000 characters.")
    ],
    max_status_chars: Annotated[
        int,
        Field(default=2000, description="Maximum number of characters to include from the Git status output. If the status exceeds this limit, it will be truncated. Default is 2000 characters.")
    ],
    extra_context: Annotated[
        Optional[str],
        Field(default=None, description="Optional additional context string to include in the prompt. Useful for providing requirements descriptions, issue links, or other contextual information that helps the LLM generate better output. This context is appended to the constructed prompt.")
    ],
    temperature: Annotated[
        float,
        Field(default=0.2, description="Temperature parameter for the LLM (controls randomness). Value must be between 0.0 and 2.0. Lower values (e.g., 0.2) produce more deterministic, focused outputs. Higher values (e.g., 0.8-1.0) produce more creative, varied outputs. Default is 0.2 for consistent commit message generation.")
    ],
    timeout_sec: Annotated[
        int,
        Field(default=120, description="Maximum time in seconds to wait for the LLM API response. If the request exceeds this timeout, it will fail with a timeout error. Default is 120 seconds.")
    ],
    combo_name: Annotated[
        Optional[str],
        Field(default=None, description="Required when action is 'combo_plan'. Specifies the name of the Git combo command template to use. The combo template defines a sequence of Git commands with placeholders that need to be filled. See git_combos module for available combo templates.")
    ],
    combo_replacements: Annotated[
        dict,
        Field(default_factory=dict, description="Optional dictionary of placeholder replacements for combo_plan action. Keys are placeholder names from the combo template (e.g., 'branch', 'remote'), values are the actual values to substitute. If not provided or if some placeholders remain unfilled, the LLM will be asked to complete them based on the context.")
    ],
) -> str:
    """Expose git workflow automations powered by external LLM providers.
    
    This tool uses LLM providers (DeepSeek or OpenGPT) to generate commit messages
    or execution plans based on repository context, Git changes, and custom prompts.
    It automatically collects README content, Git diffs, and status information to
    provide rich context for the LLM.
    
    Returns a JSON string with exit_code (0 for success, non-zero for errors),
    stdout (the generated content), stderr (error messages if any), and details
    (metadata about the provider, model, and scope used).
    """
    return execute_git_flow_command(
        repo_path=repo_path,
        action=action,
        provider=provider,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        prompt_profile=prompt_profile,
        diff_scope=diff_scope,
        diff_target=diff_target,
        include_readme=include_readme,
        include_diff=include_diff,
        include_status=include_status,
        max_readme_chars=max_readme_chars,
        max_diff_chars=max_diff_chars,
        max_status_chars=max_status_chars,
        extra_context=extra_context,
        temperature=temperature,
        timeout_sec=timeout_sec,
        combo_name=combo_name,
        combo_replacements=combo_replacements or {},
    )


@server.tool()
def git_work(
    repo_paths: Annotated[
        Optional[list[str]],
        Field(
            default=None,
            description="Local repository paths. Can be a single path or list of paths. At least one repository source (repo_paths, github_repos, gitee_repos, or gitlab_repos) must be provided."
        ),
    ],
    github_repos: Annotated[
        Optional[list[str]],
        Field(
            default=None,
            description="GitHub repositories in format OWNER/REPO. Can be a single repo or list of repos. Requires GITHUB_TOKEN environment variable for private repos."
        ),
    ],
    gitee_repos: Annotated[
        Optional[list[str]],
        Field(
            default=None,
            description="Gitee repositories in format OWNER/REPO. Can be a single repo or list of repos. Requires GITEE_TOKEN environment variable for private repos."
        ),
    ],
    gitlab_repos: Annotated[
        Optional[list[str]],
        Field(
            default=None,
            description="GitLab repositories in format NAMESPACE/PROJECT. Can be a single repo or list of repos. Requires GITLAB_TOKEN or GITLAB_PRIVATE_TOKEN environment variable for private repos. Supports custom GitLab instances via GITLAB_URL environment variable."
        ),
    ],
    since: Annotated[
        Optional[str],
        Field(
            default=None,
            description="Start datetime in ISO format (e.g., '2024-11-03T00:00:00') or YYYY-MM-DD. If not set, defaults to today 00:00:00."
        ),
    ],
    until: Annotated[
        Optional[str],
        Field(
            default=None,
            description="End datetime in ISO format (e.g., '2024-11-03T23:59:59') or YYYY-MM-DD. If not set, defaults to today 23:59:59."
        ),
    ],
    days: Annotated[
        Optional[int],
        Field(
            default=None,
            description="If set, use last N days ending today. This overrides since/until parameters."
        ),
    ],
    author: Annotated[
        Optional[str],
        Field(
            default=None,
            description="Filter commits by author name or email (case-insensitive partial match)."
        ),
    ],
    session_gap_minutes: Annotated[
        int,
        Field(
            default=60,
            description="Gap in minutes to split work sessions. Commits within this gap are considered part of the same session."
        ),
    ],
    title: Annotated[
        Optional[str],
        Field(
            default=None,
            description="Title for the work log document. If not set, auto-generates based on date range."
        ),
    ],
    add_summary: Annotated[
        bool,
        Field(
            default=False,
            description="If True, add AI-generated Chinese summary at the end of the work log."
        ),
    ],
    provider: Annotated[
        str,
        Field(
            default="deepseek",
            description="LLM provider for summary generation. Supported: 'openai' or 'deepseek'. Requires corresponding API key (OPENAI_API_KEY or DEEPSEEK_API_KEY)."
        ),
    ],
    model: Annotated[
        Optional[str],
        Field(
            default=None,
            description="Model name to use. Overrides default for provider (gpt-4o-mini for OpenAI, deepseek-chat for DeepSeek)."
        ),
    ],
    system_prompt: Annotated[
        Optional[str],
        Field(
            default=None,
            description="Custom system prompt for AI summary. If not provided, uses default prompt optimized for technical work summaries."
        ),
    ],
    temperature: Annotated[
        float,
        Field(
            default=0.3,
            description="Temperature parameter for LLM (0.0-2.0). Lower values produce more deterministic outputs."
        ),
    ],
) -> str:
    """Generate work log from git commits.
    
    This tool analyzes git commits from local repositories, GitHub, Gitee, or GitLab to generate
    a structured work log. It can:
    - Collect commits from multiple repository sources
    - Group commits by date
    - Compute work sessions based on commit timestamps
    - Generate AI-powered summaries (optional)
    
    Supports both single-project and multi-project analysis. For remote repositories:
    - GitHub: Set GITHUB_TOKEN environment variable for private repos
    - Gitee: Set GITEE_TOKEN environment variable for private repos
    - GitLab: Set GITLAB_TOKEN or GITLAB_PRIVATE_TOKEN environment variable for private repos
      (supports custom GitLab instances via GITLAB_URL environment variable)
    
    Returns a JSON string with exit_code, stdout (markdown work log), and stderr (error messages).
    """
    try:
        # Convert None lists to empty lists
        repo_paths_list = repo_paths or []
        github_repos_list = github_repos or []
        gitee_repos_list = gitee_repos or []
        gitlab_repos_list = gitlab_repos or []
        
        # Validate provider
        try:
            provider_enum = WorkLogProvider(provider)
        except ValueError:
            return json.dumps({
                "exit_code": 1,
                "stdout": "",
                "stderr": f"不支持的提供者: {provider}。支持的提供者: openai, deepseek",
            })

        payload = WorkLogInput(
            repo_paths=repo_paths_list,
            github_repos=github_repos_list,
            gitee_repos=gitee_repos_list,
            gitlab_repos=gitlab_repos_list,
            since=since,
            until=until,
            days=days,
            author=author,
            session_gap_minutes=session_gap_minutes,
            title=title,
            add_summary=add_summary,
            provider=provider_enum,
            model=model,
            system_prompt=system_prompt,
            temperature=temperature,
        )
        return execute_work_log_command(payload)
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
            "stderr": f"git_work 工具执行错误: {type(e).__name__}: {str(e)}\n详细信息: {error_details[-500:]}",
        })


@server.tool()
def git_catalog(
    provider: Annotated[
        str,
        Field(
            default="github",
            description="代码托管平台提供商。支持 'github'、'gitee' 或 'gitlab'。默认 'github'。",
            examples=["github", "gitee", "gitlab"],
        ),
    ],
    cmd: Annotated[
        str,
        Field(
            description="子命令。支持的子命令: 'cross_repos' (不同仓库同一作者明细), 'repo_authors' (同一仓库不同作者明细), 'repos_by_author' (同一作者在哪些仓库列表), 'authors_by_repo' (同一仓库活跃作者列表), 'search_repos' (关键词检索仓库), 'org_repos' (组织仓库列表), 'user_repos' (作者拥有或 Star 的项目列表)",
            examples=["search_repos", "org_repos", "cross_repos", "user_repos"],
        ),
    ],
    args: Annotated[
        dict,
        Field(
            default_factory=dict,
            description=(
                "该子命令的参数对象。参数结构取决于 cmd 值。支持的子命令及其参数：\n\n"
                "'cross_repos' (不同仓库同一作者明细): "
                "{author_login: str (可选), author_email: str (可选), owner: str (可选), "
                "repo_type: str (默认 'owner', 可选: 'owner'|'member'|'all'|'public'|'private'), "
                "max_per_repo: int (默认 1000, 范围 1-5000), "
                "since: str (可选, 起始时间 ISO 或日期如 '2025-01-01'), "
                "until: str (可选, 结束时间 ISO 或日期如 '2025-11-04')} - "
                "查询指定作者在多个仓库中的提交记录。author_login 和 author_email 至少提供一个；"
                "owner 指定枚举的仓库范围（用户或组织），不填则默认枚举 author_login 的仓库。\n\n"
                "'repo_authors' (同一仓库不同作者明细): "
                "{repo_full: str* (必填, 格式 'owner/name'), "
                "authors_login: List[str] (可选), authors_emails: List[str] (可选), "
                "max_per_author: int (默认 1000, 范围 1-5000), "
                "since: str (可选), until: str (可选)} - "
                "查询指定仓库中多个作者的提交记录。如果不提供 authors_login 和 authors_emails，"
                "则返回时间窗内所有提交。\n\n"
                "'repos_by_author' (同一作者在哪些仓库列表): "
                "{author_login: str (可选), author_email: str (可选), owner: str (可选), "
                "repo_type: str (默认 'owner'), min_commits: int (默认 1, 范围 1-10000), "
                "since: str (可选), until: str (可选)} - "
                "列出指定作者活跃的仓库及其提交数（按提交数降序）。返回格式: {repo: str, commits: int}。\n\n"
                "'authors_by_repo' (同一仓库活跃作者列表): "
                "{repo_full: str* (必填, 格式 'owner/name'), "
                "prefer: str (默认 'login', 可选: 'login'|'email'|'name' 作为主键), "
                "min_commits: int (默认 1, 范围 1-10000), "
                "since: str (可选), until: str (可选)} - "
                "列出指定仓库中的活跃作者及其提交数（按提交数降序）。返回格式: "
                "{repo: str, author_key: str, author_login: str, author_email: str, commits: int}。\n\n"
                "'search_repos' (关键词检索仓库): "
                "{keyword: str* (必填, 匹配 name/description/readme), "
                "language: str (可选, 如 'Python'|'C++'|'TypeScript'), "
                "min_stars: int (可选, 最小 Star 数), "
                "pushed_since: str (可选, 最近活跃起始如 '2025-09-01'), "
                "topic: str (可选, 限定 topic), owner: str (可选, 限定用户或组织域), "
                "sort: str (默认 'updated', 可选: 'updated'|'stars'|'forks'), "
                "order: str (默认 'desc', 可选: 'desc'|'asc'), "
                "limit: int (默认 200, 范围 1-2000)} - "
                "根据关键词、语言、Star 数等条件搜索仓库。\n\n"
                "'org_repos' (组织仓库列表): "
                "{org: str* (必填, 组织名), "
                "repo_type: str (默认 'all', 可选: 'all'|'public'|'private'|'forks'|'sources'|'member'), "
                "include_archived: bool (默认 false, 是否包含 archived 仓库), "
                "sort: str (默认 'updated', 可选: 'updated'|'pushed'|'full_name'), "
                "limit: int (默认 500, 范围 1-5000)} - "
                "列出指定组织的所有仓库。\n\n"
                "'user_repos' (作者拥有或 Star 的项目列表): "
                "{login: str* (必填, GitHub 用户登录名), "
                "mode: str (默认 'both', 可选: 'owned'|'starred'|'both'), "
                "include_private: bool (默认 false, 是否包含私有仓库，需要 token 权限), "
                "include_archived: bool (默认 true, 是否包含 archived 仓库), "
                "include_forks: bool (默认 true, 是否包含 fork 仓库), "
                "sort: str (默认 'updated', 可选: 'updated'|'pushed'|'full_name'|'stars'), "
                "order: str (默认 'desc', 可选: 'desc'|'asc'), "
                "limit: int (默认 500, 范围 1-5000)} - "
                "列出指定用户拥有或 Star 的仓库列表。返回格式包含 relation 字段（'owned' 或 'starred'）标识来源。"
                "当 mode='both' 时，会合并 owned 和 starred 的结果，然后统一排序和限量。\n\n"
                "字段标记说明: * 表示必填字段。时间字段支持 ISO 格式（如 '2025-01-01T00:00:00Z'）"
                "或简单日期格式（如 '2025-01-01'）。所有参数都经过类型验证，提供友好的错误消息。"
            ),
        ),
    ],
) -> str:
    """GitHub/Gitee/GitLab 活动/仓库目录查询工具。
    
    统一入口：支持 7 个子命令查询 GitHub、Gitee 或 GitLab 仓库和提交活动。
    
    子命令说明：
    - cross_repos: 不同仓库同一作者（提交明细） - 查询指定作者在多个仓库中的提交记录
    - repo_authors: 同一仓库不同作者（提交明细） - 查询指定仓库中多个作者的提交记录
    - repos_by_author: 同一作者在哪些仓库活跃（列表） - 列出指定作者活跃的仓库及其提交数
    - authors_by_repo: 同一仓库活跃作者（列表） - 列出指定仓库中的活跃作者及其提交数
    - search_repos: 关键词搜索仓库 - 根据关键词、语言、Star 数等条件搜索仓库
    - org_repos: 组织仓库列表 - 列出指定组织的所有仓库（GitLab 使用 groups）
    - user_repos: 作者拥有或 Star 的项目列表 - 列出指定用户拥有或 Star 的仓库，支持合并查询和多种过滤排序选项
    
    认证：
    - GitHub: 使用环境变量 GITHUB_TOKEN（未设置则匿名，速率限制 60/h）
    - Gitee: 使用环境变量 GITEE_TOKEN（访问私有仓库时必填）
    - GitLab: 使用环境变量 GITLAB_TOKEN 或 GITLAB_PRIVATE_TOKEN（访问私有仓库时必填）
    - 如需使用自定义 GitLab 实例，请设置 GITLAB_URL 环境变量（默认: https://gitlab.com/api/v4）
    - 设置 token 可提高 API 速率限制并访问私有仓库
    
    返回格式：
    - JSON 字符串，包含 exit_code（0 成功，非 0 失败）、count（记录条数）、rows（表格型数据数组）
    - rows 数组中的每个对象代表一条记录，字段取决于子命令类型
    """
    try:
        # 构建输入对象
        payload = GitCatalogInput(provider=CatalogProvider(provider), cmd=CmdCatalog(cmd), args=args)
        return execute_git_catalog_command(payload)
    except ValueError as e:
        # 参数验证错误（如 cmd 不是有效的枚举值）
        return json.dumps({
            "exit_code": 1,
            "count": 0,
            "rows": [],
            "stderr": f"参数验证错误: {str(e)}",
        }, ensure_ascii=False)
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        return json.dumps({
            "exit_code": 1,
            "count": 0,
            "rows": [],
            "stderr": f"git_catalog 工具执行错误: {type(e).__name__}: {str(e)}\n详细信息: {error_details[-500:]}",
        }, ensure_ascii=False)


@server.tool()
def health() -> str:
    """检查环境配置与外部服务就绪情况（不泄露明文密钥）。
    
    返回当前配置的状态信息，包括：
    - 环境变量是否已设置（掩码显示）
    - 各功能模块的可用性
    
    注意：此工具不会泄露密钥的完整内容，仅显示掩码后的值用于验证配置。
    
    Returns:
        JSON 字符串，包含配置状态和工具可用性信息
    """
    s = get_settings()
    
    # 检查外部客户端可用性
    github_ready = bool(s.github_token)
    gitee_ready = bool(s.gitee_token)
    gitlab_ready = bool(s.get_gitlab_token())
    deepseek_ready = bool(s.deepseek_api_key)
    opengpt_ready = bool(s.opengpt_api_key)
    openai_ready = bool(s.openai_api_key)
    
    return json.dumps({
        "status": "ok",
        "config": {
            "deepseek": {
                "api_key": mask_secret(s.deepseek_api_key),
                "api_url": s.deepseek_api_url,
                "model": s.deepseek_model,
                "ready": deepseek_ready,
            },
            "opengpt": {
                "api_key": mask_secret(s.opengpt_api_key),
                "api_url": s.opengpt_api_url,
                "model": s.opengpt_model,
                "ready": opengpt_ready,
            },
            "openai": {
                "api_key": mask_secret(s.openai_api_key),
                "ready": openai_ready,
            },
            "github": {
                "token": mask_secret(s.github_token),
                "ready": github_ready,
            },
            "gitee": {
                "token": mask_secret(s.gitee_token),
                "ready": gitee_ready,
            },
            "gitlab": {
                "token": mask_secret(s.get_gitlab_token()),
                "url": s.gitlab_url,
                "ready": gitlab_ready,
            },
        },
        "tools_available": {
            "git": True,  # 始终可用（本地操作）
            "git_flow": deepseek_ready or opengpt_ready,
            "git_work": {
                "local_repos": True,  # 始终可用
                "github_repos": github_ready,
                "gitee_repos": gitee_ready,
                "gitlab_repos": gitlab_ready,
                "ai_summary": deepseek_ready or openai_ready,
            },
            "git_catalog": {
                "github": True,  # 匿名访问可用，但建议设置 token
                "gitee": True,  # 公开仓库可用，但建议设置 token
                "gitlab": True,  # 公开仓库可用，但建议设置 token
            },
        },
    }, ensure_ascii=False)


@server.tool()
def reload_config() -> str:
    """重新加载环境变量配置（热重载）。
    
    从环境变量重新加载配置。注意：
    - 外部客户端（如 GitHub、OpenAI 客户端）不会自动重建
    - 如果需要完全更新外部客户端，建议重启服务器进程
    
    Returns:
        JSON 字符串，包含重新加载后的配置状态
    """
    try:
        global SETTINGS
        SETTINGS = reload_settings()
        return json.dumps({
            "status": "ok",
            "message": "配置已重新加载",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"重新加载配置失败: {str(e)}",
        }, ensure_ascii=False)


__all__ = [
    "app",
    "server",
    "git",
    "git_flow",
    "git_work",
    "git_catalog",
    "health",
    "reload_config",
    "GitInput",
    "GitFlowInput",
    "WorkLogInput",
    "GitCatalogInput",
    "Cmd",
    "CmdCatalog",
    "FlowAction",
    "FlowProvider",
    "WorkLogProvider",
    "DiffScope",
    "SETTINGS",
]
