"""MCP server exposing a structured Git tool.

The implementation follows the guidelines laid out in ``guide.md`` and the
accompanying documentation under ``docs/``.
"""
import json
import subprocess
import urllib.error
from typing import Annotated, Any, Dict, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .git_commands import execute_git_command
from .git_flow_commands import execute_git_flow_command
from .models import Cmd, DiffScope, FlowAction, FlowProvider, GitFlowInput, GitInput

server = FastMCP("git-mcp")
app = server.streamable_http_app()


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
            args=args or {},  # 处理 None 或空字典的情况
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


__all__ = [
    "app",
    "server",
    "git",
    "git_flow",
    "GitInput",
    "GitFlowInput",
    "Cmd",
    "FlowAction",
    "FlowProvider",
    "DiffScope",
]
