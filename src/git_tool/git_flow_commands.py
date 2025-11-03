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
    "You are an experienced software engineer and release maintainer who writes "
    "strict Conventional Commits suitable for changelogs and automated releases.\n"
    "\n"
    "Requirements & Style:\n"
    "1) Use the Conventional Commits format: type(scope)!: subject\n"
    "   - Allowed types: feat, fix, perf, refactor, docs, test, build, ci, chore, style, revert\n"
    "   - scope: short, kebab-case (e.g., core, http-client, build-scripts)\n"
    "   - subject: imperative, concise, <=72 chars, no trailing period\n"
    "   - add '!' only if breaking change\n"
    "2) Body in Chinese, technically precise, 72-col soft wrap where reasonable.\n"
    "3) Always add a '变更统计' section with: files changed, insertions(+), deletions(-), "
    "   and the affected file list.\n"
    "4) Prefer grouping details by: 背景/动机, 技术改动, 向后兼容性/风险, 性能/安全影响, 测试验证, 迁移/回滚方案.\n"
    "5) If any breaking changes exist, include a 'BREAKING CHANGE' footer in English "
    "   and a Chinese 迁移指南说明。\n"
    "6) If issues/PRs are referenced, add a 'Refs' footer with #IDs; if co-authors exist, use "
    "   'Co-authored-by' footers.\n"
    "7) If the diff suggests multiple logical changes, compose a single crisp commit that reflects "
    "   the dominant intent and summarizes secondaries in the body (do not list every hunk verbatim).\n"
    "8) Be consistent, avoid overly generic subjects (e.g., 'update code'), avoid marketing terms.\n"
    "9) Never invent files or numbers—derive stats from the provided context/diff/status. "
    "   If a number cannot be derived, state '未知' explicitly.\n"
)

_DEFAULT_USER_PROMPT = (
    "请基于下面提供的【额外上下文】【项目 README 摘要】【Git 状态】【Git Diff】等信息，"
    "生成一条**严格符合 Conventional Commits 规范**且**技术细节充分**的提交信息。\n"
    "\n"
    "输出要求（必须满足）：\n"
    "1) 标题行（Header）：使用 Conventional Commits 规范，格式为 `type(scope)!: subject`；\n"
    "   - type 仅限：feat、fix、perf、refactor、docs、test、build、ci、chore、style、revert；\n"
    "   - scope 使用短小的 kebab-case（如 core、http-client、build-scripts）；\n"
    "   - subject 使用祈使句，简洁清晰，末尾不加句号，建议 ≤72 字符；\n"
    "   - 若包含破坏性变更，需在 type 后加 `!`。\n"
    "\n"
    "2) 正文（Body）：使用中文，条理清晰，覆盖以下小节（若某项不适用可写“无”）：\n"
    "   - 背景/动机：为何要做本次修改，与 README/需求/缺陷 的关系；\n"
    "   - 技术改动：按模块或文件夹粒度概述主要代码变化（API 变更、数据结构、算法/逻辑、配置/脚本）；\n"
    "   - 向后兼容性/风险：是否有破坏性变更（行为变更、配置/接口移除或调整、默认值改变等）；\n"
    "   - 性能/安全影响：复杂度变化、关键路径影响、潜在安全点（输入校验、边界、并发/资源）；\n"
    "   - 测试与验证：新增/修改的测试点、覆盖的边界、手动验证要点；\n"
    "   - 迁移/回滚方案：如有破坏性变更，给出迁移步骤；如需回滚，说明回滚指令或条件。\n"
    "\n"
    "3) 变更统计（必须有）：\n"
    "   - 文件数：<N files changed>\n"
    "   - 行数：<insertions(+)>, <deletions(-)>\n"
    "   - 影响的文件列表：逐项列出（建议按 churn 降序，最多列出前 20 个，含新增/删除/修改标记），\n"
    "     每项写相对路径，必要时额外标注“新增/删除/重命名/权限变更”。若无法从上下文推断，标注“未知”。\n"
    "\n"
    "4) 底部附注（Footers）：\n"
    "   - 如存在破坏性变更，添加：BREAKING CHANGE: <英文一句话摘要>；\n"
    "   - 关联项：Refs: #<Issue/PR 编号列表>；\n"
    "   - 共同作者：Co-authored-by: 姓名 <邮箱>（如有）。\n"
    "\n"
    "5) 严禁捏造事实：所有统计数据和文件列表必须从【Git 状态】【Git Diff】推导；"
    "   若某项缺失或无法确定，请明确写“未知”。\n"
    "\n"
    "请直接输出最终提交信息文本，使用如下模板：\n"
    "\n"
    "================== 提交模板开始 ==================\n"
    "<type(scope)!: subject>\n"
    "\n"
    "背景/动机：\n"
    "- <要点 1>\n"
    "- <要点 2>\n"
    "\n"
    "技术改动：\n"
    "- <模块/文件夹 级别的改动要点>\n"
    "- <关键 API/数据结构/算法/配置 变化>\n"
    "\n"
    "向后兼容性/风险：\n"
    "- <是否破坏性，影响面，风险点>\n"
    "\n"
    "性能/安全影响：\n"
    "- <复杂度、关键路径、并发/资源、安全检查等>\n"
    "\n"
    "测试与验证：\n"
    "- <新增/修改的用例、边界条件、手测要点>\n"
    "\n"
    "迁移/回滚方案：\n"
    "- <迁移步骤或回滚策略；无则写“无”>\n"
    "\n"
    "变更统计：\n"
    "- files changed: <N>\n"
    "- insertions(+): <N>\n"
    "- deletions(-): <N>\n"
    "- 影响文件：\n"
    "  1) <path/to/fileA>  (新增/修改/删除/重命名…)\n"
    "  2) <path/to/fileB>  (...)\n"
    "  ...（最多 20 项，超出请写“其余共 <M> 个文件，详见 diff”）\n"
    "\n"
    "Refs: #<ID1> #<ID2>\n"
    "Co-authored-by: <Name> <email>\n"
    "BREAKING CHANGE: <英文一句话摘要（如无则省略该行）>\n"
    "================== 提交模板结束 ==================\n"
)

