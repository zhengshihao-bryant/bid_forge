# -*- coding: utf-8 -*-
"""
app/services/capability_extractor.py —— 能力卡提取 + 知识库后台处理任务（M2 核心）

管线（run_kb_task 状态机）：

    解析产物 ──▶ 清旧数据（chunks/卡片/Milvus，重跑幂等）
            ──▶ 切块入库（kb_chunking.build_chunks，干净文本）
            ──▶ 嵌入回填 + 向量 upsert（失败仅 index_status=degraded，不整任务失败）
            ──▶ 能力卡提取（历史标书跳过，只切块嵌入）
            ──▶ 已完成（progress 摘要含块数/卡数/丢弃数）

能力卡提取复用 M1 extraction.py 的窗口机制（母版复用）：
- _build_windows ≤4000 字窗口（页边界对齐；【第p页】标记只存在于窗口临时文本）
- finish_reason=="length" → 半窗递归（_make_window_from_blocks）
- 3 次重试、坏条丢弃计数、去重键 (category, 去空白 name)

与 M1 差异（能力卡特有的规则）：
- 提示词按类别分派：7 类 attributes 模板（产品/案例/资质/人员/方案/售后/介绍）
- 编号全局递增：CAP-{全局 max+1:04d} 起——重跑单资料若从 0001 起会与其它
  资料的卡片撞号（capabilities.id 主键），故查询全表现有最大编号
- 卡片溯源 = source_doc(文件名) + source_page：capabilities 表 M1 定死无
  block_id 列（块级出处由 kb_chunks.block_ids 保留，卡片只管事实）
- 历史标书（HISTORICAL_BID）不提取卡片：完整标书样貌只做向量检索语料
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

from .. import config
from ..db import Database
from ..schemas import Capability, CapabilityCategory, ParsedDocument, now_str
from .embedding import create_embedding
from .extraction import (
    ExtractionWindow, ProgressCallback, _build_windows, _make_window_from_blocks,
)
from .kb_chunking import build_chunks
from .kb_versions import record_version
from .llm import create_llm_client, llm_call_context
from .task_tracker import (fail_task, start_task, succeed_task,
                           update_progress)
from .vector_store import SqliteVectorStore, get_milvus_store

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# 提示词（按类别分派；事实约束铁律沿用 M1 extraction._SYSTEM_PROMPT 口径）
# ═══════════════════════════════════════════════════════════════════════
_IRON_LAWS = """你是一名企业资料分析师，负责从企业资料中提取【能力卡片】——结构化的事实条目，供投标匹配与标书生成引用。

