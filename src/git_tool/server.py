"""MCP server exposing a structured Git tool.

The implementation follows the guidelines laid out in ``guide.md`` and the
accompanying documentation under ``docs/``.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List

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
