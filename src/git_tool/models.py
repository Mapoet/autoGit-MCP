"""Data models for Git MCP tools."""
import os
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator


class Cmd(str, Enum):
    """Supported git command subset."""

    status = "status"
    add = "add"
    commit = "commit"
    pull = "pull"
    push = "push"
    fetch = "fetch"
    merge = "merge"
    rebase = "rebase"
    diff = "diff"
    log = "log"
    branch = "branch"
    switch = "switch"
    tag = "tag"
    reset = "reset"
    revert = "revert"
    clean = "clean"
    remote = "remote"
    stash = "stash"
    submodule = "submodule"
    cherry_pick = "cherry-pick"


class GitInput(BaseModel):
    """Validated tool input."""

    repo_path: str
    cmd: Cmd
    args: Dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False
    allow_destructive: bool = False
    timeout_sec: int = 120

    @field_validator("repo_path")
    @classmethod
    def _repo_exists(cls, value: str) -> str:  # noqa: D401 - short helper
        """Ensure the provided path contains a Git repository."""

        git_dir = os.path.join(value, ".git")
        if not os.path.isdir(git_dir):
            raise ValueError("repo_path is not a git repository")
        return value


class FlowAction(str, Enum):
    """Supported git flow automation actions."""

    generate_commit_message = "generate_commit_message"
    combo_plan = "combo_plan"


class FlowProvider(str, Enum):
    """LLM providers available for git_flow."""

    opengpt = "opengpt"
    deepseek = "deepseek"


class DiffScope(str, Enum):
    """Supported diff collection strategies."""

    staged = "staged"
    workspace = "workspace"
    head = "head"


