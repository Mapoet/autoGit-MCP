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

