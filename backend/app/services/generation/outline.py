# -*- coding: utf-8 -*-
"""
generation/outline.py —— M4-01 标书结构规划

章节三态：
    OutlineTemplate（声明，persist 到 outlines 表，seed_default 首次生成）
        → materialize（按 tender 实例化）
    BidSection（每标书章节树，persist 到 generation_sections 表）
        → mapping（批次 2 把需求挂到章节）

materialize 用 key_overlap 把大纲章节标题与 M1 招标文件章节标题做标题重叠，
找到对应的原始招标章节，填充 BidSection.source_refs（M4-01 关联原始章节）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from ... import config
from ...db import Database
from ...schemas import (CapabilityCategory, ChapterSpec, OutlineTemplate,
                        RequirementType)
from .models import BidSection, SectionType

logger = logging.getLogger(__name__)

DEFAULT_OUTLINE_ID = "outline-default"

# 章节类型中文串 ↔ SectionType 枚举的合法值（防手写漂移）
_SECTION_TYPE_ALIAS = {t.value: t for t in SectionType}


def build_default_outline() -> OutlineTemplate:
    """通用标书结构（四段 + 前置/后置固定章节）—— M4-01 默认大纲。

    12 个 M1 需求类型除「评分标准」（is_scoring 天然排除）外全部被声明，
    默认大纲下需求→章节覆盖接近 100%；「报价要求」走固定格式报价表（不编价格）。
    """
    RT = RequirementType
    C = CapabilityCategory
    ch = ChapterSpec

    def fixed(cid, title, order, types=(), prompt=""):
        return ch(id=cid, order=order, title=title, level=1, section_type="固定格式",
                  requirement_types=[RT(t) for t in types], generation_prompt=prompt)

    def node(cid, title, order, section_type, types, allowed, prompt=""):
        return ch(id=cid, order=order, title=title, level=2,
                  section_type=section_type,
                  requirement_types=[RT(t) for t in types],
                  allowed_categories=[C(a) for a in allowed],
                  generation_prompt=prompt)

    chapters = [
        fixed("CH-01", "封面", 1),
        fixed("CH-02", "目录", 2),
        fixed("CH-03", "投标函及授权书", 3, types=("投标文件格式",)),
        ch(id="CH-04", order=4, title="商务部分", level=1, section_type="方案型",
           children=[
               node("CH-04-1", "公司概况与综合实力", 1, "事实型",
                    ("项目背景",), ("公司介绍",)),
               node("CH-04-2", "企业资质与证书", 2, "表格型",
                    ("资质要求",), ("公司资质",)),
               node("CH-04-3", "类似项目业绩", 3, "表格型",
                    ("商务要求",), ("项目案例",)),
               node("CH-04-4", "商务响应与承诺", 4, "方案型",
                    ("商务要求",), ("公司介绍",)),
               node("CH-04-5", "报价表", 5, "固定格式",
                    ("报价要求",), (), prompt="本表由投标人商务决策后填写，禁止编造价格"),
           ]),
        ch(id="CH-05", order=5, title="技术部分", level=1, section_type="方案型",
           children=[
               node("CH-05-1", "项目理解与建设目标", 1, "方案型",
                    ("项目背景", "建设目标"), ()),
               node("CH-05-2", "总体技术方案", 2, "方案型",
                    ("技术要求", "功能要求"), ("技术方案", "产品")),
               node("CH-05-3", "系统功能设计", 3, "方案型",
                    ("功能要求", "技术要求"), ("产品", "技术方案")),
               node("CH-05-4", "技术指标响应表", 4, "表格型",
                    ("技术要求",), ("产品", "技术方案")),
               node("CH-05-5", "信创与安全合规", 5, "方案型",
                    ("技术要求",), ("公司资质", "技术方案")),
           ]),
        ch(id="CH-06", order=6, title="实施部分", level=1, section_type="方案型",
           children=[
               node("CH-06-1", "项目实施计划", 1, "方案型",
                    ("实施要求",), ("技术方案", "项目案例")),
               node("CH-06-2", "组织机构与人员配备", 2, "事实型",
                    ("人员要求",), ("人员资质",)),
               node("CH-06-3", "项目进度计划", 3, "表格型",
                    ("实施要求",), ("技术方案", "项目案例")),
               node("CH-06-4", "培训与验收方案", 4, "方案型",
                    ("实施要求",), ()),
               node("CH-06-5", "质量保证措施", 5, "方案型",
                    ("实施要求",), ("公司资质",)),
           ]),
        ch(id="CH-07", order=7, title="售后服务", level=1, section_type="方案型",
           children=[
               node("CH-07-1", "售后服务承诺", 1, "方案型",
                    ("售后服务",), ("售后服务",)),
               node("CH-07-2", "质保期与响应承诺", 2, "事实型",
                    ("售后服务",), ("售后服务",)),
               node("CH-07-3", "驻场与运维方案", 3, "事实型",
                    ("售后服务",), ("售后服务",)),
           ]),
        ch(id="CH-08", order=8, title="需求响应表", level=1, section_type="表格型",
           generation_prompt="M4-07 三列响应表，覆盖全部规范需求（含未映射），"
                             "MISSING/UNKNOWN 不编造"),
    ]
    return OutlineTemplate(id=DEFAULT_OUTLINE_ID, name="通用标书结构",
                           description="商务/技术/实施/售后四段默认大纲（M4-01）",
                           chapters=chapters)


class OutlineBuilder:
    """大纲的 seed / 读取 / 实例化。"""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database(config.DB_PATH)

    # ------------------------------------------------------------------
    # outlines 表读写
    # ------------------------------------------------------------------
    def seed_default(self) -> str:
        """把默认大纲 upsert 进 outlines 表，返回 outline id（幂等）。"""
        o = build_default_outline()
        row = self.db.query_one("SELECT id FROM outlines WHERE id = ?",
                                (DEFAULT_OUTLINE_ID,))
        if row:
            self.db.update("outlines", "id", DEFAULT_OUTLINE_ID,
                           {"chapters": json.dumps(
                               [c.model_dump(mode="json") for c in o.chapters],
                               ensure_ascii=False),
                            "description": o.description})
        else:
            self.db.insert("outlines", Database.outline_to_row(o))
        return DEFAULT_OUTLINE_ID

    def get(self, outline_id: str = DEFAULT_OUTLINE_ID) -> OutlineTemplate:
        row = self.db.query_one("SELECT * FROM outlines WHERE id = ?", (outline_id,))
        if not row:
            raise ValueError(f"大纲不存在: {outline_id}（请先 POST /outline seed）")
        return Database.row_to_outline(row)

    # ------------------------------------------------------------------
    # outline → 每标书 BidSection 树
    # ------------------------------------------------------------------
    def materialize(self, tender_id: str,
                    outline: Optional[OutlineTemplate] = None,
                    doc_sections: Optional[list] = None,
                    ) -> list[BidSection]:
        """OutlineTemplate → BidSection 树（顶层列表）。

        source_refs 通过标题重叠匹配 M1 招标文件章节路径填充（doc_sections 提供时）。
        章节 id 沿用大纲稳定 id（CH-04-1），同 tender 重跑先清表（镜像 matcher）。
        """
        outline = outline or self.get()
        doc_sections = doc_sections if doc_sections is not None \
            else self.load_tender_doc_sections(tender_id)

        def convert(c: ChapterSpec, parent: str, siblings: list) -> BidSection:
            sec = BidSection(
                id=c.id, tender_id=tender_id, parent_id=parent,
                title=c.title, level=c.level, ord=c.order,
                section_type=_SECTION_TYPE_ALIAS.get(c.section_type,
                                                     SectionType.SOLUTION),
                source_refs=list(c.source_refs),
                requirement_types=[t.value for t in c.requirement_types],
                allowed_categories=[a.value for a in c.allowed_categories],
                generation_prompt=c.generation_prompt)
            if not sec.source_refs:
                sec.source_refs = _match_source_refs(sec.title, doc_sections)
            sec.children = [convert(child, c.id, sec.children)
                            for child in c.children]
            return sec

        return [convert(c, "", []) for c in outline.chapters]

    @staticmethod
    def flatten(tree: list[BidSection]) -> list[BidSection]:
        """章节树 → 前序平铺（组装/任务按此顺序）。"""
        out: list[BidSection] = []
        for sec in tree:
            out.append(sec)
            out.extend(OutlineBuilder.flatten(sec.children))
        return out

    # ------------------------------------------------------------------
    def load_tender_doc_sections(self, tender_id: str) -> list:
        """招标项目全部文档的 M1 章节（Section 树）—— source_refs 匹配用。

        从 documents.parsed_file 读 ParsedDocument JSON（与 M1 解析产物一致）。
        """
        from ...schemas import Section as M1Section

        out: list[M1Section] = []
        docs = self.db.query("SELECT parsed_file FROM documents WHERE tender_id = ?",
                             (tender_id,))
        for d in docs:
            path = d.get("parsed_file") or ""
            if not path:
                continue
            # parsed_file 入库为相对文件名（data/parsed/{tender_id}/xxx.json）；
            # 绝对路径直接使用（兼容历史数据）
            p = Path(path)
            if not p.is_absolute():
                p = config.PARSED_DIR / tender_id / p
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.extend(M1Section(**s) for s in data.get("sections", []))
        return out


def tree_from_flat(flat: list[BidSection]) -> list[BidSection]:
    """平铺章节（parent_id + 顺序）→ 树。顶层返回有序列表。

    镜像 M1 Section 树重组：children 按原 flat 顺序保持（ord 已保证前序）。
    """
    by_id = {s.id: s for s in flat}
    roots: list[BidSection] = []
    for s in flat:
        s.children = []
        parent = by_id.get(s.parent_id)
        if parent is not None:
            parent.children.append(s)
        else:
            roots.append(s)
    # 兄弟按 ord 排序（平铺本已前序，防御性再排一次）
    for s in by_id.values():
        s.children.sort(key=lambda c: c.ord)
    roots.sort(key=lambda r: r.ord)
    return roots


# ---------------------------------------------------------------------------
def _match_source_refs(title: str, doc_sections: list) -> list[str]:
    """大纲章节标题 × M1 章节标题做 key_overlap 匹配 → 原始章节路径列表。

    只返回重叠度 ≥0.25 的章节 path（"第四章 技术要求 > 4.2 平台功能要求"）。
    """
    if not doc_sections:
        return []
    from ..matching.similarity import key_overlap

    hits: list[str] = []

    def walk(sec, prefix: str):
        path = f"{prefix}{sec.title}" if not prefix else f"{prefix} > {sec.title}"
        if key_overlap(title, sec.title) >= 0.25:
            hits.append(path)
        for child in sec.children:
            walk(child, path)

    for sec in doc_sections:
        walk(sec, "")
    return hits[:5]


__all__ = ["OutlineBuilder", "build_default_outline", "DEFAULT_OUTLINE_ID"]