class GitFlowInput(BaseModel):
    """Validated input for git_flow automation."""

    repo_path: str
    action: FlowAction = FlowAction.generate_commit_message
    provider: FlowProvider = FlowProvider.deepseek
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    user_prompt: Optional[str] = None
    prompt_profile: Optional[str] = None  # String value, will be converted to PromptProfile enum when needed
    diff_scope: DiffScope = DiffScope.staged
    diff_target: Optional[str] = None
    include_readme: bool = True
    include_diff: bool = True
    include_status: bool = True
    max_readme_chars: int = 4000
    max_diff_chars: int = 8000
    max_status_chars: int = 2000
    extra_context: Optional[str] = None
    temperature: float = 0.2
    timeout_sec: int = 120
    combo_name: Optional[str] = None
    combo_replacements: Dict[str, str] = Field(default_factory=dict)

    @field_validator("repo_path")
    @classmethod
    def _repo_exists(cls, value: str) -> str:
        return GitInput._repo_exists(value)

    @field_validator("temperature")
    @classmethod
    def _validate_temperature(cls, value: float) -> float:
        if not 0.0 <= value <= 2.0:
            raise ValueError("temperature must be between 0 and 2")
        return value

    @field_validator("max_readme_chars")
    @classmethod
    def _positive_int_readme(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("maximum lengths must be positive")
        return value

    @field_validator("max_diff_chars")
    @classmethod
    def _positive_int_diff(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("maximum lengths must be positive")
        return value

    @field_validator("max_status_chars")
    @classmethod
    def _positive_int_status(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("maximum lengths must be positive")
        return value

    @field_validator("timeout_sec")
    @classmethod
    def _positive_timeout(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("timeout_sec must be positive")
        return value

    @model_validator(mode="after")
    def _validate_combo(self) -> "GitFlowInput":
        """Validate that combo_name is provided when action is combo_plan."""
        if self.action is FlowAction.combo_plan and not self.combo_name:
            raise ValueError("combo_name is required when action=combo_plan")
        return self


class WorkLogProvider(str, Enum):
    """LLM providers for work log summary generation."""

    openai = "openai"
    deepseek = "deepseek"


class WorkLogInput(BaseModel):
    """Validated input for work log generation."""

    # Repository sources
    repo_paths: list[str] = Field(default_factory=list, description="Local repository paths (comma-separated or list)")
    github_repos: list[str] = Field(default_factory=list, description="GitHub repositories (format: OWNER/REPO)")
    gitee_repos: list[str] = Field(default_factory=list, description="Gitee repositories (format: OWNER/REPO)")
    
    # Time range
    since: Optional[str] = Field(default=None, description="Start datetime (ISO format or YYYY-MM-DD). If not set, defaults to today 00:00:00")
    until: Optional[str] = Field(default=None, description="End datetime (ISO format or YYYY-MM-DD). If not set, defaults to today 23:59:59")
    days: Optional[int] = Field(default=None, description="If set, use last N days ending today. Overrides since/until")
    
    # Filtering
    author: Optional[str] = Field(default=None, description="Filter commits by author name or email")
    
    # Work session settings
    session_gap_minutes: int = Field(default=60, description="Gap in minutes to split work sessions")
    
    # Output settings
    title: Optional[str] = Field(default=None, description="Title for the work log document")
    add_summary: bool = Field(default=False, description="Add AI-generated summary at the end")
    
    # AI summary settings
    provider: WorkLogProvider = Field(default=WorkLogProvider.deepseek, description="LLM provider for summary generation")
    model: Optional[str] = Field(default=None, description="Model name (overrides default for provider)")
    system_prompt: Optional[str] = Field(default=None, description="Custom system prompt for AI summary")
    temperature: float = Field(default=0.3, description="Temperature for LLM (0.0-2.0)")
    
    @field_validator("session_gap_minutes")
    @classmethod
    def _positive_session_gap(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("session_gap_minutes must be positive")
        return value

    @field_validator("temperature")
    @classmethod
    def _validate_temperature(cls, value: float) -> float:
        if not 0.0 <= value <= 2.0:
            raise ValueError("temperature must be between 0 and 2")
        return value

    @field_validator("days")
    @classmethod
    def _positive_days(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return None
        # 处理字符串形式的数字（MCP JSON-RPC 可能传递字符串）
        if isinstance(value, str):
            try:
                value = int(value)
            except (ValueError, TypeError):
                raise ValueError(f"days 必须是正整数，收到: {value} (类型: {type(value).__name__})")
        # 确保是整数
        if not isinstance(value, int):
            raise ValueError(f"days 必须是整数，收到: {value} (类型: {type(value).__name__})")
        if value <= 0:
            raise ValueError("days must be positive")
        return value

    @model_validator(mode="after")
    def _validate_repos(self) -> "WorkLogInput":
        """Ensure at least one repository source is provided."""
        if not self.repo_paths and not self.github_repos and not self.gitee_repos:
            raise ValueError("At least one repository source must be provided (repo_paths, github_repos, or gitee_repos)")
        return self


# ──────────────────────────────────────────────────────────────
# Git Catalog Models
# ──────────────────────────────────────────────────────────────


class CmdCatalog(str, Enum):
    """Supported git catalog command subset."""

    cross_repos = "cross_repos"  # 不同仓库同一作者（明细）
    repo_authors = "repo_authors"  # 同一仓库不同作者（明细）
    repos_by_author = "repos_by_author"  # 同一作者在哪些仓库（列表）
    authors_by_repo = "authors_by_repo"  # 同一仓库活跃作者（列表）
    search_repos = "search_repos"  # 关键词检索仓库
    org_repos = "org_repos"  # 组织仓库列表
    user_repos = "user_repos"  # 作者拥有或 Star 的项目列表


class TimeWindow(BaseModel):
    """Time window for filtering commits."""

    since: Optional[str] = Field(
        default=None,
        description="起始时间, ISO 或日期，如 '2025-01-01' 或 '2025-01-01T00:00:00Z'",
        examples=["2025-01-01", "2025-01-01T00:00:00Z"],
    )
    until: Optional[str] = Field(
        default=None,
        description="结束时间, ISO 或日期，如 '2025-11-04' 或 '2025-11-04T23:59:59Z'",
        examples=["2025-11-04", "2025-11-04T23:59:59Z"],
    )


class CrossReposArgs(TimeWindow):
    """Arguments for cross_repos command (不同仓库同一作者明细)."""

    author_login: Optional[str] = Field(
        default=None,
        description="作者 GitHub 登录名",
        examples=["octocat"],
    )
    author_email: Optional[str] = Field(
        default=None,
        description="作者邮箱（更稳定）",
        examples=["user@example.com"],
    )
    owner: Optional[str] = Field(
        default=None,
        description="枚举此 owner 的仓库（用户或组织）。不填则默认枚举 author_login 的仓库",
        examples=["github", "octocat"],
    )
    repo_type: str = Field(
        default="owner",
        description="仓库类型",
        examples=["owner", "member", "all", "public", "private"],
    )
    max_per_repo: int = Field(
        default=1000,
        ge=1,
        le=5000,
        description="每仓最多抓取条数",
    )


class RepoAuthorsArgs(TimeWindow):
    """Arguments for repo_authors command (同一仓库不同作者明细)."""

    repo_full: str = Field(
        description="仓库全名 'owner/name'",
        examples=["github/octocat"],
    )
    authors_login: Optional[List[str]] = Field(
        default=None,
        description="作者登录名列表",
        examples=[["octocat", "user2"]],
    )
    authors_emails: Optional[List[str]] = Field(
        default=None,
        description="作者邮箱列表",
        examples=[["user1@example.com", "user2@example.com"]],
    )
    max_per_author: int = Field(
        default=1000,
        ge=1,
        le=5000,
        description="每作者最多抓取条数",
    )


class ReposByAuthorArgs(TimeWindow):
    """Arguments for repos_by_author command (同一作者在哪些仓库列表)."""

    author_login: Optional[str] = Field(
        default=None,
        description="作者登录名",
        examples=["octocat"],
    )
    author_email: Optional[str] = Field(
        default=None,
        description="作者邮箱（更稳定）",
        examples=["user@example.com"],
    )
    owner: Optional[str] = Field(
        default=None,
        description="枚举此 owner 的仓库（用户或组织）",
        examples=["github"],
    )
    repo_type: str = Field(
        default="owner",
        description="仓库类型",
        examples=["owner", "member", "all", "public", "private"],
    )
    min_commits: int = Field(
        default=1,
        ge=1,
        le=10000,
        description="最小提交数阈值",
    )


class AuthorsByRepoArgs(TimeWindow):
    """Arguments for authors_by_repo command (同一仓库活跃作者列表)."""

    repo_full: str = Field(
        description="仓库全名 'owner/name'",
        examples=["github/octocat"],
    )
    prefer: str = Field(
        default="login",
        description="作者主键偏好",
        examples=["login", "email", "name"],
    )
    min_commits: int = Field(
        default=1,
        ge=1,
        le=10000,
        description="最小提交数阈值",
    )


class SearchReposArgs(BaseModel):
    """Arguments for search_repos command (关键词检索仓库)."""

    keyword: str = Field(
        description="关键词（匹配 name/description/readme）",
        examples=["gnss", "machine learning"],
    )
    language: Optional[str] = Field(
        default=None,
        description="语言限定，如 C++/Python/TypeScript",
        examples=["Python", "C++", "TypeScript"],
    )
    min_stars: Optional[int] = Field(
        default=None,
        ge=0,
        description="最小 Star 数",
        examples=[50, 100],
    )
    pushed_since: Optional[str] = Field(
        default=None,
        description="最近活跃起始，如 '2025-09-01'",
        examples=["2025-09-01"],
    )
    topic: Optional[str] = Field(
        default=None,
        description="限定 topic",
        examples=["ai", "gnss"],
    )
    owner: Optional[str] = Field(
        default=None,
        description="限定用户或组织域",
        examples=["github", "tensorflow"],
    )
    sort: str = Field(
        default="updated",
        description="排序字段",
        examples=["updated", "stars", "forks"],
    )
    order: str = Field(
        default="desc",
        description="排序方向",
        examples=["desc", "asc"],
    )
    limit: int = Field(
        default=200,
        ge=1,
        le=2000,
        description="最多返回条数",
    )


class OrgReposArgs(BaseModel):
    """Arguments for org_repos command (组织仓库列表)."""

    org: str = Field(
        description="组织名",
        examples=["tensorflow", "microsoft"],
    )
    repo_type: str = Field(
        default="all",
        description="仓库类型",
        examples=["all", "public", "private", "forks", "sources", "member"],
    )
    include_archived: bool = Field(
        default=False,
        description="是否包含 archived 仓库",
    )
    sort: str = Field(
        default="updated",
        description="排序字段（GitHub 支持 updated/pushed/full_name）",
        examples=["updated", "pushed", "full_name"],
    )
    limit: int = Field(
        default=500,
        ge=1,
        le=5000,
        description="最多返回条数",
    )


class UserReposArgs(BaseModel):
    """Arguments for user_repos command (作者拥有或 Star 的项目列表)."""

    login: str = Field(
        description="GitHub 用户登录名",
        examples=["octocat", "mapoet"],
    )
    mode: str = Field(
        default="both",
        description="查询模式",
        examples=["owned", "starred", "both"],
    )
    include_private: bool = Field(
        default=False,
        description="是否包含私有仓库（需要 token 权限）",
    )
    include_archived: bool = Field(
        default=True,
        description="是否包含 archived 仓库",
    )
    include_forks: bool = Field(
        default=True,
        description="是否包含 fork 仓库",
    )
    sort: str = Field(
        default="updated",
        description="排序字段",
        examples=["updated", "pushed", "full_name", "stars"],
    )
    order: str = Field(
        default="desc",
        description="排序方向",
        examples=["desc", "asc"],
    )
    limit: int = Field(
        default=500,
        ge=1,
        le=5000,
        description="最多返回条数",
    )


class CatalogProvider(str, Enum):
    """Provider for git_catalog tool."""

    github = "github"
    gitee = "gitee"


class GitCatalogInput(BaseModel):
    """Validated input for git_catalog tool."""

    provider: CatalogProvider = Field(
        default=CatalogProvider.github,
        description="代码托管平台提供商",
        examples=["github", "gitee"],
    )
    cmd: CmdCatalog = Field(
        description="子命令",
        examples=["search_repos", "org_repos", "cross_repos"],
    )
    args: Union[
        CrossReposArgs,
        RepoAuthorsArgs,
        ReposByAuthorArgs,
        AuthorsByRepoArgs,
        SearchReposArgs,
        OrgReposArgs,
        UserReposArgs,
    ] = Field(
        description="该子命令的参数对象",
        examples=[
            {"keyword": "gnss", "language": "C++", "min_stars": 50},
            {"org": "tensorflow", "repo_type": "public", "limit": 200},
            {"login": "mapoet", "mode": "both", "limit": 200},
        ],
    )

    @model_validator(mode="after")
    def _validate_cmd_args_match(self) -> "GitCatalogInput":
        """Validate that args type matches cmd."""
        cmd_to_args_type = {
            CmdCatalog.cross_repos: CrossReposArgs,
            CmdCatalog.repo_authors: RepoAuthorsArgs,
            CmdCatalog.repos_by_author: ReposByAuthorArgs,
            CmdCatalog.authors_by_repo: AuthorsByRepoArgs,
            CmdCatalog.search_repos: SearchReposArgs,
            CmdCatalog.org_repos: OrgReposArgs,
            CmdCatalog.user_repos: UserReposArgs,
        }
        expected_type = cmd_to_args_type.get(self.cmd)
        if expected_type and not isinstance(self.args, expected_type):
            # Try to convert if possible
            if isinstance(self.args, dict):
                try:
                    self.args = expected_type(**self.args)  # type: ignore
                except Exception as e:
                    raise ValueError(
                        f"args 类型不匹配 cmd={self.cmd}。期望 {expected_type.__name__}，收到 {type(self.args).__name__}。错误: {e}"
                    )
            else:
                raise ValueError(
                    f"args 类型不匹配 cmd={self.cmd}。期望 {expected_type.__name__}，收到 {type(self.args).__name__}"
                )
        return self

