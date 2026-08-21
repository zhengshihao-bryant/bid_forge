# -*- coding: utf-8 -*-
"""
quality/checks/scan.py —— 数字扫描与锚定取值（facts/consistency 共用）

三层独立机制：

1. `iter_numbers`：章节文本逐行抽数（含结构豁免判定）。
2. `is_structural_number`：结构数字豁免集（防误报）——
   - 与字母直连（ISO9001 / CMMI3 / EVD-0001 / V3.2 / max_devices 属性键）
   - 年份（1900-2099 + 后随"年"）、"第N页/章"、7×24 相邻乘号、日期成分
   - 表格行首列 ≤2 位序号（is_table_first_col_number）
3. `extract_claims`：最近锚点取值 —— 每个数字归属最近的指标锚点，产出
   该指标的规范化声明 (metric, lo, hi, unit)。同一指标多声明是跨章节
   冲突（consistency）与锚定比较（facts）的同源输入。

最近锚点策略消除表格/紧凑行内跨指标串线：CH-04-3 行
「| 1 | 智慧园区类项目 | 3 | 单个合同额500万元 |」中 "3" 归"项目"锚点、
"500" 归"合同额"锚点，互不串线。require_all 条目（人员：姓名+经验）还需
±16 窗口同时含全部关键词，防"质保3年 vs 张伟3年"类误配。
"""
from __future__ import annotations

import re
from typing import Iterator, Optional

from ...matching.rules.rule_engine import _UNIT_KEYS
from ..models import FactRegistry

# 章节文本数字
NUM_RE = re.compile(r"\d+(?:\.\d+)?")
# 表格行首列序号："| 1 | ..."
_TABLE_FIRST_COL_RE = re.compile(r"^\|\s*(\d{1,2})\s*\|")
# 日期 YYYY-MM-DD / YYYY-MM-DD HH:MM
_DATE_RE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}(?:[ T]\d{1,2}:\d{2})?")
_ASCII_LETTER = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

# 锚定窗口：数字归属最近锚点的最大距离；require_all 关键词窗口半径
MAX_ANCHOR_DIST = 24
KEYWORD_WINDOW = 16

# 结构数字集（Layer A 与 Layer B 共用豁免）
def is_structural_number(line: str, start: int, num_text: str) -> bool:
    """结构数字豁免：id/版本/年份/页次/乘式/日期，非企业事实值。"""
    end = start + len(num_text)
    before = line[:start]
    after = line[end:]

    # 1) 与 ASCII 字母直连（ISO9001/CMMI3/V3.2/max_devices 属性键内数字）
    if start > 0 and before[-1] in _ASCII_LETTER:
        return True
    # 2) EVD-0001 型：- 前是字母，数字紧贴 -（如 "EVD-0001"）
    if start >= 2 and before[-1] == "-" and before[-2] in _ASCII_LETTER:
        return True
    # 3) 年份：1900-2099 且后随"年"
    if 1900 <= float(num_text) <= 2099 and after.startswith("年"):
        return True
    # 4) "第N页/章"
    if before.endswith("第") and (after.startswith("页")
                                  or after.startswith("章")):
        return True
    # 5) 7×24 相邻乘号（两侧任一）
    if after.startswith("×") or after.startswith("x") \
            or before.endswith("×") or before.endswith("x"):
        return True
    # 6) 日期成分（YYYY-MM-DD）
    for m in _DATE_RE.finditer(line):
        if m.start() <= start and m.end() >= end:
            return True
    return False


def is_table_first_col_number(line: str, start: int, num_text: str) -> bool:
    """表格行首列序号（≤2 位）：| 1 | ... → 豁免。"""
    m = _TABLE_FIRST_COL_RE.match(line)
    if not m:
        return False
    col = m.group(1)
    col_start = line.find(col)
    return col_start <= start <= col_start + len(col)


def iter_numbers(line: str) -> Iterator[tuple[str, int, int]]:
    """逐数产出 (num_text, start, end)。"""
    for m in NUM_RE.finditer(line):
        yield m.group(0), m.start(), m.end()


class Claim:
    """某个指标的一条正文声明（含来源章节，供冲突/锚定共用）。"""

    __slots__ = ("metric", "section_id", "lo", "hi", "unit",
                 "num_text", "start", "end", "lineno")

    def __init__(self, metric: str, section_id: str, lo: float,
                 hi: Optional[float], unit: str, num_text: str = "",
                 start: int = 0, end: int = 0, lineno: int = 0):
        self.metric = metric
        self.section_id = section_id
        self.lo = lo
        self.hi = hi
        self.unit = unit
        self.num_text = num_text
        self.start = start
        self.end = end
        self.lineno = lineno

    def as_interval(self, tolerance: float = 0.0) -> tuple[float, float]:
        lo = self.lo * (1 - tolerance)
        hi = (self.hi if self.hi is not None else self.lo) * (1 + tolerance)
        return lo, hi

    def __repr__(self):  # pragma: no cover - 调试用
        return (f"<Claim {self.metric} {self.section_id} "
                f"{self.lo}-{self.hi}{self.unit}>")


