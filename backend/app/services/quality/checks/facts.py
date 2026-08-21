# -*- coding: utf-8 -*-
"""
quality/checks/facts.py —— M5-02/03/04/05/06 事实检查

数字双层检查 + 资质证书双向校验，全部确定性、无 LLM：

Layer A 成员资格（M5-03 数字可溯源）
    事实区章节每个非结构数字必须 ∈ 允许语料
    （evidences.content + capabilities(name/desc/attributes) +
     canonical_requirements(title/text) + requirements(title/original_text)），
    否则 NUMBER_MISMATCH(WARNING)。已被 Layer B 锚定认领的数字跳过
    （Layer B 已做同口径比较，避免同数双报）。

Layer B 锚定比较（M5-04/05/06 人员/证书/项目/指标）
    extract_claims 把事实区每个数字归属最近指标锚点，产出规范声明
    (metric, lo, hi, unit)；与注册表同指标任一条目命中（容差 ±1%，
    percent 用绝对 0.05 个百分点）即过；全部不中 → 按条目 kind 归类：
    person→PERSON_MISMATCH(ERROR)、project→PROJECT_MISMATCH(ERROR)、
    metric/company→NUMBER_MISMATCH(WARNING)。单位不可换算的声明跳过
    （防误报）。

证书双向校验（M5-05 资质与证书）
    (a) 注册表每张证书名必须出现在事实区正文（缺失 → CERTIFICATE_MISMATCH）；
    (b) 正文中证书样 token（ISO9001/CMMI3/等保三级/PMP…）必须 ∈ 注册表
        （ISO9001→9002 被此抓）；
    (c) 证书 valid_until < as_of → CERTIFICATE_MISMATCH(CRITICAL)；
        valid_until 缺失/非日期 → 跳过并注明"有效期未登记"。
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any, Optional

from ..models import (CheckContext, IssueType, QualityIssue, Severity)
from .scan import extract_claims, iter_numbers, is_structural_number, \
    is_table_first_col_number

# 证书样 token：正文出现即须 ∈ 注册表证书集（防 9002 冒名 9001）
# ISO 证书号 4-5 位（ISO9001/ISO27001/ISO22000），贪心全串匹配防截断
_CERT_TOKEN_RES = [
    re.compile(r"ISO\s?\d{4,5}"),
    re.compile(r"CMMI\s?\d"),
    re.compile(r"等保[一二三]级"),
    re.compile(r"PMP"),
    re.compile(r"CCRC\s?\d{0,4}"),
    re.compile(r"ISCCC"),
]
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


# ═══════════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════════
def check_facts(ctx: CheckContext) -> list[QualityIssue]:
    """事实区数字双层检查 + 资质证书双向校验。"""
    issues: list[QualityIssue] = []
    corpus = _build_corpus_numbers(ctx)
    for section in ctx.fact_zone_sections():
        sid = section.get("section_id") or ""
        content = section.get("content_md") or ""
        if not content:
            continue
        claims = extract_claims(content, sid, ctx.registry)
        claimed = _claimed_positions(claims)
        issues += _layer_a(content, sid, corpus, claimed)
        issues += _layer_b(claims, sid, ctx)
    issues += _cert_check(ctx)
    return issues


# ═══════════════════════════════════════════════════════════════════════
# Layer A —— 成员资格
# ═══════════════════════════════════════════════════════════════════════
def _build_corpus_numbers(ctx: CheckContext) -> set[str]:
    """允许语料的全部数字 token 集（数字可溯源判定基准）。"""
    texts: list[str] = []
    for ev in ctx.evidences.values():
        texts.append(ev.get("content") or "")
    for cap in ctx.capabilities:
        texts.append(cap.get("name") or "")
        texts.append(cap.get("description") or "")
        texts.append(cap.get("attributes") or "")
    for c in ctx.canonicals:
        texts.append(c.get("title") or "")
        texts.append(c.get("text") or "")
    for row in ctx.db.query(
            "SELECT title, original_text FROM requirements "
            "WHERE tender_id = ?", (ctx.tender_id,)):
        texts.append(row.get("title") or "")
        texts.append(row.get("original_text") or "")
    nums: set[str] = set()
    for t in texts:
        for m in _NUM_RE.finditer(t):
            nums.add(m.group(0))
    return nums


def _claimed_positions(claims) -> dict[int, set[tuple[int, int]]]:
    """Layer B 已认领的数字位置（lineno → {(start, end)}），Layer A 跳过。"""
    out: dict[int, set[tuple[int, int]]] = {}
    for c in claims:
        out.setdefault(c.lineno, set()).add((c.start, c.end))
    return out


def _layer_a(content: str, section_id: str, corpus: set[str],
             claimed: dict[int, set[tuple[int, int]]]) -> list[QualityIssue]:
    """逐行抽数：非结构数字且 ∉ 语料且未被 Layer B 认领 → NUMBER_MISMATCH。"""
    issues: list[QualityIssue] = []
    for lineno, line in enumerate(content.split("\n")):
        for num, s, e in iter_numbers(line):
            if is_table_first_col_number(line, s, num) \
                    or is_structural_number(line, s, num):
                continue
            if (s, e) in claimed.get(lineno, set()):
                continue
            if num in corpus:
                continue
            issues.append(_issue(
                section_id=section_id,
                issue_type=IssueType.NUMBER_MISMATCH,
                severity=Severity.WARNING,
                message=f"数字「{num}」在知识库语料（证据/能力卡/招标需求）中"
                        f"不可溯源",
                source_refs=[{"section": section_id, "number": num}],
                suggestion="核对数字来源，无法溯源的数字应从标书中删除或"
                           "改用【待确认】如实标注"))
    return issues


# ═══════════════════════════════════════════════════════════════════════
# Layer B —— 锚定比较
# ═══════════════════════════════════════════════════════════════════════
def _layer_b(claims, section_id: str, ctx: CheckContext) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    for c in claims:
        entries = ctx.registry.metric(c.metric)
        if not entries:
            continue                     # 无注册表条目 → 不做锚定（防误报）
        any_compared = False
        matched = False
        for e in entries:
            norm = _to_implied(c.lo, c.hi, c.unit, e.unit or "count")
            if norm is None:
                continue                 # 单位不可换算 → 放弃该条目比较
            any_compared = True
            if _values_match(norm[0], norm[1], e.value, e.value_hi,
                             e.unit or "count"):
                matched = True
                break
        if not any_compared or matched:
            continue
        issues.append(_mismatch_issue(c, e=next(iter(entries)), ctx=ctx))
    return issues


def _to_implied(lo: float, hi: Optional[float], from_unit: str,
                to_unit: str) -> Optional[tuple[float, Optional[float]]]:
    """裸数字（""→隐含单位）直接取；带单位走同库换算；不可换算 → None。"""
    if not from_unit or from_unit == to_unit:
        return lo, hi
    from ...matching.rules.rule_engine import _UNIT_FACTORS
    f = _UNIT_FACTORS.get((from_unit, to_unit))
    if f is None:
        return None
    return lo * f, (hi * f if hi is not None else None)


def _values_match(a_lo: float, a_hi: Optional[float],
                  b_lo: Optional[float], b_hi: Optional[float],
                  unit: str) -> bool:
    """区间相交判定。percent 用绝对 0.05 个百分点；其余 ±1% 相对。"""
    if unit == "percent":
        tol = 0.05
        al, ah = a_lo - tol, (a_hi if a_hi is not None else a_lo) + tol
        bl, bh = (b_lo if b_lo is not None else 0.0) - tol, \
                 (b_hi if b_hi is not None else b_lo) + tol
    else:
        tol = 0.01
        al, ah = a_lo * (1 - tol), (a_hi if a_hi is not None else a_lo) * (1 + tol)
        bl, bh = (b_lo if b_lo is not None else 0.0) * (1 - tol), \
                 (b_hi if b_hi is not None else b_lo) * (1 + tol)
    return max(al, bl) <= min(ah, bh)


def _mismatch_issue(c, e, ctx: CheckContext) -> QualityIssue:
    """按注册表条目 kind 归类严重度与类型。"""
    if e.kind == "person":
        itype, sev = IssueType.PERSON_MISMATCH, Severity.ERROR
    elif e.kind == "project":
        itype, sev = IssueType.PROJECT_MISMATCH, Severity.ERROR
    else:
        itype, sev = IssueType.NUMBER_MISMATCH, Severity.WARNING
    got = f"{c.lo}{c.unit or ''}"
    if c.hi is not None:
        got = f"{c.lo}-{c.hi}{c.unit or ''}"
    expected = _fmt_value(e)
    return _issue(
        section_id=c.section_id,
        issue_type=itype, severity=sev,
        message=f"{e.metric}标书声明 {got}，知识库为 {expected}（{e.source_ref}）",
        source_refs=[{"section": c.section_id, "metric": c.metric,
                      "claim": got, "source": e.source_ref}],
        suggestion=f"与知识库 {e.source_ref} 核对后修正为 {expected}")


def _fmt_value(e) -> str:
    if e.value is None:
        return e.name or e.metric
    s = f"{e.value}"
    if e.value_hi is not None:
        s = f"{e.value}-{e.value_hi}"
    unit = {"money_wan": "万元", "year": "年", "hour": "小时",
            "count": "台/个", "percent": "%", "month": "个月"}.get(e.unit, "")
    return f"{s}{unit}"


# ═══════════════════════════════════════════════════════════════════════
# 证书双向校验（M5-05）
# ═══════════════════════════════════════════════════════════════════════
def _fact_body(ctx: CheckContext) -> str:
    return "\n".join(s.get("content_md") or ""
                     for s in ctx.fact_zone_sections())


def _cert_check(ctx: CheckContext) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    body = _fact_body(ctx)
    certs = ctx.registry.certs()
    if not certs:
        return issues
    names = {e.name for e in certs}
    as_of = ctx.as_of or date.today().isoformat()

    # (a) 注册表证书必须出现在正文 + (c) 有效期
    for e in certs:
        name = e.name or ""
        if name and name not in body:
            issues.append(_issue(
                issue_type=IssueType.CERTIFICATE_MISMATCH,
                severity=Severity.ERROR,
                message=f"知识库资质「{name}」未在标书正文出现",
                source_refs=[{"cap": e.source_ref, "cert": name}],
                suggestion=f"在资质章节如实补充「{name}」证书信息"))
        valid_until = e.extra.get("valid_until") or ""
        if name and valid_until:
            expired = _is_expired(valid_until, as_of)
            if expired is True:
                issues.append(_issue(
                    issue_type=IssueType.CERTIFICATE_MISMATCH,
                    severity=Severity.CRITICAL,
                    message=f"资质「{name}」有效期至 {valid_until}，"
                            f"已早于检查基准日 {as_of}",
                    source_refs=[{"cap": e.source_ref, "cert": name,
                                  "valid_until": valid_until}],
                    suggestion="更新证书或从标书中移除该资质声明"))
            elif expired is None:
                issues.append(_issue(
                    issue_type=IssueType.CERTIFICATE_MISMATCH,
                    severity=Severity.INFO,
                    message=f"资质「{name}」有效期「{valid_until}」格式不明，"
                            f"未做过期判定",
                    source_refs=[{"cap": e.source_ref, "cert": name}]))
    # (b) 正文证书样 token 必须 ∈ 注册表（逐节扫描，带 section 溯源）
    for section in ctx.fact_zone_sections():
        sid = section.get("section_id") or ""
        for m in _cert_tokens(section.get("content_md") or ""):
            if m in names:
                continue
            issues.append(_issue(
                section_id=sid, issue_type=IssueType.CERTIFICATE_MISMATCH,
                severity=Severity.ERROR,
                message=f"标书出现证书「{m}」，知识库证书库中不存在",
                source_refs=[{"section": sid, "cert": m}],
                suggestion=f"核对「{m}」是否为真实持有的证书；若为笔误请改正"))
    return issues


def _cert_tokens(text: str) -> set[str]:
    out: set[str] = set()
    for rx in _CERT_TOKEN_RES:
        for m in rx.finditer(text):
            out.add(re.sub(r"\s+", "", m.group(0)))
    return out


def _is_expired(valid_until: str, as_of: str) -> Optional[bool]:
    """valid_until < as_of → True；非日期格式 → None（无法判定）。"""
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            v = datetime.strptime(valid_until.strip(), fmt).date()
            a = datetime.strptime(as_of[:10], "%Y-%m-%d").date()
            return v < a
        except ValueError:
            continue
    return None


def _issue(section_id: str = "", requirement_id: str = "",
           issue_type: IssueType = IssueType.FORMAT_ERROR,
           severity: Severity = Severity.WARNING,
           message: str = "", source_refs: Optional[list[dict[str, Any]]] = None,
           suggestion: str = "", autofixable: bool = False) -> QualityIssue:
    return QualityIssue(
        section_id=section_id, requirement_id=requirement_id,
        issue_type=issue_type, severity=severity, message=message,
        source_refs=source_refs or [], suggestion=suggestion,
        autofixable=autofixable)


__all__ = ["check_facts"]
