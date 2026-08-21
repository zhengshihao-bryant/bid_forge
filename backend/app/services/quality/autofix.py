# -*- coding: utf-8 -*-
"""
quality/autofix.py —— M5-17 格式自动修复（只动格式，绝不碰内容）

    AutoFixer.apply(content_md, issue) → 修复后内容

边界（M5 原则：只检查、不重写）：
- 仅处理 autofixable=True 的 FORMAT_ERROR issue；
- 修复只做格式：删行尾空白 / 标题补空格 / 压缩连续空行 / 表格行补齐管道；
- 绝不修改数字、名称、金额、年限、资质、证书、【待确认】等任何内容。

每条 FORMAT_ERROR 的 source_refs 携带 {section, line}（连续空行无行号），
apply 按 message 前缀分派到对应的行级/全局修复。
"""
from __future__ import annotations

import re
from typing import Optional

from .models import IssueType, QualityIssue

_HEADING_FIX_RE = re.compile(r"^(#+)([^\s#].*)$")
_BLANK_RUN_RE = re.compile(r"\n{3,}")


class AutoFixer:
    """格式问题修复器（幂等：已修复内容再跑不产生新变更）。"""

    def apply(self, content_md: str, issue: QualityIssue) -> str:
        """按 issue 的格式类型返回修复后的内容。非格式 issue → 原样返回。"""
        if issue.issue_type != IssueType.FORMAT_ERROR or not issue.autofixable:
            return content_md
        msg = issue.message
        ref = {k: v for r in issue.source_refs for k, v in r.items()}
        line_no: Optional[int] = ref.get("line")
        lines = content_md.split("\n")

        if msg.startswith("行尾存在空白"):
            if line_no and 1 <= line_no <= len(lines):
                lines[line_no - 1] = lines[line_no - 1].rstrip()
        elif msg.startswith("标题「"):
            if line_no and 1 <= line_no <= len(lines):
                lines[line_no - 1] = _HEADING_FIX_RE.sub(
                    r"\1 \2", lines[line_no - 1])
        elif msg.startswith("存在连续多个空行"):
            return _BLANK_RUN_RE.sub("\n\n", content_md)
        elif msg.startswith("表格第"):
            expected = ref.get("expected")
            if line_no and 1 <= line_no <= len(lines) and expected:
                lines[line_no - 1] = _pad_row(lines[line_no - 1], int(expected))
        return "\n".join(lines)


def _pad_row(line: str, expected_pipes: int) -> str:
    """补齐表格行管道数（追加空单元格），不修改已有单元格内容。"""
    cur = line.count("|")
    if cur >= expected_pipes:
        return line
    return line + "|" * (expected_pipes - cur)


__all__ = ["AutoFixer"]
