# -*- coding: utf-8 -*-
"""
quality/checks/format_check.py —— M5-17 前置格式检查

只报格式类问题，全部 autofixable=True（供 AutoFixer 消费）。绝不触碰
数字/名称/证书/【待确认】等任何内容——格式修复只是删除行尾空白、
补标题空格、压缩连续空行、对齐表格管道。

四类格式缺陷（基线正常生成应零触发）：
1. 行尾空白：line != line.rstrip()
2. 标题缺空格：以 "##" 开头且第 3 字符非 #/空格/行尾（"##标题"）
3. 连续空行 ≥2（"\n\n\n"）
4. 表格管道数不一致：同一表格块内数据行管道数 != 块首行
   （跳过分隔行 |---|---| 与转义管道 \\|）

口径：只扫内容本身（fact-zone + 其它章节 content_md）。回显表
（CH-08/CH-05-4）为自动生成的三列响应表，首列单元格含换行、管道
天然不齐（\\| 转义），排除——它们不是手写格式缺陷。
"""
from __future__ import annotations

import re
from typing import Optional

from ..models import CheckContext, IssueType, QualityIssue, Severity

_SEP_RE = re.compile(r"^\|[\s:\-|]+\|$")     # 表格分隔行 |---|---|
_HEADING_RE = re.compile(r"^##[^#\s]")
_ECHO_ZONES = {"CH-08", "CH-05-4"}


def check_format(ctx: CheckContext) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    for section in ctx.sections:
        sid = section.get("section_id") or ""
        if sid in _ECHO_ZONES:
            continue                      # 回显表自动生成，格式归组装器负责
        content = section.get("content_md") or ""
        if not content:
            continue
        issues += _trailing_whitespace(content, sid)
        issues += _heading_no_space(content, sid)
        issues += _consecutive_blank_lines(content, sid)
        issues += _table_pipe_mismatch(content, sid)
    return issues


def _trailing_whitespace(content: str, sid: str) -> list[QualityIssue]:
    out = []
    for i, line in enumerate(content.split("\n"), 1):
        if line != line.rstrip():
            out.append(_fmt_issue(
                sid, "行尾存在空白字符（不可见且易致排版错乱）",
                {"line": i}, "删除行尾空白"))
    return out


def _heading_no_space(content: str, sid: str) -> list[QualityIssue]:
    out = []
    for i, line in enumerate(content.split("\n"), 1):
        if _HEADING_RE.match(line):
            out.append(_fmt_issue(
                sid, f"标题「{line.strip()[:20]}」在 ## 与标题之间缺空格",
                {"line": i}, '补为 "## 标题"'))
    return out


def _consecutive_blank_lines(content: str, sid: str) -> list[QualityIssue]:
    out = []
    if "\n\n\n" in content:
        out.append(_fmt_issue(sid, "存在连续多个空行（≥2 个空行）",
                              {}, "压缩为单个空行"))
    return out


def _table_pipe_mismatch(content: str, sid: str) -> list[QualityIssue]:
    out = []
    block_first: Optional[int] = None
    for i, line in enumerate(content.split("\n"), 1):
        if not line.startswith("|"):
            block_first = None
            continue
        if "\\|" in line:
            block_first = None
            continue                      # 转义管道块 → 跳过
        if _SEP_RE.match(line):
            continue
        count = line.count("|")
        if block_first is None:
            block_first = count
        elif count != block_first:
            out.append(_fmt_issue(
                sid, f"表格第 {i} 行管道数 {count} ≠ 块首行 {block_first}",
                {"line": i, "pipes": count, "expected": block_first},
                "补齐缺失的表格列分隔符"))
    return out


def _fmt_issue(sid: str, message: str, ref: dict, fix: str) -> QualityIssue:
    return QualityIssue(
        section_id=sid, issue_type=IssueType.FORMAT_ERROR,
        severity=Severity.WARNING, message=message,
        source_refs=[{"section": sid, **ref}],
        suggestion=fix, autofixable=True)


__all__ = ["check_format"]
