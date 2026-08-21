# -*- coding: utf-8 -*-
"""
quality/report.py —— M5-15 质量报告渲染（Markdown / JSON）

    render_json(report, issues)    → 结构化 payload（finalize 落盘 quality-report.json）
    render_markdown(report, issues)→ 人工可读报告（final.md 附于文末）

口径：score 为内部质量指标（5 维扣分公式），报告内显式声明"非准确率"，
避免与模型准确率等外部口径混淆。按严重度分组列出问题、待确认汇总与审计信息。
"""
from __future__ import annotations

import json
from typing import Any

from .models import IssueType, QualityIssue, QualityReport, Severity

_SEVERITY_ORDER = [Severity.CRITICAL, Severity.ERROR, Severity.WARNING,
                   Severity.INFO]


def render_json(report: QualityReport,
                issues: list[QualityIssue]) -> dict[str, Any]:
    """结构化报告（API 与 finalize 落盘共用）。"""
    return {
        "id": report.id,
        "tender_id": report.tender_id,
        "document_version": report.document_version,
        "score": report.score,
        "dimensions": [d.model_dump(mode="json") for d in report.dimensions],
        "counts": report.counts,
        "issue_counts": report.issue_counts,
        "status": report.status,
        "reviewer": report.reviewer,
        "review_time": report.review_time,
        "created_at": report.created_at,
        "summary": report.summary,
        "issues": [i.model_dump(mode="json") for i in issues],
    }


def render_markdown(report: QualityReport,
                    issues: list[QualityIssue]) -> str:
    """Markdown 报告：总分 / 5 维 / 按严重度分组问题 / 待确认汇总 / 审计。"""
    lines: list[str] = [
        f"# 标书质量检查报告（{report.id}）",
        "",
        f"- 招标项目：{report.tender_id}",
        f"- 文档版本：{report.document_version or '—'}",
        f"- 生成时间：{report.created_at}",
        f"- 总分：**{report.score}**（5 维均值）",
        "",
        "> 口径声明：score 为 BidForge 内部质量指标（按问题严重度扣分），"
        "**不是**识别/匹配准确率，仅供投标质检内部参考。",
        "",
        "## 五维得分",
        "",
        "| 维度 | 得分 | 扣分项 |",
        "|------|------|--------|",
    ]
    for d in report.dimensions:
        ded = "；".join(d.deductions) if d.deductions else "—"
        lines.append(f"| {d.name} | {d.score} | {ded} |")

    lines += ["", "## 问题汇总", ""]
    lines.append(f"共 **{len(issues)}** 条："
                 + "，".join(f"{k} {v}" for k, v in report.counts.items()
                            if v) or "无")
    lines += ["", "### 按严重度", ""]
    for sev in _SEVERITY_ORDER:
        group = [i for i in issues if i.severity == sev]
        if not group:
            continue
        lines.append(f"#### {sev.value}（{len(group)}）")
        lines.append("")
        for i in group:
            loc = f"{i.section_id or '—'}"
            req = f" / {i.requirement_id}" if i.requirement_id else ""
            lines.append(f"- **{i.issue_type.value}** `{loc}{req}`：{i.message}")
            if i.suggestion:
                lines.append(f"  - 建议：{i.suggestion}")
        lines.append("")

    pending = [i for i in issues
               if i.issue_type == IssueType.PENDING_CONFIRMATION]
    lines += ["## 待确认清单", ""]
    if pending:
        for i in pending:
            lines.append(f"- [{i.section_id or '—'}] {i.message}")
    else:
        lines.append("无待确认项。")
    lines += ["", "## 审计", ""]
    lines.append(f"- 报告状态：{report.status}")
    if report.reviewer:
        lines.append(f"- 审批人：{report.reviewer}（{report.review_time}）")
    lines.append("")
    return "\n".join(lines)


__all__ = ["render_json", "render_markdown"]