铁律（事实约束）：
1. 只提取原文中明确写出的内容，绝不补充、推测或润色数字
2. 量化指标必须原样保留：数值、比较符（≥/≤/不少于/不高于）、单位，填入 quantitative 数组每项 {metric, op, value, unit}
3. 无法确定的字段留空（空字符串或空数组），不要编造
4. category 必须为给定枚举值
5. 一张卡 = 一个可独立引用的能力实体（如一个产品、一个证书、一位人员、一个项目）
6. 目录、页眉页脚、纯宣传语（"行业领先""一流品质"等无信息量表述）不提取
7. 每条给出原文页码 page，用于回溯核对"""

# 类别 → attributes 字段模板 + 示例（7 类；历史标书不提取）
_CATEGORY_SPECS: dict[str, dict[str, str]] = {
    "产品": {
        "fields": "product（产品名）、version（版本号）、key_capabilities（关键能力列表）、quantitative（量化指标数组）、certifications（认证/检测列表）",
        "example": '{"category": "产品", "name": "智慧园区综合管理平台", "description": "园区综合管理软件产品", "attributes": {"product": "智慧园区综合管理平台", "version": "V3.2", "key_capabilities": ["视频监控", "人脸门禁"], "quantitative": [{"metric": "设备接入", "op": "不少于", "value": "2000", "unit": "个"}], "certifications": ["GB/T 28181"]}, "page": 3}',
    },
    "项目案例": {
        "fields": "project_name（项目名）、client（客户）、industry（行业）、year（年份）、scale（规模）、key_features（关键建设内容列表）、achievements（成果/奖项列表）",
        "example": '{"category": "项目案例", "name": "XX智慧园区一期", "description": "2021年交付的智慧园区项目", "attributes": {"project_name": "XX智慧园区一期", "client": "XX科技有限公司", "industry": "园区", "year": "2021", "scale": "合同额1250万元", "key_features": ["视频监控"], "achievements": []}, "page": 1}',
    },
    "公司资质": {
        "fields": "cert_name（证书名）、cert_no（证书编号）、issuer（发证机构）、issued_at（发证日期）、valid_until（有效期至）、scope（认证范围）",
        "example": '{"category": "公司资质", "name": "ISO9001质量管理体系认证", "description": "质量管理体系认证证书", "attributes": {"cert_name": "ISO9001质量管理体系认证", "cert_no": "00222Q12345R0S", "issuer": "中国质量认证中心", "issued_at": "2022-05-10", "valid_until": "2025-05-09", "scope": "软件开发与服务"}, "page": 2}',
    },
    "人员资质": {
        "fields": "person_name（姓名）、role（职务/角色）、experience_years（相关经验年限）、certs（证书列表）、projects（代表项目列表）",
        "example": '{"category": "人员资质", "name": "张伟-项目经理", "description": "PMP、信息系统项目管理师", "attributes": {"person_name": "张伟", "role": "项目经理", "experience_years": "6", "certs": ["PMP", "信息系统项目管理师"], "projects": ["XX智慧园区一期"]}, "page": 1}',
    },
    "技术方案": {
        "fields": "solution_name（方案名）、architecture（架构）、modules（功能模块列表）、standards（遵循标准列表）、quantitative（量化指标数组）",
        "example": '{"category": "技术方案", "name": "园区综合管理平台技术方案", "description": "四层架构园区管理方案", "attributes": {"solution_name": "园区综合管理平台技术方案", "architecture": "四层架构", "modules": ["视频监控", "门禁管理"], "standards": ["GB/T 28181", "等保三级"], "quantitative": [{"metric": "响应时间", "op": "≤", "value": "3", "unit": "秒"}]}, "page": 4}',
    },
    "售后服务": {
        "fields": "service_items（服务内容列表）、warranty（质保期）、response_time（响应时间）、onsite_staff（驻场人员）",
        "example": '{"category": "售后服务", "name": "质保与运维服务", "description": "质保3年、2小时到场", "attributes": {"service_items": ["质保期内免费维修"], "warranty": "3年", "response_time": "2小时到场", "onsite_staff": "2人"}, "page": 1}',
    },
    "公司介绍": {
        "fields": "company_name（公司名）、founded_year（成立年份）、registered_capital（注册资本）、employees（员工总数）、rd_staff（研发人员数）、revenue（营收）、clients（客户数量/名单）",
        "example": '{"category": "公司介绍", "name": "XX科技有限公司", "description": "成立于2012年的软件企业", "attributes": {"company_name": "XX科技有限公司", "founded_year": "2012", "registered_capital": "5000万元", "employees": "320", "rd_staff": "80", "revenue": "近三年营收1.2亿元", "clients": "60+"}, "page": 1}',
    },
}

_OUTPUT_FORMAT = """输出 JSON 格式（务必只输出 JSON）：
{"capabilities": [{"category": "枚举之一", "name": "一句话名称(≤30字)", "description": "一句话描述(≤100字)", "attributes": {字段模板}, "page": 页码数字}]}"""

_USER_TEMPLATE = """【资料类别】{category}
【文件】{file_name}
【章节路径】{section_path}
【页码范围】{page_range}
【原文】（【第p页】为页码标记，填写 page 字段时以此为准）

{text}