def _anchor_index(registry: FactRegistry) -> list[dict]:
    """anchors：平铺 (metric, keyword, require_all, keywords_set)。"""
    anchors = []
    for e in registry.entries:
        kws = list(e.anchor_keywords)
        if not kws:
            continue
        for kw in kws:
            anchors.append({"metric": e.metric, "keyword": kw,
                            "require_all": e.require_all,
                            "keywords": set(kws)})
    return anchors


def _parse_claim(line: str, s: int, e: int) -> Optional[tuple[float, Optional[float], str]]:
    """从数字原位向后解析（带单位）。避免 ±8 窗口取到前一个数字。

    注意 s/e 是行内偏移（调用方逐行扫描）；单位原始串归一为
    rule_engine._UNIT_KEYS 的规范键（"万元"→money_wan），与注册表同口径。
    """
    m = _VALUE_RE_MATCH.match(line[s: s + 16])
    if not m:
        return None
    unit_raw = m.group("unit") or ""
    return float(m.group("a")), (
        float(m.group("b")) if m.group("b") else None), _UNIT_KEYS.get(unit_raw, "")


def extract_claims(text: str, section_id: str,
                   registry: FactRegistry) -> list[Claim]:
    """事实区章节 → 最近锚点取值声明列表。

    规则：逐行扫锚点出现位置；每个非结构数字归属最近锚点（≤24 字符）；
    require_all 条目须 ±16 窗口含该条目全部关键词，否则不认领。
    """
    claims: list[Claim] = []
    anchors = _anchor_index(registry)
    if not anchors:
        return claims
    lines = text.split("\n")
    for lineno, line in enumerate(lines):
        occ = []  # (pos, metric, require_all, keywords_set)
        for a in anchors:
            for m in re.finditer(re.escape(a["keyword"]), line):
                occ.append((m.start(), a["metric"], a["require_all"],
                            a["keywords"]))
        if not occ:
            continue
        for num, s, e in iter_numbers(line):
            if is_table_first_col_number(line, s, num) \
                    or is_structural_number(line, s, num):
                continue
            best: Optional[tuple[float, object]] = None
            best_d = MAX_ANCHOR_DIST + 1
            for pos, metric, req_all, kws in occ:
                # 距离：数字区间与锚点区间相交 → 0；否则取最近端间距
                anchor_span = _anchor_span(line, pos)
                if max(anchor_span[0], s) <= min(anchor_span[1], e):
                    d = 0
                else:
                    d = min(abs(s - anchor_span[1]), abs(e - anchor_span[0]))
                if d < best_d:
                    best_d = d
                    best = (metric, kws, req_all)
            if best is None:
                continue
            metric, kws, req_all = best
            # require_all 才做全关键词窗口校验（防"质保3年 vs 张伟3年"）；
            # 非 require_all 条目任一关键词出现即认领（"合同额/金额"任一即算）
            if req_all and len(kws) > 1:
                window = line[max(0, s - KEYWORD_WINDOW):
                              min(len(line), e + KEYWORD_WINDOW)]
                if not all(k in window for k in kws):
                    continue  # require_all 防串线
            parsed = _parse_claim(line, s, e)
            if parsed is None:
                continue
            lo, hi, unit = parsed
            claims.append(Claim(metric, section_id, lo, hi, unit,
                                num_text=num, start=s, end=e, lineno=lineno))
    return claims


def _anchor_span(line: str, pos: int) -> tuple[int, int]:
    """锚点区间：从 pos 向后延伸到非词边界（锚点多为中英文混合串）。"""
    end = pos
    while end < len(line) and line[end] not in " \t，。；：、()（）|,;":
        end += 1
    return pos, end


# 与 rule_engine._VALUE_RE 同口径，但 .match 从数字原位解析（不含前置字符）
_VALUE_RE_MATCH = re.compile(
    r"(?P<a>\d+(?:\.\d+)?)\s*(?:[-~～至]\s*(?P<b>\d+(?:\.\d+)?))?"
    r"\s*(?P<unit>工作日|个月|万元|小时|分钟|人民币|年|月|台|个|人|名|家|项|套|座|份|%|万|元|秒|天|日)?")


__all__ = ["iter_numbers", "is_structural_number",
           "is_table_first_col_number", "extract_claims", "Claim",
           "NUM_RE", "MAX_ANCHOR_DIST"]
