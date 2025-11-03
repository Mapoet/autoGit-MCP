"""Data models for Git MCP tools."""
import os
from enum import Enum
from typing import Any, Dict, Optional

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

