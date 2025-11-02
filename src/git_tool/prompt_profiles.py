"""Predefined prompt profiles for git_flow automation."""

from __future__ import annotations

from enum import Enum
from typing import Dict


class PromptProfile(str, Enum):
    """Named prompt presets that can be injected into git_flow requests."""

    software_engineering = "software_engineering"
    devops = "devops"
    product_analysis = "product_analysis"
    documentation = "documentation"
    data_analysis = "data_analysis"


PromptTemplate = Dict[str, str]


PROMPT_PROFILE_TEMPLATES: Dict[PromptProfile, PromptTemplate] = {
    PromptProfile.software_engineering: {
        "system": (
            "You are a senior software engineer specializing in Git-based workflows. "
            "Produce safe, review-ready outputs, call out risks, and respect repository "
            "conventions surfaced in the context."
        ),
        "user": (
            "项目概览：\n"
            "{{repo_summary}}\n\n"
            "任务目标：\n"
            "{{task_description}}\n\n"
            "相关变更：\n"
            "{{diff_snippet}}\n\n"
            "风险 / 兼容性提示：\n"
            "{{risk_notice}}\n\n"
            "请基于以上信息，输出 {{desired_output}}，并确保：\n"
            "1. 给出必要的代码上下文解释；\n"
            "2. 指出可能的副作用与测试建议；\n"
            "3. 若发现不一致或潜在问题，请明确标注并提出修正思路。"
        ),
    },
    PromptProfile.devops: {
        "system": (
            "You are a DevOps specialist focused on reliable delivery, CI/CD, and "
            "infrastructure automation. Emphasize reproducibility, rollback safety, "
            "and observability practices."
        ),
        "user": (
            "当前服务与仓库信息：\n"
            "{{repo_summary}}\n\n"
            "运维 / 部署任务：\n"
            "{{task_description}}\n\n"
            "配置或脚本差异：\n"
            "{{diff_snippet}}\n\n"
            "约束与风险：\n"
            "{{risk_notice}}\n\n"
            "请产出 {{desired_output}}，需包含：\n"
            "- 环境或流水线的更新步骤；\n"
            "- 监控与验证建议；\n"
            "- 回滚策略或故障预案。"
        ),
    },
    PromptProfile.product_analysis: {
        "system": (
            "You are a product strategist skilled at translating business requirements "
            "into actionable engineering guidance. Balance user value, feasibility, "
            "and measurable outcomes."
        ),
        "user": (
            "产品背景：\n"
            "{{repo_summary}}\n\n"
            "当前需求与痛点：\n"
            "{{task_description}}\n\n"
            "相关实现或差异：\n"
            "{{diff_snippet}}\n\n"
            "业务限制 / 风险说明：\n"
            "{{risk_notice}}\n\n"
            "请围绕 {{desired_output}} 进行分析，需包含：\n"
            "1. 用户价值与成功指标；\n"
            "2. 方案可行性评估（含依赖与影响范围）；\n"
            "3. 对后续迭代或验证的建议。"
        ),
    },
    PromptProfile.documentation: {
        "system": (
            "You are a technical writer who keeps engineering knowledge bases consistent, "
            "concise, and accessible. Maintain tone alignment with existing documentation."
        ),
        "user": (
            "文档上下文：\n"
            "{{repo_summary}}\n\n"
            "更新目标：\n"
            "{{task_description}}\n\n"
            "内容差异或待整合信息：\n"
            "{{diff_snippet}}\n\n"
            "注意事项：\n"
            "{{risk_notice}}\n\n"
            "请输出 {{desired_output}}，并确保：\n"
            "- 用词统一且符合既有术语；\n"
            "- 给出必要的交叉引用或链接建议；\n"
            "- 标注需要人工确认的部分。"
        ),
    },
    PromptProfile.data_analysis: {
        "system": (
            "You are a data analyst experienced in experimental design, metrics "
            "interpretation, and communicating insights to mixed audiences."
        ),
        "user": (
            "数据集与项目背景：\n"
            "{{repo_summary}}\n\n"
            "分析诉求：\n"
            "{{task_description}}\n\n"
            "代码 / 笔记本差异：\n"
            "{{diff_snippet}}\n\n"
            "潜在风险或数据质量提示：\n"
            "{{risk_notice}}\n\n"
            "请生成 {{desired_output}}，需要：\n"
            "- 概述关键发现与指标波动；\n"
            "- 指出假设、前提条件与可能的偏差；\n"
            "- 给出下一步验证或可视化建议。"
        ),
    },
}


__all__ = ["PromptProfile", "PROMPT_PROFILE_TEMPLATES"]

