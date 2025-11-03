"""Implementation of git command execution."""
import json
import shlex
import subprocess
from typing import Any, Callable, Dict, Iterable, List

from .models import Cmd, GitInput


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
    if args.get("stat"):
        argv.append("--stat")
    unified = args.get("unified")
    if unified is not None:
        argv.extend(["-U", str(unified)])
    if args.get("color_words"):
        argv.append("--color-words")
    pathspec = args.get("paths")
    if isinstance(pathspec, (list, tuple)):
        _extend(argv, [str(item) for item in pathspec])
    elif pathspec:
        argv.append(str(pathspec))
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


def _map_remote(args: Dict[str, Any], allow_destructive: bool) -> List[str]:
    action = args.get("action", "list")
    if action == "list":
        argv: List[str] = ["remote"]
        if args.get("verbose", True):
            argv.append("-v")
        return argv

    name = args.get("name")
    if not name:
        raise ValueError("remote action requires name")

    if action == "add":
        url = args.get("url")
        if not url:
            raise ValueError("remote add requires url")
        return ["remote", "add", str(name), str(url)]

    if action in {"remove", "rm"}:
        return ["remote", "remove", str(name)]

    if action == "set_url":
        url = args.get("url")
        if not url:
            raise ValueError("remote set_url requires url")
        return ["remote", "set-url", str(name), str(url)]

    if action == "rename":
        new_name = args.get("new_name")
        if not new_name:
            raise ValueError("remote rename requires new_name")
        return ["remote", "rename", str(name), str(new_name)]

    if action == "prune":
        return ["remote", "prune", str(name)]

    raise ValueError(f"unsupported remote action: {action}")


def _map_stash(args: Dict[str, Any], allow_destructive: bool) -> List[str]:
    action = args.get("action", "list")

    if action == "list":
        return ["stash", "list"]

    if action == "push":
        argv: List[str] = ["stash", "push"]
        if args.get("include_untracked"):
            argv.append("--include-untracked")
        if args.get("all"):
            argv.append("--all")
        message = args.get("message")
        if message:
            _extend(argv, ["-m", str(message)])
        pathspec = args.get("pathspec")
        if pathspec:
            if isinstance(pathspec, (list, tuple)):
                _extend(argv, [str(item) for item in pathspec])
            else:
                argv.append(str(pathspec))
        return argv

    if action in {"apply", "pop", "drop"}:
        ref = args.get("ref")
        argv = ["stash", action]
        if action == "drop":
            _ensure_safe(True, allow_destructive, "stash drop")
        if ref:
            argv.append(str(ref))
        return argv

    if action == "clear":
        _ensure_safe(True, allow_destructive, "stash clear")
        return ["stash", "clear"]

    raise ValueError(f"unsupported stash action: {action}")


def _map_submodule(args: Dict[str, Any], allow_destructive: bool) -> List[str]:
    action = args.get("action", "update")

    if action == "update":
        argv: List[str] = ["submodule", "update"]
        if args.get("init", True):
            argv.append("--init")
        if args.get("recursive", True):
            argv.append("--recursive")
        path = args.get("path")
        if path:
            argv.append(str(path))
        return argv

    if action == "sync":
        argv = ["submodule", "sync"]
        if args.get("recursive", True):
            argv.append("--recursive")
        path = args.get("path")
        if path:
            argv.append(str(path))
        return argv

    if action == "status":
        argv = ["submodule", "status"]
        if args.get("recursive"):
            argv.append("--recursive")
        return argv

    raise ValueError(f"unsupported submodule action: {action}")


def _map_cherry_pick(args: Dict[str, Any], allow_destructive: bool) -> List[str]:
    if args.get("continue"):
        return ["cherry-pick", "--continue"]
    if args.get("abort"):
        return ["cherry-pick", "--abort"]
    if args.get("quit"):
        return ["cherry-pick", "--quit"]
    if args.get("skip"):
        return ["cherry-pick", "--skip"]

    commits = args.get("commits") or args.get("commit")
    if not commits:
        raise ValueError("cherry-pick requires commit hash or list of hashes")

    argv: List[str] = ["cherry-pick"]
    if args.get("no_commit"):
        argv.append("--no-commit")
    if args.get("signoff"):
        argv.append("--signoff")
    if args.get("edit"):
        argv.append("--edit")

    if isinstance(commits, (list, tuple)):
        if not commits:
            raise ValueError("cherry-pick commits list cannot be empty")
        _extend(argv, [str(item) for item in commits])
    else:
        argv.append(str(commits))

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
    Cmd.remote: lambda args, allow: _map_remote(args, allow),
    Cmd.stash: lambda args, allow: _map_stash(args, allow),
    Cmd.submodule: lambda args, allow: _map_submodule(args, allow),
    Cmd.cherry_pick: lambda args, allow: _map_cherry_pick(args, allow),
}


def run_git(repo: str, argv: List[str], timeout: int) -> Dict[str, Any]:
    """Execute Git and capture output.
    
    Args:
        repo: Repository path
        argv: Git command arguments
        timeout: Timeout in seconds
        
    Returns:
        Dict with exit_code, stdout, stderr
    """

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


def execute_git_command(payload: GitInput) -> str:
    """Execute a git command and return JSON result.
    
    Args:
        payload: Validated git command input
        
    Returns:
        JSON string with exit_code, stdout, stderr
    """
    try:
        mapper = _MAP[payload.cmd]
        argv = mapper(payload.args, payload.allow_destructive)

        if payload.dry_run and payload.cmd in {Cmd.commit, Cmd.merge, Cmd.reset, Cmd.revert, Cmd.clean}:
            command_str = "git " + " ".join(shlex.quote(part) for part in argv)
            return json.dumps(
                {
                    "exit_code": 0,
                    "stdout": f"DRY-RUN: {command_str}",
                    "stderr": "",
                }
            )

        result = run_git(payload.repo_path, argv, payload.timeout_sec)
        return json.dumps(result)
    except ValueError as e:
        # 参数验证错误
        return json.dumps({
            "exit_code": 1,
            "stdout": "",
            "stderr": f"参数验证错误: {str(e)}",
        })
    except KeyError as e:
        # 命令映射错误
        return json.dumps({
            "exit_code": 1,
            "stdout": "",
            "stderr": f"不支持的 Git 命令: {str(e)}",
        })
    except subprocess.TimeoutExpired:
        # 执行超时
        return json.dumps({
            "exit_code": 124,
            "stdout": "",
            "stderr": f"Git 命令执行超时（超过 {payload.timeout_sec} 秒）",
        })
    except Exception as e:  # noqa: BLE001 - 捕获所有其他异常并返回给客户端
        # 其他未预期的错误
        import traceback
        error_details = traceback.format_exc()
        return json.dumps({
            "exit_code": 1,
            "stdout": "",
            "stderr": f"执行错误: {type(e).__name__}: {str(e)}\n详细信息: {error_details[-500:]}",  # 只返回最后500字符
        })

