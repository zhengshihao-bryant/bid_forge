# -*- coding: utf-8 -*-
"""
app/services/extraction.py —— 需求提取服务（M1 核心）

管线：

    解析产物 ──▶ 窗口切分（≤4000 字，页边界对齐；【第p页】标记只存在于窗口临时文本，
                   绝不写回 Block.text —— 否则污染 M2 嵌入与 M3 引用）
            ──▶ LLM 结构化提取（JSON 提示词；重试 3 次；finish_reason=="length" 切窗重试）
            ──▶ Pydantic 校验（坏条丢弃计数上报，不整批失败）
            ──▶ 去重 + REQ 编号 + ★条款规则补扫
            ──▶ 入库（由后台任务 run_extraction_task 调用）

评分标准表不走 LLM —— parse_score_tables 规则解析 TABLE 块
（LLM 读表不可靠：多列表对齐、数字易错；规则更准，且可解释）。

窗口大小与 DeepSeek 上限的关系（技术校验结论）：
deepseek-chat 默认 max_tokens=4096（8K 需 beta 端点），窗口 ≤4000 字
使"输入 + 输出"安全落在上下文与输出上限内；超时设 120s。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .. import config
from ..db import Database
from ..schemas import (    Block, BlockType, ParsedDocument, QuantitativeItem, Requirement,
    RequirementType, ScorePoint, SourceAnchor, now_str,
)
from .llm import create_llm_client, llm_call_context
from .task_tracker import (fail_task, start_task, succeed_task,
                           update_progress)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int, int], None]  # (描述, 已完成窗口, 总窗口)


# ═══════════════════════════════════════════════════════════════════════
# 提示词（事实约束铁律写进 system prompt）
# ═══════════════════════════════════════════════════════════════════════
_SYSTEM_PROMPT = """你是一名资深投标顾问，负责从招标文件中提取【必须响应】的要求条目。

铁律（事实约束）：
1. 只提取原文中明确写出的要求，绝不补充、推测或润色数字
2. 量化指标必须原样保留：数值、比较符（≥/≤/不少于/不高于）、单位
3. 无法确定的字段留空字符串，不要编造
4. type 必须从给定枚举中选择
5. 重要度：标★/※条款、否决项、实质性要求 → "高"；评分标准相关 → "高"；一般硬性要求 → "中"；介绍性内容 → "低"
6. 每条要求给出原文片段 snippet 与页码 page，用于回溯核对
7. 章节标题、目录性文字本身不是要求，不要提取

type 枚举：项目背景、建设目标、技术要求、功能要求、实施要求、人员要求、资质要求、售后服务、评分标准、投标文件格式、商务要求、报价要求

输出 JSON 格式（务必只输出 JSON）：
{"requirements": [{"type": "枚举之一", "title": "一句话概括(≤30字)", "original_text": "原文逐字摘录(≤200字)", "quantitative": [{"metric": "指标名", "op": "≥/≤/不少于/不高于或空", "value": "数值", "unit": "单位或空"}], "importance": "高/中/低", "is_star": true或false, "page": 页码数字}]}"""

_USER_TEMPLATE = """【招标项目】{tender_name}
【当前文件】{file_name}
【章节路径】{section_path}
【页码范围】{page_range}
【原文】（【第p页】为页码标记，填写 page 字段时以此为准）

{text}