请从以上原文提取该类别的能力卡片，输出 JSON。"""


def _system_prompt(category: CapabilityCategory) -> str:
    spec = _CATEGORY_SPECS[category.value]
    return f"{_IRON_LAWS}\n\n本资料的类别：{category.value}。attributes 字段模板：\n{spec['fields']}\n\n示例（字段值仅为示范格式）：\n{spec['example']}\n\n{_OUTPUT_FORMAT}"


# ═══════════════════════════════════════════════════════════════════════
# 校验与归一
# ═══════════════════════════════════════════════════════════════════════
def _coerce_cap_category(raw: Any) -> Optional[CapabilityCategory]:
    """类别模糊归一（复用 extraction._coerce_type 风格）；匹配不上返回 None（该条丢弃）。"""
    if not raw:
        return None
    raw = str(raw).strip()
    for c in CapabilityCategory:
        if c.value == raw:
            return c
    for c in CapabilityCategory:
        if c.value in raw or raw in c.value:
            return c
    return None


def _normalize_attributes(raw: dict) -> dict:
    """attributes 透传；quantitative 数组逐项归一为 {metric, op, value, unit} 字符串。"""
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if k == "quantitative" and isinstance(v, list):
            out[k] = [
                {
                    "metric": str(i.get("metric") or "").strip(),
                    "op": str(i.get("op") or "").strip(),
                    "value": str(i.get("value") or "").strip(),
                    "unit": str(i.get("unit") or "").strip(),
                }
                for i in v if isinstance(i, dict)
            ]
        else:
            out[k] = v
    return out


# ═══════════════════════════════════════════════════════════════════════
# 提取器
# ═══════════════════════════════════════════════════════════════════════
class CapabilityExtractor:
    """能力卡提取器：窗口切分 → 按类别 LLM 提取 → 校验 → 去重 + 全局编号。"""

    def __init__(self, client=None, window_chars: Optional[int] = None,
                 progress_cb: Optional[ProgressCallback] = None):
        self.client = client or create_llm_client()
        self.window_chars = window_chars or config.EXTRACT_WINDOW_CHARS
        self.progress_cb = progress_cb

    # ------------------------------------------------------------------
    def extract(self, file_name: str, category: CapabilityCategory,
                parsed_doc: ParsedDocument, start_no: int = 1) -> tuple[list[Capability], dict]:
        """单资料全量提取。start_no = 全局编号起点（_next_cap_number(db)）。"""
        stats = {"windows": 0, "llm_calls": 0, "dropped_items": 0,
                 "retries": 0, "windows_failed": 0}
        if category == CapabilityCategory.HISTORICAL_BID:
            logger.info("历史标书跳过能力卡提取（只切块嵌入）: %s", file_name)
            stats["skipped"] = True
            return [], stats

        windows = _build_windows(parsed_doc, self.window_chars)
        stats["windows"] = len(windows)
        all_caps: list[Capability] = []
        for i, w in enumerate(windows):
            caps, wstats = self._extract_window(file_name, category, w)
            all_caps.extend(caps)
            for k in ("llm_calls", "dropped_items", "retries", "windows_failed"):
                stats[k] += wstats[k]
            if self.progress_cb:
                loc = (f"第{w.page_start}-{w.page_end}页" if w.page_start is not None
                       else "无页码（以章节路径为准）")
                self.progress_cb(f"{w.file_name} {w.section_path[:40]} {loc}",
                                 i + 1, len(windows))

        caps = self._dedupe_and_number(all_caps, start_no)
        logger.info("能力卡提取完成 %s: %d 张（%d 窗口 / %d 次 LLM 调用 / %d 次重试 / 丢弃 %d 条）",
                    file_name, len(caps), stats["windows"], stats["llm_calls"],
                    stats["retries"], stats["dropped_items"])
        return caps, stats

    # ------------------------------------------------------------------
    def _extract_window(self, file_name: str, category: CapabilityCategory,
                        w: ExtractionWindow) -> tuple[list[Capability], dict]:
        stats = {"llm_calls": 0, "dropped_items": 0, "retries": 0, "windows_failed": 0}
        page_range = (f"第 {w.page_start}-{w.page_end} 页" if w.page_start is not None
                      else "该文件无页码（章节路径为准）")
        user = _USER_TEMPLATE.format(
            category=category.value, file_name=w.file_name,
            section_path=w.section_path, page_range=page_range, text=w.text)

        for _attempt in range(3):
            with llm_call_context("kb_extract"):
                resp = self.client.chat_json(_system_prompt(category), user)
            stats["llm_calls"] += 1
            if resp is None:
                stats["retries"] += 1
                continue
            if resp.get("finish_reason") == "length":
                # 输出截断：半窗切分后分别提取（递归）
                logger.warning("窗口输出截断，切半窗重试: %s %s", w.file_name, w.section_path)
                return self._extract_split(file_name, category, w, stats)
            data = resp.get("data") or {}
            raw_items = data.get("capabilities")
            if not isinstance(raw_items, list):
                stats["retries"] += 1
                continue
            caps, dropped = self._validate_items(file_name, w, raw_items)
            stats["dropped_items"] += dropped
            return caps, stats

        stats["windows_failed"] += 1
        logger.warning("窗口提取失败（3 次重试后放弃）: %s %s", w.file_name, w.section_path)
        return [], stats

    def _extract_split(self, file_name: str, category: CapabilityCategory,
                       w: ExtractionWindow, stats: dict) -> tuple[list[Capability], dict]:
        mid = len(w.blocks) // 2
        if mid < 2:
            return [], stats
        caps_all: list[Capability] = []
        for half in (w.blocks[:mid], w.blocks[mid:]):
            sub = _make_window_from_blocks(w, half)
            caps, s2 = self._extract_window(file_name, category, sub)
            caps_all.extend(caps)
            for k in stats:
                stats[k] += s2[k]
        return caps_all, stats

    # ------------------------------------------------------------------
    def _validate_items(self, file_name: str, w: ExtractionWindow,
                        raw_items: list) -> tuple[list[Capability], int]:
        caps: list[Capability] = []
        dropped = 0
        for raw in raw_items:
            if not isinstance(raw, dict):
                dropped += 1
                continue
            ccat = _coerce_cap_category(raw.get("category"))
            if ccat is None:
                dropped += 1
                continue
            name = str(raw.get("name") or "").strip()
            if not name:
                dropped += 1
                continue
            attrs = raw.get("attributes")
            if not isinstance(attrs, dict):
                dropped += 1
                continue
            page = raw.get("page")
            if not isinstance(page, int) or page <= 0:
                page = w.page_start
            caps.append(Capability(
                id="",  # 去重编号后回填（全局递增，见 _dedupe_and_number）
                category=ccat,
                name=name[:100],
                attributes=_normalize_attributes(attrs),
                description=str(raw.get("description") or "").strip()[:500],
                source_doc=file_name,
                source_page=page,
                created_at=now_str(),
            ))
        return caps, dropped

    # ------------------------------------------------------------------
    @staticmethod
    def _dedupe_and_number(caps: list[Capability], start_no: int) -> list[Capability]:
        """去重键 (category, 去空白 name)；编号全局递增 CAP-{start_no+i:04d}。"""
        seen: set[tuple] = set()
        out: list[Capability] = []
        for cap in caps:
            key = (cap.category.value, re.sub(r"\s+", "", cap.name))
            if key in seen:
                continue
            seen.add(key)
            cap.id = f"CAP-{start_no + len(out):04d}"
            out.append(cap)
        return out


# ═══════════════════════════════════════════════════════════════════════
# 后台任务入口（每任务独立 DB 连接；状态落库防重启丢状态）
# ═══════════════════════════════════════════════════════════════════════
_CAP_ID_RE = re.compile(r"^CAP-(\d+)$")


def _next_cap_number(db: Database) -> int:
    """全局最大卡片编号 + 1。重跑单资料若从 0001 起会与其它资料的卡片撞号。"""
    max_n = 0
    for r in db.query("SELECT id FROM capabilities", ()):
        m = _CAP_ID_RE.match(r["id"] or "")
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n + 1


def run_kb_task(material_id: str, task_id: str = "") -> dict:
    """后台任务入口：清旧数据 → 切块入库 → 嵌入 + upsert（失败仅降级）→ 能力卡提取。

    M7-05：task_id 非空时同步任务中心状态（start/progress/succeed/fail）。
    M7-04：完成时写 knowledge_versions 行（material_reprocess）——
    重处理是知识库的版本变更事件，追溯链由此接续。
    """
    db = Database(config.DB_PATH)
    mat = db.query_one("SELECT * FROM kb_materials WHERE id = ?", (material_id,))
    if not mat:
        logger.error("知识库处理任务：资料不存在 material_id=%s", material_id)
        return {"material_id": material_id, "error": "资料不存在"}

    if task_id:
        start_task(db, task_id)
    db.update("kb_materials", "id", material_id,
              {"process_status": "处理中", "process_progress": "读取解析产物"})
    try:
        category = CapabilityCategory(mat["category"])
        file_name = mat["file_name"]

        # 1. 解析产物
        pfile = Path(config.KB_PARSED_DIR) / material_id / mat["parsed_file"]
        if not pfile.exists():
            raise ValueError(f"解析产物缺失: {pfile}（请删除资料后重新上传）")
        doc = ParsedDocument.model_validate_json(pfile.read_text(encoding="utf-8"))

        # 2. 清旧数据（chunks/卡片/Milvus 全清，重跑幂等）
        db.execute("DELETE FROM kb_chunks WHERE material_id = ?", (material_id,))
        db.execute("DELETE FROM capabilities WHERE source_doc = ?", (file_name,))
        milvus = get_milvus_store()
        if milvus is not None:
            try:
                milvus.delete_material(material_id)
            except Exception as e:  # noqa: BLE001 —— 旧数据删除 best-effort，不影响重跑
                logger.warning("Milvus 旧数据删除失败（不影响重跑）: %s", str(e)[:200])

        def progress_cb(msg: str, done: int, total: int) -> None:
            db.update("kb_materials", "id", material_id,
                      {"process_progress": f"[{done}/{total}] {msg}"})
            if task_id:
                update_progress(db, task_id, done, total, msg)

        # 3. 切块入库
        chunks = build_chunks(doc, material_id, file_name, category,
                              max_chars=config.KB_CHUNK_CHARS)
        for c in chunks:
            db.insert("kb_chunks", Database.chunk_to_row(c))
        db.update("kb_materials", "id", material_id,
                  {"process_progress": f"{len(chunks)} 块已入库，嵌入中"})

        # 4. 嵌入 + 向量写入（失败仅 index_status=degraded，不整任务失败）
        index_status = "done"
        try:
            rows = [
                {"chunk_id": c.id, "material_id": c.material_id,
                 "category": c.category.value, "file_name": c.file_name,
                 "section_path": c.section_path,
                 "page_start": c.page_start, "page_end": c.page_end,
                 "block_ids": c.block_ids, "content": c.content}
                for c in chunks
            ]
            vecs = create_embedding().embed([c.content for c in chunks])
            for r, v in zip(rows, vecs):
                r["embedding"] = v
            SqliteVectorStore(db).upsert(rows)   # 事实源向量回填（降级检索/重建索引用）
            if milvus is not None:
                milvus.upsert(rows)              # 可重建索引（bid_chunks）
        except Exception as e:  # noqa: BLE001 —— 嵌入失败不整任务失败，SQLite 检索仍可用
            index_status = "degraded"
            logger.warning("向量索引写入失败（index_status=degraded）: %s", str(e)[:300])

        # 5. 能力卡提取（历史标书只切块嵌入，不提取卡片）
        caps: list[Capability] = []
        cap_stats = {"windows": 0, "llm_calls": 0, "dropped_items": 0,
                     "retries": 0, "windows_failed": 0}
        if category != CapabilityCategory.HISTORICAL_BID:
            extractor = CapabilityExtractor(create_llm_client(), progress_cb=progress_cb)
            caps, cap_stats = extractor.extract(file_name, category, doc,
                                                start_no=_next_cap_number(db))
            if mat["file_type"] == "docx":
                # docx 无页码（Word 页面属渲染层），LLM 返回的 page 是臆测值 → 置空，
                # 与 SourceAnchor 口径一致：docx 以章节路径 + block_ids 溯源
                for cap in caps:
                    cap.source_page = None
            for cap in caps:
                db.insert("capabilities", Database.capability_to_row(cap))

        # 6. 收尾
        summary = (f"{len(chunks)} 块 / {len(caps)} 张卡片 / 索引 {index_status} / "
                   f"丢弃 {cap_stats['dropped_items']} 条")
        db.update("kb_materials", "id", material_id, {
            "process_status": "已完成",
            "process_progress": summary,
            "chunk_count": len(chunks),
            "capability_count": len(caps),
            "index_status": index_status,
        })
        logger.info("知识库处理完成 material=%s: %d 块 / %d 卡 / 索引 %s",
                    material_id, len(chunks), len(caps), index_status)
        # M7-04：重处理完成 = 知识库版本变更事件
        record_version(db, change_type="material_reprocess",
                       material_id=material_id,
                       summary=f"file={file_name} {summary}")
        if task_id:
            succeed_task(db, task_id, total=len(chunks), done=len(chunks),
                         progress=summary)
        return {"material_id": material_id, "chunks": len(chunks),
                "capabilities": len(caps), "index_status": index_status,
                "stats": cap_stats}
    except Exception as e:
        logger.exception("知识库处理失败 material=%s", material_id)
        db.update("kb_materials", "id", material_id,
                  {"process_status": "失败", "process_progress": str(e)[:500]})
        if task_id:
            fail_task(db, task_id, error=str(e))
        return {"material_id": material_id, "error": str(e)}


__all__ = ["CapabilityExtractor", "run_kb_task"]
