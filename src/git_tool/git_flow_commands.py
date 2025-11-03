"""Implementation of git_flow command execution."""
import json
import os
import subprocess
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from .git_combos import Combo, get_combo
from .git_commands import run_git
from .models import DiffScope, FlowAction, FlowProvider, GitFlowInput
from .prompt_profiles import PROMPT_PROFILE_TEMPLATES, PromptProfile


_DEFAULT_SYSTEM_PROMPT = (
    "You are an experienced software engineer who writes Conventional Commits."
)
_DEFAULT_USER_PROMPT = (
    "请基于以下项目上下文与 diff，生成一条简洁的 Conventional Commit 信息，并给出简短的正文说明。"
)

_DEFAULT_COMBO_SYSTEM_PROMPT = (
    "You are a senior Git expert who designs safe, reproducible command plans."
)
_DEFAULT_COMBO_USER_PROMPT = (
    "请基于给定的 Git 组合命令模板，为当前仓库生成执行说明：\n"
    "1. 概述适用场景与前置条件。\n"
    "2. 逐步列出每条 git 命令，并解释目的与注意事项。\n"
    "3. 如包含占位符，请提醒用户替换并给出建议值。\n"
    "4. 若提供了 README 摘要或 diff，请结合说明风险与验证步骤。\n"
    "5. 最后输出可直接复制的脚本片段。"
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


def _resolve_prompts(
    payload: GitFlowInput,
    *,
    combo: bool = False,
) -> Tuple[str, str]:
    """Determine system/user prompt pair for the request."""

    profile = payload.prompt_profile
    # Handle both string and PromptProfile enum
    if profile:
        if isinstance(profile, str):
            try:
                profile = PromptProfile(profile)
            except ValueError:
                profile = None
        template = PROMPT_PROFILE_TEMPLATES.get(profile)
        if template:
            system = payload.system_prompt or template["system"]
            user = payload.user_prompt or template["user"]
            return system, user

    default_system = _DEFAULT_COMBO_SYSTEM_PROMPT if combo else _DEFAULT_SYSTEM_PROMPT
    default_user = _DEFAULT_COMBO_USER_PROMPT if combo else _DEFAULT_USER_PROMPT

    system = payload.system_prompt or default_system
    user = payload.user_prompt or default_user

    return system, user


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

    if not payload.include_diff:
        return ""

    if payload.diff_scope is DiffScope.staged:
        argv = ["diff", "--cached"]
    elif payload.diff_scope is DiffScope.workspace:
        argv = ["diff"]
    else:
        target = payload.diff_target or "HEAD"
        argv = ["diff", str(target)]

    result = run_git(payload.repo_path, argv, timeout=payload.timeout_sec)
    if result["exit_code"] != 0:
        raise RuntimeError(result["stderr"] or "failed to collect diff")
    return str(result["stdout"])[: payload.max_diff_chars]


def _collect_status(payload: GitFlowInput) -> str:
    """Capture a concise git status for additional context."""

    if not payload.include_status:
        return ""

    result = run_git(payload.repo_path, ["status", "-sb"], timeout=payload.timeout_sec)
    if result["exit_code"] != 0:
        raise RuntimeError(result["stderr"] or "failed to collect status")
    return str(result["stdout"])[: payload.max_status_chars]


def _build_context(payload: GitFlowInput) -> Dict[str, str]:
    """Gather README and diff context for the prompt."""

    readme_content = ""
    if payload.include_readme:
        readme_path = _find_readme(payload.repo_path)
        if readme_path:
            readme_content = _read_file(readme_path, payload.max_readme_chars)

    diff_content = _collect_diff(payload)
    status_content = _collect_status(payload)

    return {
        "readme": readme_content,
        "diff": diff_content,
        "status": status_content,
        "extra": payload.extra_context or "",
    }


def _apply_replacements(text: str, replacements: Dict[str, str]) -> str:
    """Replace angle-bracket placeholders using provided replacements."""

    result = text
    for key, value in replacements.items():
        placeholder = f"<{key}>"
        result = result.replace(placeholder, value)
    return result


def _render_combo_details(combo: Combo, replacements: Dict[str, str]) -> str:
    """Format combo metadata for prompt injection."""

    summary = _apply_replacements(combo["summary"], replacements)
    parameters = _apply_replacements(combo["parameters"], replacements)
    notes = _apply_replacements(combo["notes"], replacements)

    steps = [
        f"{index + 1}. {_apply_replacements(step, replacements)}"
        for index, step in enumerate(combo["steps"])
    ]
    steps_block = "\n".join(steps)

    script = _apply_replacements(combo["script"], replacements).strip()

    return (
        f"名称：{combo['name']}\n"
        f"用途：{summary}\n"
        f"参数建议：{parameters}\n"
        f"执行步骤：\n{steps_block}\n\n"
        f"脚本模板：\n```bash\n{script}\n```\n\n"
        f"补充说明：{notes}"
    )


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

    system, user = _resolve_prompts(payload)

    segments = [user]
    if context["extra"]:
        segments.append("# 额外上下文\n" + context["extra"].strip())
    if context["readme"]:
        segments.append("# 项目 README 摘要\n" + context["readme"].strip())
    if context["status"]:
        segments.append("# Git 状态\n" + context["status"].strip())
    if context["diff"]:
        segments.append(f"# Git Diff（{payload.diff_scope.value}）\n" + context["diff"].strip())

    user_message = "\n\n".join(segment for segment in segments if segment)

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message},
    ]