请从以上原文提取所有必须响应的要求条目，输出 JSON。"""


# ═══════════════════════════════════════════════════════════════════════
# 窗口模型与切分
# ═══════════════════════════════════════════════════════════════════════
@dataclass
class ExtractionWindow:
    doc_id: str
    file_name: str
    file_type: str
    section_path: str
    page_start: Optional[int]
    page_end: Optional[int]
    blocks: list[Block] = field(default_factory=list)
    text: str = ""    # 带【第p页】标记的临时文本（不写回 Block）


def _block_text(b: Block) -> str:
    if b.type == BlockType.TABLE and b.table:
        return "\n".join(" | ".join(r) for r in b.table)
    return b.text


def _section_path(ancestors: list[str]) -> str:
    return " > ".join(ancestors)


def _build_windows(doc: ParsedDocument, max_chars: int) -> list[ExtractionWindow]:
    """按顶层章节切窗口：≤max_chars，超限优先按页边界回退切分。"""
    block_map = {b.block_id: b for b in doc.blocks}

    def section_blocks(section) -> list[Block]:
        ids: list[str] = []

        def walk(sec) -> None:
            ids.extend(sec.block_ids)
            for c in sec.children:
                walk(c)

        walk(section)
        return [block_map[i] for i in ids if i in block_map]

    windows: list[ExtractionWindow] = []
    for sec in doc.sections:
        blocks = section_blocks(sec)
        if not blocks:
            continue
        for chunk in _chunk_blocks(blocks, max_chars):
            windows.append(_make_window(doc, [sec.title], chunk))
    return windows


def _chunk_blocks(blocks: list[Block], max_chars: int) -> list[list[Block]]:
    """块序列 → 若干 ≤max_chars 的块组；溢出时优先回退到最近页边界切分。"""
    chunks: list[list[Block]] = []
    current: list[Block] = []
    chars = 0
    for b in blocks:
        length = len(_block_text(b))
        if current and chars + length > max_chars:
            cut = None
            for i in range(len(current) - 1, 0, -1):
                if current[i].page is not None and current[i].page != current[i - 1].page:
                    cut = i
                    break
            if cut is not None:
                chunks.append(current[:cut])
                current = current[cut:]
                chars = sum(len(_block_text(x)) for x in current)
            else:
                chunks.append(current)
                current, chars = [], 0
        current.append(b)
        chars += length
    if current:
        chunks.append(current)
    return chunks


def _make_window(doc: ParsedDocument, ancestors: list[str],
                 blocks: list[Block]) -> ExtractionWindow:
    parts: list[str] = []
    last_page: Optional[int] = None
    pages = [b.page for b in blocks if b.page is not None]
    for b in blocks:
        if b.page is not None and b.page != last_page:
            parts.append(f"【第{b.page}页】")
            last_page = b.page
        parts.append(_block_text(b))
    return ExtractionWindow(
        doc_id="",  # 由调用方回填（documents 表主键）
        file_name=doc.file_name,
        file_type=doc.file_type,
        section_path=_section_path(ancestors),
        page_start=min(pages) if pages else None,
        page_end=max(pages) if pages else None,
        blocks=blocks,
        text="\n".join(parts),
    )


# ═══════════════════════════════════════════════════════════════════════
# 类型归一与校验
# ═══════════════════════════════════════════════════════════════════════
def _coerce_type(raw: Any) -> Optional[RequirementType]:
    """类型模糊归一：枚举值互相包含即匹配；匹配不上返回 None（该条丢弃）。"""
    if not raw:
        return None
    raw = str(raw).strip()
    for t in RequirementType:
        if t.value == raw:
            return t
    for t in RequirementType:
        if t.value in raw or raw in t.value:
            return t
    return None


def _parse_quantitative(raw: Any) -> list[QuantitativeItem]:
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        q = QuantitativeItem(
            metric=str(item.get("metric") or "").strip(),
            op=str(item.get("op") or "").strip(),
            value=str(item.get("value") or "").strip(),
            unit=str(item.get("unit") or "").strip(),
        )
        if q.metric or q.value:
            out.append(q)
    return out


def _match_block_id(snippet: str, blocks: list[Block]) -> str:
    """snippet 与窗口内块文本双向包含匹配，得到块号（四元溯源细化）。"""
    if not snippet:
        return ""
    head = snippet[:30]
    for b in blocks:
        if b.type == BlockType.TABLE:
            continue
        if head in b.text or (len(b.text) >= 20 and b.text[:30] in snippet):
            return b.block_id
    return ""


# ═══════════════════════════════════════════════════════════════════════
# 提取器
# ═══════════════════════════════════════════════════════════════════════
class RequirementExtractor:
    """需求提取器：窗口切分 → LLM 结构化提取 → 校验 → 去重编号 → ★补扫。"""

    def __init__(self, client=None, window_chars: Optional[int] = None,
                 progress_cb: Optional[ProgressCallback] = None):
        self.client = client or create_llm_client()
        self.window_chars = window_chars or config.EXTRACT_WINDOW_CHARS
        self.progress_cb = progress_cb

    # ------------------------------------------------------------------
    def extract(self, tender_id: str, tender_name: str,
                parsed_docs: list[ParsedDocument],
                doc_id_map: Optional[dict[str, str]] = None) -> tuple[list[Requirement], dict]:
        """全量提取。doc_id_map: file_name → documents 表主键（四元溯源）。"""
        doc_id_map = doc_id_map or {}
        stats = {"windows": 0, "llm_calls": 0, "dropped_items": 0,
                 "retries": 0, "windows_failed": 0}
        all_reqs: list[Requirement] = []
        windows: list[ExtractionWindow] = []
        for doc in parsed_docs:
            windows.extend(_build_windows(doc, self.window_chars))
        stats["windows"] = len(windows)

        for i, w in enumerate(windows):
            w.doc_id = doc_id_map.get(w.file_name, "")
            reqs, wstats = self._extract_window(tender_id, tender_name, w)
            all_reqs.extend(reqs)
            for k in ("llm_calls", "dropped_items", "retries", "windows_failed"):
                stats[k] += wstats[k]
            if self.progress_cb:
                if w.page_start is not None:
                    loc = f"第{w.page_start}-{w.page_end}页"
                else:
                    loc = "无页码（以章节路径为准）"
                self.progress_cb(f"{w.file_name} {w.section_path[:40]} {loc}", i + 1, len(windows))

        reqs = self._dedupe_and_number(all_reqs)
        reqs = self._star_sweep(reqs)
        logger.info("提取完成 tender=%s: %d 条需求（%d 窗口 / %d 次 LLM 调用 / %d 次重试 / 丢弃 %d 条）",
                    tender_id, len(reqs), stats["windows"], stats["llm_calls"],
                    stats["retries"], stats["dropped_items"])
        return reqs, stats

    # ------------------------------------------------------------------
    def _extract_window(self, tender_id: str, tender_name: str,
                        w: ExtractionWindow) -> tuple[list[Requirement], dict]:
        stats = {"llm_calls": 0, "dropped_items": 0, "retries": 0, "windows_failed": 0}
        page_range = (f"第 {w.page_start}-{w.page_end} 页" if w.page_start is not None
                      else "该文件无页码（章节路径为准）")
        user = _USER_TEMPLATE.format(
            tender_name=tender_name, file_name=w.file_name,
            section_path=w.section_path, page_range=page_range, text=w.text)

        for attempt in range(3):
            with llm_call_context("extract"):
                resp = self.client.chat_json(_SYSTEM_PROMPT, user)
            stats["llm_calls"] += 1
            if resp is None:
                stats["retries"] += 1
                continue
            if resp.get("finish_reason") == "length":
                # 输出截断：半窗切分后分别提取（递归）
                logger.warning("窗口输出截断，切半窗重试: %s %s", w.file_name, w.section_path)
                return self._extract_split(tender_id, tender_name, w, stats)
            data = resp.get("data") or {}
            raw_items = data.get("requirements")
            if not isinstance(raw_items, list):
                stats["retries"] += 1
                continue
            reqs, dropped = self._validate_items(tender_id, w, raw_items)
            stats["dropped_items"] += dropped
            return reqs, stats

        stats["windows_failed"] += 1
        logger.warning("窗口提取失败（3 次重试后放弃）: %s %s", w.file_name, w.section_path)
        return [], stats

    def _extract_split(self, tender_id: str, tender_name: str,
                       w: ExtractionWindow, stats: dict) -> tuple[list[Requirement], dict]:
        mid = len(w.blocks) // 2
        if mid < 2:
            return [], stats
        reqs_all: list[Requirement] = []
        for half in (w.blocks[:mid], w.blocks[mid:]):
            sub = _make_window_from_blocks(w, half)
            reqs, s2 = self._extract_window(tender_id, tender_name, sub)
            reqs_all.extend(reqs)
            for k in stats:
                stats[k] += s2[k]
        return reqs_all, stats

    # ------------------------------------------------------------------
    def _validate_items(self, tender_id: str, w: ExtractionWindow,
                        raw_items: list) -> tuple[list[Requirement], int]:
        reqs: list[Requirement] = []
        dropped = 0
        for raw in raw_items:
            if not isinstance(raw, dict):
                dropped += 1
                continue
            rtype = _coerce_type(raw.get("type"))
            if rtype is None:
                dropped += 1
                continue
            original = str(raw.get("original_text") or "").strip()
            title = str(raw.get("title") or "").strip()
            if not original or not title:
                dropped += 1
                continue
            page = raw.get("page")
            if not isinstance(page, int) or page <= 0:
                page = w.page_start
            snippet = str(raw.get("snippet") or original)[:300]
            reqs.append(Requirement(
                tender_id=tender_id,
                type=rtype,
                title=title[:60],
                original_text=original[:300],
                quantitative=_parse_quantitative(raw.get("quantitative")),
                importance=raw.get("importance") if raw.get("importance") in ("高", "中", "低") else "中",
                is_star=bool(raw.get("is_star")),
                source=SourceAnchor(
                    document=w.file_name,
                    doc_id=w.doc_id,
                    page=page,
                    section_path=w.section_path,
                    block_id=_match_block_id(snippet, w.blocks),
                    snippet=snippet,
                ),
            ))
        return reqs, dropped

    # ------------------------------------------------------------------
    @staticmethod
    def _dedupe_and_number(reqs: list[Requirement]) -> list[Requirement]:
        seen: set[tuple] = set()
        out: list[Requirement] = []
        for r in reqs:
            key = (r.type.value, re.sub(r"\s+", "", r.title))
            if key in seen:
                continue
            seen.add(key)
            r.id = f"REQ-{len(out) + 1:04d}"
            r.updated_at = now_str()
            out.append(r)
        return out

    @staticmethod
    def _star_sweep(reqs: list[Requirement]) -> list[Requirement]:
        """规则补扫：★/※/否决项/实质性要求 强制 is_star + 高重要度。"""
        for r in reqs:
            haystack = f"{r.title} {r.original_text} {(r.source.snippet if r.source else '')}"
            if any(kw in haystack for kw in ("★", "※", "否决", "实质性要求")):
                r.is_star = True
                r.importance = "高"
        return reqs


def _make_window_from_blocks(w: ExtractionWindow, blocks: list[Block]) -> ExtractionWindow:
    parts: list[str] = []
    last_page: Optional[int] = None
    pages = [b.page for b in blocks if b.page is not None]
    for b in blocks:
        if b.page is not None and b.page != last_page:
            parts.append(f"【第{b.page}页】")
            last_page = b.page
        parts.append(_block_text(b))
    return ExtractionWindow(
        doc_id=w.doc_id, file_name=w.file_name, file_type=w.file_type,
        section_path=w.section_path,
        page_start=min(pages) if pages else None,
        page_end=max(pages) if pages else None,
        blocks=blocks, text="\n".join(parts),
    )


# ═══════════════════════════════════════════════════════════════════════
# 评分标准表规则解析（不走 LLM）
# ═══════════════════════════════════════════════════════════════════════
_SCORE_HEADER_KEYWORDS = ("评分", "分值", "评审", "评标")


def _detect_columns(header_row: list[str]) -> dict[str, int]:
    """表头单元格 → 列映射（优先级：评价项 > 细则 > 分值，避免"评分标准"误判为分值列）。"""
    col_map: dict[str, int] = {}
    for i, cell in enumerate(header_row):
        c = cell or ""
        if "item" not in col_map and any(
                k in c for k in ("评价项", "评审项", "评审内容", "评分项目", "评分因素")):
            col_map["item"] = i
        elif "criteria" not in col_map and any(
                k in c for k in ("细则", "说明", "标准", "评分办法", "评分依据")):
            col_map["criteria"] = i
        elif "score" not in col_map and any(
                k in c for k in ("分值", "分数", "权重", "评分")):
            col_map["score"] = i
    return col_map


def _parse_number(cell: str) -> Optional[float]:
    m = re.search(r"\d+(?:\.\d+)?", cell or "")
    return float(m.group()) if m else None


def _detect_category(haystack: str) -> str:
    if any(k in haystack for k in ("价格", "报价")):
        return "价格"
    if any(k in haystack for k in ("商务", "资质", "业绩", "信誉")):
        return "商务"
    if any(k in haystack for k in ("技术", "方案", "功能", "性能")):
        return "技术"
    return "其他"


def _block_section_context(doc: ParsedDocument) -> dict[str, str]:
    """块号 → 所在章节路径（用于评分表类别判定：表头上下文比行内关键词可靠）。"""
    mapping: dict[str, str] = {}

    def walk(section, path: list[str]) -> None:
        p = path + [section.title]
        for bid in section.block_ids:
            mapping[bid] = " > ".join(p)
        for child in section.children:
            walk(child, p)

    for s in doc.sections:
        walk(s, [])
    return mapping


def parse_score_tables(tender_id: str, parsed_docs: list[ParsedDocument],
                       ) -> tuple[list[ScorePoint], list[dict]]:
    """规则解析评分表 TABLE 块 → ScorePoint 列表 + 未识别表告警。

    类别判定用「章节标题 + 表头 + 评价项」：样例中技术评分表位于
    "11.2 技术评分表" 标题下，行内关键词（如"安全性设计"）识别不出时
    靠章节上下文兜底，这是类别判定的主要依据。
    """
    points: list[ScorePoint] = []
    warnings: list[dict] = []
    for doc in parsed_docs:
        section_of = _block_section_context(doc)
        for b in doc.blocks:
            if b.type != BlockType.TABLE or not b.table:
                continue
            rows = b.table
            if len(rows) < 2:
                continue
            header = " ".join(rows[0])
            if not any(kw in header for kw in _SCORE_HEADER_KEYWORDS):
                continue
            col_map = _detect_columns(rows[0])
            if "item" not in col_map or "score" not in col_map:
                warnings.append({
                    "file": doc.file_name, "block": b.block_id,
                    "header": header[:60], "reason": "未识别出评价项/分值列"})
                continue
            context = section_of.get(b.block_id, "")
            for row in rows[1:]:
                if not any(row):
                    continue
                item = (row[col_map["item"]] or "").strip()
                if not item:
                    continue
                score = _parse_number(row[col_map["score"]])
                criteria = (row[col_map["criteria"]] if "criteria" in col_map else "").strip()
                category = _detect_category(f"{context} {header} {item}")
                points.append(ScorePoint(
                    id=f"SC-{len(points) + 1:04d}",
                    tender_id=tender_id,
                    category=category,
                    item=item,
                    max_score=score,
                    criteria=criteria,
                    rule_id=f"RULE-{b.block_id}",
                    weight=score or 0.0,
                    source_ref=f"{doc.file_name}#{b.block_id}",
                ))
    if warnings:
        logger.warning("评分表规则解析：%d 张候选表未识别（人工兜底）", len(warnings))
    return points, warnings


# ═══════════════════════════════════════════════════════════════════════
# 后台任务入口（每任务独立 DB 连接；状态落库防重启丢状态）
# ═══════════════════════════════════════════════════════════════════════
def _score_to_row(p: ScorePoint) -> dict:
    return {
        "id": p.id, "tender_id": p.tender_id, "category": p.category,
        "item": p.item, "max_score": p.max_score, "criteria": p.criteria,
        "rule_id": p.rule_id, "weight": p.weight, "source_ref": p.source_ref,
        "created_at": p.created_at,
    }


def run_extraction_task(tender_id: str, task_id: str = "") -> dict:
    """后台任务入口：读取解析产物 → 提取需求 + 评分点 → 入库。

    M7-05：task_id 非空时同步任务中心状态（start/progress/succeed/fail），
    旧调用（不传 task_id）零改动。
    """
    db = Database(config.DB_PATH)
    tender = db.query_one("SELECT * FROM tenders WHERE id = ?", (tender_id,))
    if not tender:
        logger.error("提取任务：招标项目不存在 tender_id=%s", tender_id)
        return {"tender_id": tender_id, "error": "招标项目不存在"}

    if task_id:
        start_task(db, task_id)
    db.update("tenders", "id", tender_id,
              {"extraction_status": "提取中", "extraction_progress": "读取解析产物"})
    try:
        docs = db.query("SELECT * FROM documents WHERE tender_id = ?", (tender_id,))
        parsed_docs: list[ParsedDocument] = []
        doc_id_map: dict[str, str] = {}
        for d in docs:
            if d["parse_error"]:
                continue
            pfile = Path(config.PARSED_DIR) / tender_id / d["parsed_file"]
            if not pfile.exists():
                logger.warning("解析产物缺失: %s", pfile)
                continue
            parsed_docs.append(ParsedDocument.model_validate_json(pfile.read_text(encoding="utf-8")))
            doc_id_map[parsed_docs[-1].file_name] = d["id"]
        if not parsed_docs:
            raise ValueError("该招标项目没有可用的解析产物")

        def progress_cb(msg: str, done: int, total: int) -> None:
            db.update("tenders", "id", tender_id,
                      {"extraction_progress": f"[{done}/{total}] {msg}"})
            if task_id:
                update_progress(db, task_id, done, total, msg)

        extractor = RequirementExtractor(create_llm_client(), progress_cb=progress_cb)
        reqs, stats = extractor.extract(tender_id, tender["name"], parsed_docs, doc_id_map)
        points, warnings = parse_score_tables(tender_id, parsed_docs)

        db.execute("DELETE FROM requirements WHERE tender_id = ?", (tender_id,))
        db.execute("DELETE FROM score_points WHERE tender_id = ?", (tender_id,))
        for r in reqs:
            db.insert("requirements", Database.requirement_to_row(r))
        for p in points:
            db.insert("score_points", _score_to_row(p))

        db.update("tenders", "id", tender_id, {
            "extraction_status": "已完成",
            "extraction_progress": (
                f"{len(reqs)} 条需求 / {len(points)} 个评分点 / "
                f"未识别表 {len(warnings)} / 丢弃 {stats['dropped_items']} 条"),
            "requirement_count": len(reqs),
            "score_point_count": len(points),
        })
        logger.info("提取完成 tender=%s: %d 需求 / %d 评分点 / 窗口 %d / LLM 调用 %d",
                    tender_id, len(reqs), len(points), stats["windows"], stats["llm_calls"])
        if task_id:
            succeed_task(db, task_id, total=len(parsed_docs), done=len(parsed_docs),
                         progress=f"{len(reqs)} 条需求 / {len(points)} 个评分点")
        return {"tender_id": tender_id, "requirements": len(reqs),
                "score_points": len(points), "stats": stats,
                "table_warnings": warnings}
    except Exception as e:
        logger.exception("提取失败 tender=%s", tender_id)
        db.update("tenders", "id", tender_id,
                  {"extraction_status": "失败", "extraction_progress": str(e)[:500]})
        if task_id:
            fail_task(db, task_id, error=str(e))
        return {"tender_id": tender_id, "error": str(e)}
