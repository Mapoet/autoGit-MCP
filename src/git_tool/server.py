"""MCP server exposing a structured Git tool.

The implementation follows the guidelines laid out in ``guide.md`` and the
accompanying documentation under ``docs/``.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import urllib.error
import urllib.request
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional

from mcp.server.fastapi import FastAPIMCPServer
from pydantic import BaseModel, Field, validator

app, server = FastAPIMCPServer("git-mcp")


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


class GitInput(BaseModel):
    """Validated tool input."""

    repo_path: str
    cmd: Cmd
    args: Dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False
    allow_destructive: bool = False
    timeout_sec: int = 120

    @validator("repo_path")
    def _repo_exists(cls, value: str) -> str:  # noqa: D401 - short helper
        """Ensure the provided path contains a Git repository."""

        git_dir = os.path.join(value, ".git")
        if not os.path.isdir(git_dir):
            raise ValueError("repo_path is not a git repository")
        return value


def _ensure_safe(flag: bool, allow: bool, message: str) -> None:
    """Guard potentially destructive operations."""

    if flag and not allow:
        raise ValueError(f"{message} requires allow_destructive=true")


def _extend(argv: List[str], items: Iterable[str]) -> List[str]:
    argv.extend(items)
    return argv


def _map_status(args: Dict[str, Any]) -> List[str]:
    argv = ["status"]
    if args.get("short"):
        argv.append("-sb")
    elif args.get("branch"):
        argv.append("-sb")
    return argv


def _map_add(args: Dict[str, Any]) -> List[str]:
    argv = ["add"]
    if args.get("all"):
        argv.append("-A")
    if args.get("patch"):
        argv.append("-p")
    paths = args.get("paths")
    if not paths:
        argv.append(".")
    elif isinstance(paths, (list, tuple)):
        _extend(argv, [str(path) for path in paths])
    else:
        argv.append(str(paths))
    return argv


def _map_commit(args: Dict[str, Any]) -> List[str]:
    argv = ["commit"]
    if args.get("all"):
        argv.append("-a")
    if args.get("amend"):
        argv.append("--amend")
    if args.get("no_verify"):
        argv.append("--no-verify")
    if args.get("signoff"):
        argv.append("--signoff")
    message = args.get("message")
    if not message:
        raise ValueError("commit message is required")
    _extend(argv, ["-m", message])
    return argv


def _map_pull(args: Dict[str, Any]) -> List[str]:
    remote = args.get("remote", "origin")
    branch = args.get("branch")
    argv = ["pull", remote]
    if branch:
        argv.append(branch)
    if args.get("rebase", True):
        argv.insert(1, "--rebase")
    return argv


def _map_push(args: Dict[str, Any], allow_destructive: bool) -> List[str]:
    remote = args.get("remote", "origin")
    branch = args.get("branch")
    set_upstream = args.get("set_upstream")

    if set_upstream and branch:
        argv: List[str] = ["push", "-u", remote, branch]
    else:
        argv = ["push", remote]
        if branch:
            argv.append(branch)

    if args.get("force_with_lease"):
        argv.append("--force-with-lease")
    if args.get("force"):
        _ensure_safe(True, allow_destructive, "push --force")
        argv.append("--force")
    if args.get("tags"):
        argv.append("--tags")
    return argv


def _map_fetch(args: Dict[str, Any]) -> List[str]:
    argv = ["fetch"]
    if args.get("all"):
        argv.append("--all")
    if args.get("prune", True):
        argv.append("--prune")
    remote = args.get("remote")
    if remote:
        argv.append(remote)
    return argv


def _map_merge(args: Dict[str, Any]) -> List[str]:
    branch = args.get("branch")
    if not branch:
        raise ValueError("merge requires branch")
    argv: List[str] = ["merge", branch]
    if args.get("ff_only"):
        argv.insert(1, "--ff-only")
    elif args.get("squash"):
        argv.insert(1, "--squash")
    elif args.get("no_ff", True):
        argv.insert(1, "--no-ff")
    return argv


def _map_rebase(args: Dict[str, Any]) -> List[str]:
    if args.get("continue"):
        return ["rebase", "--continue"]
    if args.get("abort"):
        return ["rebase", "--abort"]
    upstream = args.get("upstream")
    if not upstream:
        raise ValueError("rebase requires upstream")
    argv: List[str] = ["rebase", upstream]
    if args.get("interactive"):
        argv.insert(1, "-i")
    if args.get("autosquash", True):
        argv.insert(1, "--autosquash")
    return argv


def _map_diff(args: Dict[str, Any]) -> List[str]:
    argv = ["diff"]
    if args.get("cached"):
        argv.append("--cached")
    if args.get("name_only"):
        argv.append("--name-only")
    against = args.get("against")
    if against:
        argv.append(str(against))
    return argv


def _map_log(args: Dict[str, Any]) -> List[str]:
    argv = ["log"]
    if args.get("oneline", True):
        argv.append("--oneline")
    if args.get("graph", True):
        argv.append("--graph")
    if args.get("decorate", True):
        argv.append("--decorate")
    if args.get("all"):
        argv.append("--all")
    max_count = args.get("max_count")
    if max_count:
        _extend(argv, ["-n", str(max_count)])
    return argv


def _map_branch(args: Dict[str, Any]) -> List[str]:
    if args.get("create"):
        return ["branch", str(args["create"])]
    if args.get("delete"):
        flag = "-D" if args.get("force") else "-d"
        return ["branch", flag, str(args["delete"])]
    argv = ["branch"]
    if args.get("verbose", True):
        argv.append("-vv")
    return argv


def _map_switch(args: Dict[str, Any]) -> List[str]:
    branch = args.get("branch")
    if not branch:
        raise ValueError("switch requires branch")
    argv: List[str] = ["switch", str(branch)]
    if args.get("create"):
        argv.insert(1, "-c")
    return argv


def _map_tag(args: Dict[str, Any]) -> List[str]:
    if args.get("delete"):
        return ["tag", "-d", str(args["delete"])]
    if args.get("list", True) and not args.get("name"):
        return ["tag"]
    name = args.get("name")
    if not name:
        raise ValueError("tag requires name when not listing")
    if args.get("annotate"):
        message = args.get("message")
        if not message:
            raise ValueError("annotated tag requires message")
        return ["tag", "-a", str(name), "-m", message]
    return ["tag", str(name)]


def _map_reset(args: Dict[str, Any], allow_destructive: bool) -> List[str]:
    mode = str(args.get("mode", "mixed"))
    target = str(args.get("target", "HEAD"))
    if mode == "hard":
        _ensure_safe(True, allow_destructive, "reset --hard")
    return ["reset", f"--{mode}", target]


def _map_revert(args: Dict[str, Any]) -> List[str]:
    commit = args.get("commit")
    if not commit:
        raise ValueError("revert requires commit hash")
    argv: List[str] = ["revert", str(commit)]
    if args.get("no_edit", True):
        argv.append("--no-edit")
    return argv


def _map_clean(args: Dict[str, Any], allow_destructive: bool) -> List[str]:
    force = bool(args.get("force"))
    dirs = bool(args.get("dirs"))
    _ensure_safe(force or dirs, allow_destructive, "clean -fd")
    argv = ["clean"]
    if force:
        argv.append("-f")
    if dirs:
        argv.append("-d")
    if args.get("interactive"):
        argv.append("-i")
    return argv


Mapper = Callable[[Dict[str, Any], bool], List[str]]

_MAP: Dict[Cmd, Mapper] = {
    Cmd.status: lambda args, allow: _map_status(args),
    Cmd.add: lambda args, allow: _map_add(args),
    Cmd.commit: lambda args, allow: _map_commit(args),
    Cmd.pull: lambda args, allow: _map_pull(args),
    Cmd.push: _map_push,
    Cmd.fetch: lambda args, allow: _map_fetch(args),
    Cmd.merge: lambda args, allow: _map_merge(args),
    Cmd.rebase: lambda args, allow: _map_rebase(args),
    Cmd.diff: lambda args, allow: _map_diff(args),
    Cmd.log: lambda args, allow: _map_log(args),
    Cmd.branch: lambda args, allow: _map_branch(args),
    Cmd.switch: lambda args, allow: _map_switch(args),
    Cmd.tag: lambda args, allow: _map_tag(args),
    Cmd.reset: _map_reset,
    Cmd.revert: lambda args, allow: _map_revert(args),
    Cmd.clean: _map_clean,
}


def _run_git(repo: str, argv: List[str], timeout: int) -> Dict[str, Any]:
    """Execute Git and capture output."""

    proc = subprocess.run(
        ["git", *argv],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


@server.tool()
def git(
    repo_path: str,
    cmd: str,
    args: Dict[str, Any] | None = None,
    dry_run: bool = False,
    allow_destructive: bool = False,
    timeout_sec: int = 120,
) -> str:
    """Run a controlled Git command."""

    payload = GitInput(
        repo_path=repo_path,
        cmd=Cmd(cmd),
        args=args or {},
        dry_run=dry_run,
        allow_destructive=allow_destructive,
        timeout_sec=timeout_sec,
    )

    mapper = _MAP[payload.cmd]
    argv = mapper(payload.args, payload.allow_destructive)

    if dry_run and payload.cmd in {Cmd.commit, Cmd.merge, Cmd.reset, Cmd.revert, Cmd.clean}:
        command_str = "git " + " ".join(shlex.quote(part) for part in argv)
        return json.dumps(
            {
                "exit_code": 0,
                "stdout": f"DRY-RUN: {command_str}",
                "stderr": "",
            }
        )

    result = _run_git(payload.repo_path, argv, payload.timeout_sec)
    return json.dumps(result)


__all__ = ["app", "server", "git", "GitInput", "Cmd"]


class FlowAction(str, Enum):
    """Supported git flow automation actions."""

    generate_commit_message = "generate_commit_message"


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
    provider: FlowProvider = FlowProvider.opengpt
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    user_prompt: Optional[str] = None
    diff_scope: DiffScope = DiffScope.staged
    diff_target: Optional[str] = None
    include_readme: bool = True
    max_readme_chars: int = 4000
    max_diff_chars: int = 8000
    extra_context: Optional[str] = None
    temperature: float = 0.2
    timeout_sec: int = 120

    @validator("repo_path")
    def _repo_exists(cls, value: str) -> str:
        return GitInput._repo_exists(value)

    @validator("temperature")
    def _validate_temperature(cls, value: float) -> float:
        if not 0.0 <= value <= 2.0:
            raise ValueError("temperature must be between 0 and 2")
        return value

    @validator("max_readme_chars", "max_diff_chars")
    def _positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("maximum lengths must be positive")
        return value

    @validator("timeout_sec")
    def _positive_timeout(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("timeout_sec must be positive")
        return value


_DEFAULT_SYSTEM_PROMPT = (
    "You are an experienced software engineer who writes Conventional Commits."
)
_DEFAULT_USER_PROMPT = (
    "请基于以下项目上下文与 diff，生成一条简洁的 Conventional Commit 信息，并给出简短的正文说明。"
)


_PROVIDER_CONFIG: Dict[FlowProvider, Dict[str, Optional[str]]] = {
    FlowProvider.opengpt: {
        "api_key_env": "OPENGPT_API_KEY",
        "url_env": "OPENGPT_API_URL",
        "default_url": "https://api.opengpt.com/v1/chat/completions",
        "model_env": "OPENGPT_MODEL",
        "default_model": "gpt-4.1-mini",
        "auth_header": "Authorization",
        "auth_scheme": "Bearer",
    },
    FlowProvider.deepseek: {
        "api_key_env": "DEEPSEEK_API_KEY",
        "url_env": "DEEPSEEK_API_URL",
        "default_url": "https://api.deepseek.com/v1/chat/completions",
        "model_env": "DEEPSEEK_MODEL",
        "default_model": "deepseek-chat",
        "auth_header": "Authorization",
        "auth_scheme": "Bearer",
    },
}


def _find_readme(repo_path: str) -> Optional[str]:
    """Locate a README file within the repository root."""

    for name in ("README.md", "README.MD", "README.txt", "README"):
        candidate = os.path.join(repo_path, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def _read_file(path: str, limit: int) -> str:
    """Read a file and truncate to the provided character limit."""

    try:
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()
    except UnicodeDecodeError:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            content = handle.read()
    return content[:limit]


def _collect_diff(payload: GitFlowInput) -> str:
    """Capture git diff output based on the requested scope."""

    if payload.diff_scope is DiffScope.staged:
        argv = ["diff", "--cached"]
    elif payload.diff_scope is DiffScope.workspace:
        argv = ["diff"]
    else:
        target = payload.diff_target or "HEAD"
        argv = ["diff", str(target)]

    result = _run_git(payload.repo_path, argv, timeout=payload.timeout_sec)
    if result["exit_code"] != 0:
        raise RuntimeError(result["stderr"] or "failed to collect diff")
    return str(result["stdout"])[: payload.max_diff_chars]


def _build_context(payload: GitFlowInput) -> Dict[str, str]:
    """Gather README and diff context for the prompt."""

    readme_content = ""
    if payload.include_readme:
        readme_path = _find_readme(payload.repo_path)
        if readme_path:
            readme_content = _read_file(readme_path, payload.max_readme_chars)

    diff_content = _collect_diff(payload)

    return {
        "readme": readme_content,
        "diff": diff_content,
        "extra": payload.extra_context or "",
    }


def _call_provider(
    payload: GitFlowInput,
    messages: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Send prompt to the configured provider and parse the response."""

    config = _PROVIDER_CONFIG[payload.provider]
    api_key_env = config["api_key_env"]
    assert api_key_env is not None
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise RuntimeError(f"missing API key: set {api_key_env}")

    url_env = config["url_env"]
    url = os.environ.get(url_env) if url_env else None
    if not url:
        url = config.get("default_url")
    if not url:
        raise RuntimeError("no API endpoint configured")

    model_env = config.get("model_env")
    chosen_model = payload.model or (os.environ.get(model_env) if model_env else None) or config.get("default_model")
    if not chosen_model:
        raise RuntimeError("no model configured")

    body = json.dumps(
        {
            "model": chosen_model,
            "messages": messages,
            "temperature": payload.temperature,
        }
    ).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    header = config.get("auth_header") or "Authorization"
    scheme = config.get("auth_scheme") or "Bearer"
    headers[header] = f"{scheme} {api_key}" if scheme else api_key

    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"provider error: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"provider unreachable: {exc.reason}") from exc

    data = json.loads(raw)
    content = ""
    if isinstance(data, dict):
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    content = str(message.get("content") or "").strip()
    return {"content": content, "raw": data, "model": chosen_model, "url": url}