def _format_combo_prompt(
    payload: GitFlowInput,
    context: Dict[str, str],
    combo: Combo,
) -> List[Dict[str, str]]:
    """Build chat messages for combo execution planning."""

    system, user = _resolve_prompts(payload, combo=True)

    combo_details = _render_combo_details(combo, payload.combo_replacements)

    segments = [user, "# 组合命令模板\n" + combo_details]
    if context["extra"]:
        segments.append("# 额外上下文\n" + context["extra"].strip())
    if context["readme"]:
        segments.append("# 项目 README 摘要\n" + context["readme"].strip())
    if context["status"]:
        segments.append("# Git 状态\n" + context["status"].strip())
    if context["diff"]:
        segments.append(f"# Git Diff（{payload.diff_scope.value}）\n" + context["diff"].strip())

    user_message = "\n\n".join(segment for segment in segments if segment)

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message},
    ]


def _handle_git_flow(payload: GitFlowInput) -> Dict[str, Any]:
    """Execute the requested git_flow automation."""

    if payload.action is FlowAction.generate_commit_message:
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

    if payload.action is FlowAction.combo_plan:
        assert payload.combo_name is not None  # validated earlier
        combo = get_combo(payload.combo_name)
        context = _build_context(payload)
        messages = _format_combo_prompt(payload, context, combo)
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
                "combo": combo["name"],
            },
        }

    raise ValueError(f"unsupported git_flow action: {payload.action}")