_DEFAULT_COMBO_SYSTEM_PROMPT = (
    "You are a senior Git expert who designs safe, reproducible, and auditable "
    "command plans for complex repository operations.\n"
    "\n"
    "Authoring Rules:\n"
    "1) Always prefer safety: check preconditions (clean working tree, correct repo, protected branches).\n"
    "2) Be explicit: show each git command, its intent, and caveats. No implicit steps.\n"
    "3) Use placeholders clearly (e.g., <src-branch>, <dst-branch>, <remote>, <tag>), "
    "   and instruct users how to substitute.\n"
    "4) Include risk and validation steps; if README or diff is provided, tailor risks/tests accordingly.\n"
    "5) Provide a final, copy-pastable bash script with:\n"
    "   - set -euo pipefail, nounset checks, traps, and idempotent guards\n"
    "   - dry-run flags where possible (e.g., git push --dry-run)\n"
    "   - pre-flight checks and clear rollback guidance\n"
    "6) Never assume; if data is unavailable from context, say '未知' and propose conservative defaults.\n"
)

_DEFAULT_COMBO_USER_PROMPT = (
    "请基于下面提供的【组合命令模板】以及可选的【额外上下文】【项目 README 摘要】"
    "【Git 状态】【Git Diff】，为当前仓库生成一份**安全、可复现、可审计**的执行说明与脚本。\n"
    "\n"
    "请完整输出以下内容：\n"
    "1) 适用场景与前置条件：\n"
    "   - 适用的工作流/分支模型（如 trunk-based、GitFlow 等），所需权限（push/tag/rebase）。\n"
    "   - 前置检查（工作区是否干净、远端是否可达、是否在正确仓库与分支、是否存在保护分支等）。\n"
    "2) 分步命令与解释：\n"
    "   - 逐条列出 git 命令；说明目的、关键参数、可替代方案与注意事项。\n"
    "   - 如可 dry-run，请提供 dry-run 方式；如涉及破坏性操作（reset/rebase/force-push），要醒目标注。\n"
    "3) 占位符替换指南：\n"
    "   - 指明所有占位符（<remote>、<src-branch>、<dst-branch>、<tag>、<sha> 等）。\n"
    "   - 给出建议值与获取方法（例如通过 git rev-parse、git branch -a、git remote -v）。\n"
    "4) 风险与验证：\n"
    "   - 若给出 README 摘要或 Diff，请结合具体变更分析风险点（合并方向、冲突概率、历史改写风险）。\n"
    "   - 给出验证步骤（本地验证、CI 验证、回滚演练），以及可观测指标（构建、测试、关键二进制/文档）。\n"
    "5) 可复制脚本：\n"
    "   - 输出一段可直接复制的 Bash 脚本，包含：严格模式、预检、占位符与默认值、dry-run、实际执行、回滚方案、审计日志。\n"
    "   - 要求脚本对未知信息写明“未知”并提供安全默认；对危险操作加入二次确认（或需显式 --yes）。\n"
    "\n"
    "严禁捏造事实：如无法从上下文确认信息，请明确写“未知”。\n"
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