def _format_prompt(payload: GitFlowInput, context: Dict[str, str]) -> List[Dict[str, str]]:
    """Assemble chat messages for the provider."""

    system = payload.system_prompt or _DEFAULT_SYSTEM_PROMPT
    user = payload.user_prompt or _DEFAULT_USER_PROMPT

    segments = [user]
    if context["extra"]:
        segments.append("# 额外上下文\n" + context["extra"].strip())
    if context["readme"]:
        segments.append("# 项目 README 摘要\n" + context["readme"].strip())
    if context["diff"]:
        segments.append(f"# Git Diff（{payload.diff_scope.value}）\n" + context["diff"].strip())

    user_message = "\n\n".join(segment for segment in segments if segment)

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message},
    ]


def _handle_git_flow(payload: GitFlowInput) -> Dict[str, Any]:
    """Execute the requested git_flow automation."""

    if payload.action is not FlowAction.generate_commit_message:
        raise ValueError(f"unsupported git_flow action: {payload.action}")

    context = _build_context(payload)
    messages = _format_prompt(payload, context)
    response = _call_provider(payload, messages)

    if not response["content"]:
        raise RuntimeError("provider returned empty content")

    return {
        "exit_code": 0,
        "stdout": response["content"],
        "stderr": "",
        "details": {
            "provider": payload.provider.value,
            "model": response["model"],
            "diff_scope": payload.diff_scope.value,
            "endpoint": response["url"],
        },
    }


@server.tool()
def git_flow(**kwargs: Any) -> str:
    """Expose git workflow automations powered by external LLM providers."""

    payload = GitFlowInput(**kwargs)
    try:
        result = _handle_git_flow(payload)
    except Exception as exc:  # noqa: BLE001 - surfaced to clients as structured error
        return json.dumps({"exit_code": 1, "stdout": "", "stderr": str(exc)})
    return json.dumps(result)


__all__.extend(["git_flow", "GitFlowInput", "FlowProvider", "DiffScope", "FlowAction"])