def execute_git_flow_command(
    repo_path: str,
    action: str,
    provider: str,
    model: Optional[str],
    system_prompt: Optional[str],
    user_prompt: Optional[str],
    prompt_profile: Optional[str],
    diff_scope: str,
    diff_target: Optional[str],
    include_readme: bool,
    include_diff: bool,
    include_status: bool,
    max_readme_chars: int,
    max_diff_chars: int,
    max_status_chars: int,
    extra_context: Optional[str],
    temperature: float,
    timeout_sec: int,
    combo_name: Optional[str],
    combo_replacements: Optional[Dict[str, str]],
) -> str:
    """Execute a git_flow command and return JSON result.
    
    Args:
        All parameters from the MCP tool interface
        
    Returns:
        JSON string with exit_code, stdout, stderr, and details
    """
    # 参数验证和转换
    try:
        action_enum = FlowAction(action)
    except ValueError:
        return json.dumps({
            "exit_code": 1,
            "stdout": "",
            "stderr": f"不支持的操作类型: {action}。支持的操作: generate_commit_message, combo_plan",
        })

    try:
        provider_enum = FlowProvider(provider)
    except ValueError:
        return json.dumps({
            "exit_code": 1,
            "stdout": "",
            "stderr": f"不支持的提供者: {provider}。支持的提供者: opengpt, deepseek",
        })

    try:
        diff_scope_enum = DiffScope(diff_scope)
    except ValueError:
        return json.dumps({
            "exit_code": 1,
            "stdout": "",
            "stderr": f"不支持的 diff_scope: {diff_scope}。支持的选项: staged, workspace, head",
        })

    prompt_profile_enum = None
    if prompt_profile:
        try:
            prompt_profile_enum = PromptProfile(prompt_profile)
        except ValueError:
            return json.dumps({
                "exit_code": 1,
                "stdout": "",
                "stderr": f"不支持的 prompt_profile: {prompt_profile}。支持的配置: software_engineering, devops, product_analysis, documentation, data_analysis",
            })

    # 创建输入对象（可能抛出 ValueError）
    try:
        payload = GitFlowInput(
            repo_path=repo_path,
            action=action_enum,
            provider=provider_enum,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            prompt_profile=prompt_profile,
            diff_scope=diff_scope_enum,
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
        # 转换 prompt_profile 字符串为枚举并附加到对象（用于后续处理）
        # 使用 object.__setattr__ 来绕过 Pydantic 的字段验证
        if prompt_profile_enum:
            object.__setattr__(payload, 'prompt_profile', prompt_profile_enum)
    except ValueError as e:
        # 参数验证错误
        return json.dumps({
            "exit_code": 1,
            "stdout": "",
            "stderr": f"参数验证错误: {str(e)}",
        })

    # 执行 git_flow 操作
    try:
        result = _handle_git_flow(payload)
    except RuntimeError as e:
        # RuntimeError 通常来自 API 调用失败、Git 命令失败等
        error_msg = str(e)
        if "missing API key" in error_msg:
            return json.dumps({
                "exit_code": 1,
                "stdout": "",
                "stderr": f"API 密钥未设置: {error_msg}。请设置相应的环境变量（如 DEEPSEEK_API_KEY 或 OPENGPT_API_KEY）",
            })
        elif "provider error" in error_msg or "provider unreachable" in error_msg:
            return json.dumps({
                "exit_code": 1,
                "stdout": "",
                "stderr": f"LLM 服务错误: {error_msg}",
            })
        elif "failed to collect" in error_msg or "not a git repository" in error_msg:
            return json.dumps({
                "exit_code": 1,
                "stdout": "",
                "stderr": f"Git 操作错误: {error_msg}",
            })
        else:
            return json.dumps({
                "exit_code": 1,
                "stdout": "",
                "stderr": f"执行错误: {error_msg}",
            })
    except KeyError as e:
        # 通常来自 get_combo 找不到指定的 combo
        return json.dumps({
            "exit_code": 1,
            "stdout": "",
            "stderr": f"找不到指定的组合命令模板: {str(e)}",
        })
    except urllib.error.HTTPError as e:
        # HTTP 错误
        return json.dumps({
            "exit_code": 1,
            "stdout": "",
            "stderr": f"HTTP 请求错误: {e.code} {e.reason}",
        })
    except urllib.error.URLError as e:
        # 网络连接错误
        return json.dumps({
            "exit_code": 1,
            "stdout": "",
            "stderr": f"网络连接错误: {e.reason}",
        })
    except subprocess.TimeoutExpired:
        # 执行超时
        return json.dumps({
            "exit_code": 124,
            "stdout": "",
            "stderr": f"操作超时（超过 {timeout_sec} 秒）",
        })
    except Exception as e:  # noqa: BLE001 - 捕获所有其他异常
        # 其他未预期的错误
        import traceback
        error_details = traceback.format_exc()
        return json.dumps({
            "exit_code": 1,
            "stdout": "",
            "stderr": f"未预期的错误: {type(e).__name__}: {str(e)}\n详细信息: {error_details[-500:]}",
        })

    return json.dumps(result)

