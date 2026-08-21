# -*- coding: utf-8 -*-
"""
tests/test_m3_matcher.py —— M3-16 全管线离线端到端（确定性：MockLLM + FakeEmbedding + tmp DB）

企业资料预埋口径（与样例企业包一致）：
    产品 2000台/并发1000/可用性99.95% | 资质 ISO9001+ISO27001+CMMI3+等保三级
    张伟 6年+PMP | 质保3年/2小时到场/驻场2人 | 案例 3个≥500万
    历史标书：工期10个月（仅历史证据 → 工期非 FULL）

覆盖（用户 M3-16 基线）：
- 状态分布：FULL×17 / PARTIAL×6 / MISSING×5 / UNKNOWN×5（各 ≥5、FULL ≥10）
- 预埋基线逐条：设备接入→FULL、项目经理5年→FULL、ISO9001→FULL、
  质保≥2年→FULL、业绩≥3个≥500万→FULL、工期≤12月→非FULL（历史标书
  不能覆盖正式项目资料）、报价/格式→UNKNOWN
- method 分派：RULE（数值/存在性约束）/ HEURISTIC（RAG 证据路径）
- 证据链：REQ-C → MAT → EVD → 溯源非空；FULL 必有 ≥1 条 VALID 证据
- 状态口径：MISSING 仅当资料明确显示不满足（证据存在）；无证据 → UNKNOWN
- 冲突：正式资料 A 2000台 vs 旧版 B 1250台 → 文档新旧仲裁（time）
- 评分细则：is_scoring 不参与匹配；M1 requirements.status 回写已匹配
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import config  # noqa: E402
from app.db import Database  # noqa: E402
from app.services.matching.pipeline import Matcher, run_matching_task  # noqa: E402

from conftest import m3_req, seed_m3_kb  # noqa: E402

TENDER_ID = "T-M3"


# ═══════════════════════════════════════════════════════════════════════
# 企业知识库（预埋正向/反向证据）
# ═══════════════════════════════════════════════════════════════════════
_KB_MATERIALS = [
    {"id": "m1", "category": "产品", "file_name": "01_产品介绍.pdf"},
    {"id": "m2", "category": "公司资质", "file_name": "03_公司资质.docx"},
    {"id": "m3", "category": "人员资质", "file_name": "04_人员资质.docx"},
    {"id": "m4", "category": "售后服务", "file_name": "06_售后服务.docx"},
    {"id": "m5", "category": "项目案例", "file_name": "02_项目案例.docx"},
    {"id": "m6", "category": "历史标书", "file_name": "08_历史标书.docx"},
    {"id": "m7", "category": "公司介绍", "file_name": "07_公司介绍.pdf"},
    {"id": "m9", "category": "产品", "file_name": "09_旧版产品说明.pdf",
     "created_at": "2025-06-01 00:00:00"},
]

_KB_CHUNKS = [
    {"id": "m1_C0001", "material_id": "m1", "category": "产品",
     "file_name": "01_产品介绍.pdf", "page_start": 3,
     "section_path": "第一章 产品能力", "seq": 1,
     "content": "智慧园区综合管理平台V3.2，设备接入支持不少于2000台，"
                "并发1000用户，系统可用性99.95%。"},
    {"id": "m2_C0001", "material_id": "m2", "category": "公司资质",
     "file_name": "03_公司资质.docx", "page_start": 2, "seq": 1,
     "content": "公司具有ISO9001质量管理体系认证、ISO27001信息安全管理体系认证、"
                "CMMI3级认证、等保三级资质。"},
    {"id": "m3_C0001", "material_id": "m3", "category": "人员资质",
     "file_name": "04_人员资质.docx", "page_start": 1, "seq": 1,
     "content": "张伟担任项目经理，具有6年智慧园区项目管理经验，持有PMP证书。"},
    {"id": "m4_C0001", "material_id": "m4", "category": "售后服务",
     "file_name": "06_售后服务.docx", "page_start": 1, "seq": 1,
     "content": "质保期3年，2小时到场，驻场工程师2人，7×24小时热线。"},
    {"id": "m5_C0001", "material_id": "m5", "category": "项目案例",
     "file_name": "02_项目案例.docx", "page_start": 5, "seq": 1,
     "content": "近三年完成智慧园区项目3个，单个合同额均不低于500万元。"},
    {"id": "m6_C0001", "material_id": "m6", "category": "历史标书",
     "file_name": "08_历史标书.docx", "page_start": 8, "seq": 1,
     "content": "本项目工期预计10个月完成。"},
    {"id": "m7_C0001", "material_id": "m7", "category": "公司介绍",
     "file_name": "07_公司介绍.pdf", "page_start": 1, "seq": 1,
     "content": "公司成立于2010年，注册资本5000万元，员工500人。"},
    {"id": "m9_C0001", "material_id": "m9", "category": "产品",
     "file_name": "09_旧版产品说明.pdf", "page_start": 2, "seq": 1,
     "content": "旧版平台设备接入能力为1250台，设备接入上限1250台。"},
]

_KB_CAPABILITIES = [
    {"id": "CAP-0001", "category": "产品", "name": "智慧园区综合管理平台V3.2",
     "description": "", "source_doc": "01_产品介绍.pdf", "source_page": 3,
     "attributes": {"max_devices": "2000", "concurrent_users": "1000",
                    "availability": "99.95%"}},
    {"id": "CAP-0002", "category": "公司资质", "name": "质量管理体系认证证书",
     "source_doc": "03_公司资质.docx", "source_page": 2,
     "attributes": {"certs": ["ISO9001", "ISO27001", "CMMI3", "等保三级"]}},
    {"id": "CAP-0003", "category": "人员资质", "name": "张伟-项目经理",
     "source_doc": "04_人员资质.docx", "source_page": 1,
     "attributes": {"experience_years": "6", "certs": ["PMP"],
                    "role": "项目经理"}},
    {"id": "CAP-0004", "category": "售后服务", "name": "驻场与质保响应承诺",
     "source_doc": "06_售后服务.docx", "source_page": 1,
     "attributes": {"warranty": "3年", "response_time": "2小时到场",
                    "onsite_staff": "2"}},
    {"id": "CAP-0005", "category": "项目案例", "name": "智慧园区项目案例",
     "source_doc": "02_项目案例.docx", "source_page": 5,
     "attributes": {"project_count": "3", "scale": "单个合同额500万元"}},
    {"id": "CAP-0006", "category": "公司介绍", "name": "公司概况",
     "source_doc": "07_公司介绍.pdf", "source_page": 1,
     "attributes": {"registered_capital": "5000万元", "founded_years": "16"}},
    {"id": "CAP-0007", "category": "产品", "name": "接入网关",
     "source_doc": "01_产品介绍.pdf", "source_page": 4,
     "attributes": {"max_devices": "1500-2500"}},
    {"id": "CAP-0009", "category": "产品", "name": "接入与可用性保障",
     "source_doc": "01_产品介绍.pdf", "source_page": 5,
     "attributes": {"availability": "99.9-99.99", "warranty": "2年"}},
    {"id": "CAP-0010", "category": "公司介绍", "name": "员工团队规模",
     "source_doc": "07_公司介绍.pdf", "source_page": 2,
     "attributes": {"employees": "300-600"}},
]


# ═══════════════════════════════════════════════════════════════════════
# 招标原始需求（36 条：预埋基线 + 四状态用例 + 冲突 + 评分细则）
# ═══════════════════════════════════════════════════════════════════════
def _tender_reqs():
    def q(metric, op, value, unit):
        return {"metric": metric, "op": op, "value": value, "unit": unit}

    def add(rid, type_, title, text, quantitative=None, importance="中",
            is_star=False, page=1):
        return m3_req(tender_id=TENDER_ID, rid=rid, type_=type_, title=title,
                      text=text, quantitative=quantitative,
                      importance=importance, is_star=is_star, page=page)

    reqs = []
    # ── 预埋正向基线（企业资料全命中） ──
    reqs.append(add("REQ-0001", "技术要求", "设备接入不少于1000台",
                    "平台应支持不少于 1000 台（个）设备的接入管理。",
                    [q("设备接入", "不少于", "1000", "台")], importance="高", page=12))
    reqs.append(add("REQ-0002", "功能要求", "设备接入支持不少于1000台",
                    "系统须支持 1000 台以上设备同时接入，满足园区设备统一管理需求。",
                    [q("设备接入", "不少于", "1000", "台")], page=31))
    reqs.append(add("REQ-0003", "技术要求", "并发用户数不低于500",
                    "平台并发用户数不低于 500。",
                    [q("并发", "不低于", "500", "户")]))
    reqs.append(add("REQ-0004", "技术要求", "系统年可用性不低于99.9%",
                    "系统年可用性不低于 99.9%。",
                    [q("可用性", "不低于", "99.9", "%")]))
    reqs.append(add("REQ-0005", "资质要求", "投标人须具有ISO9001质量管理体系认证",
                    "投标人须具有 ISO9001 质量管理体系认证。", importance="高"))
    reqs.append(add("REQ-0006", "资质要求", "投标人须具有ISO27001信息安全管理体系认证",
                    "投标人须具有 ISO27001 信息安全管理体系认证。"))
    reqs.append(add("REQ-0007", "资质要求", "投标人须具有CMMI3级认证",
                    "投标人须具有 CMMI3 级认证。"))
    reqs.append(add("REQ-0008", "资质要求", "投标人须具有等级保护三级资质",
                    "投标人须具有等级保护三级资质。"))
    reqs.append(add("REQ-0009", "人员要求", "项目经理经验不少于5年",
                    "★项目经理须具有 5 年以上智慧园区类项目管理经验。",
                    [q("经验", "不少于", "5", "年")], is_star=True, page=45))
    reqs.append(add("REQ-0010", "人员要求", "项目经理须具有PMP证书",
                    "项目经理须具有 PMP 证书。"))
    reqs.append(add("REQ-0011", "售后服务", "质保期不少于2年",
                    "质保期不少于 2 年。", [q("质保", "不少于", "2", "年")]))
    reqs.append(add("REQ-0012", "售后服务", "故障到场时间不超过2小时",
                    "故障到场时间不超过 2 小时。",
                    [q("到场", "不超过", "2", "小时")]))
    reqs.append(add("REQ-0013", "售后服务", "驻场工程师不少于2人",
                    "驻场工程师不少于 2 人。",
                    [q("驻场", "不少于", "2", "人")]))
    reqs.append(add("REQ-0014", "商务要求", "近三年业绩不少于3个类似项目",
                    "近三年承担智慧园区类项目业绩不少于 3 个。",
                    [q("业绩", "不少于", "3", "个")]))
    reqs.append(add("REQ-0015", "商务要求", "单个合同额不低于500万元",
                    "近三年单个合同额不低于 500 万元。",
                    [q("合同额", "不低于", "500", "万元")]))
    # ── 工期：企业仅有历史标书证据 → 非 FULL（历史标书不能覆盖正式资料） ──
    reqs.append(add("REQ-0016", "实施要求", "项目工期不超过12个月",
                    "项目工期不超过 12 个月。",
                    [q("工期", "不超过", "12", "个月")]))
    # ── 无证据 → UNKNOWN（没有证据 ≠ 不满足） ──
    reqs.append(add("REQ-0017", "报价要求", "投标报价不得超过预算上限",
                    "投标报价不得超过招标预算上限。"))
    reqs.append(add("REQ-0018", "投标文件格式", "投标文件正本1份副本4份并胶装",
                    "投标文件须正本 1 份副本 4 份并胶装。"))
    reqs.append(add("REQ-0019", "技术要求", "系统须支持信创国产化适配",
                    "系统须支持信创国产化适配（操作系统、数据库、中间件）。"))
    # ── 商务基线 ──
    reqs.append(add("REQ-0020", "商务要求", "员工人数不少于300人",
                    "投标人员工人数不少于 300 人。",
                    [q("员工", "不少于", "300", "人")]))
    reqs.append(add("REQ-0021", "商务要求", "注册资本不低于3000万元",
                    "投标人注册资本不低于 3000 万元。",
                    [q("注册资本", "不低于", "3000", "万元")]))
    reqs.append(add("REQ-0022", "技术要求", "系统操作响应时间不超过3秒",
                    "系统操作响应时间不超过 3 秒。",
                    [q("响应时间", "不超过", "3", "秒")]))
    # ── RAG 语义匹配路径（无结构化约束 → 证据判定 FULL） ──
    reqs.append(add("REQ-0023", "资质要求", "投标人应具备智慧园区平台建设经验",
                    "投标人应具备智慧园区平台建设经验。"))
    # ── PARTIAL：区间卡部分覆盖 / 多约束部分满足 ──
    reqs.append(add("REQ-0024", "技术要求", "设备接入能力不低于2500台",
                    "平台设备接入能力不低于 2500 台。",
                    [q("设备接入", "不低于", "2500", "台")]))
    reqs.append(add("REQ-0025", "技术要求", "接入规模综合要求",
                    "平台设备接入能力与质保期两项指标须满足要求。",
                    [q("设备接入", "不低于", "2500", "台"),
                     q("质保", "不少于", "2", "年")]))
    reqs.append(add("REQ-0026", "商务要求", "配备员工规模不低于500人",
                    "配备员工规模不低于 500 人。",
                    [q("员工", "不少于", "500", "人")]))
    reqs.append(add("REQ-0027", "技术要求", "高可用性保障99.99%",
                    "系统可用性不低于 99.99%。",
                    [q("可用性", "不低于", "99.99", "%")]))
    reqs.append(add("REQ-0028", "技术要求", "平台设备接入不低于2200台",
                    "平台设备接入能力不低于 2200 台。",
                    [q("设备接入", "不低于", "2200", "台")]))
    # ── MISSING：资料明确显示不满足（有相反证据） ──
    reqs.append(add("REQ-0029", "技术要求", "支持5000台设备接入",
                    "平台须支持 5000 台设备接入。",
                    [q("设备接入", "不少于", "5000", "台")]))
    reqs.append(add("REQ-0030", "售后服务", "提供五年质保服务",
                    "质保期不少于 5 年。", [q("质保", "不少于", "5", "年")]))
    reqs.append(add("REQ-0031", "售后服务", "到场时间不超过30分钟",
                    "故障到场时间不超过 30 分钟。",
                    [q("到场", "不超过", "30", "分钟")]))
    reqs.append(add("REQ-0032", "商务要求", "员工人数不低于1000人",
                    "投标人员工人数不低于 1000 人。",
                    [q("员工", "不低于", "1000", "人")]))
    reqs.append(add("REQ-0033", "商务要求", "类似项目业绩不低于10个",
                    "近三年业绩不少于 10 个。",
                    [q("业绩", "不少于", "10", "个")]))
    # ── 冲突：正式资料 2000台 vs 旧版资料 1250台（同档位 → 文档新旧仲裁） ──
    reqs.append(add("REQ-0034", "技术要求", "接入规模不低于1800台",
                    "平台设备接入能力不低于 1800 台。",
                    [q("设备接入", "不低于", "1800", "台")]))
    # ── 无证据 UNKNOWN（资质类：企业无涉密资质资料） ──
    reqs.append(add("REQ-0035", "资质要求", "投标人须具有涉密信息系统集成资质",
                    "投标人须具有涉密信息系统集成资质。"))
    # ── 评分细则：不参与匹配 ──
    reqs.append(add("REQ-0036", "评分标准", "技术方案评分细则",
                    "技术方案评分细则：优得 10 分，良得 6 分。", importance="低"))
    return reqs


# ═══════════════════════════════════════════════════════════════════════
# 夹具
# ═══════════════════════════════════════════════════════════════════════
def _setup(tmp_env, m3_env) -> Database:
    db = Database(config.DB_PATH)
    db.init_schema()
    seed_m3_kb(db, m3_env, materials=_KB_MATERIALS,
               capabilities=_KB_CAPABILITIES, chunks=_KB_CHUNKS)
    for r in _tender_reqs():
        db.insert("requirements", Database.requirement_to_row(r))
    return db


def _run(db: Database):
    return Matcher(db).match(TENDER_ID)


def _match_by_title(db: Database, title: str) -> dict:
    row = db.query_one(
        "SELECT m.* FROM requirement_matches m "
        "JOIN canonical_requirements c ON c.id = m.requirement_id "
        "WHERE c.tender_id = ? AND c.title = ?", (TENDER_ID, title))
    assert row is not None, f"找不到规范需求: {title}"
    return row


# ═══════════════════════════════════════════════════════════════════════
# 端到端
# ═══════════════════════════════════════════════════════════════════════
def test_pipeline_status_distribution(tmp_env, m3_env):
    """四状态分布：FULL×17 / PARTIAL×6 / MISSING×5 / UNKNOWN×5。"""
    db = _setup(tmp_env, m3_env)
    report = _run(db)
    assert report.total == 33, report.counts
    assert report.counts == {"FULL": 17, "PARTIAL": 6, "MISSING": 5,
                             "UNKNOWN": 5}, report.counts
    # 规范需求收敛：36 原始 → 34 规范（33 匹配 + 1 评分细则），远小于 421
    canonicals = db.query("SELECT * FROM canonical_requirements WHERE tender_id = ?",
                          (TENDER_ID,))
    assert len(canonicals) == 34
    # 落库：matches 与 evidences 行数一致
    n_match = db.query_one("SELECT COUNT(*) AS n FROM requirement_matches "
                           "WHERE tender_id = ?", (TENDER_ID,))["n"]
    n_evd = db.query_one("SELECT COUNT(*) AS n FROM evidences "
                         "WHERE tender_id = ?", (TENDER_ID,))["n"]
    assert n_match == 33 and n_evd > 0


def test_baseline_full_cases(tmp_env, m3_env):
    """预埋基线：设备/并发/可用性/资质/人员/售后/业绩/商务 → FULL。"""
    db = _setup(tmp_env, m3_env)
    _run(db)
    for title in ("设备接入不少于1000台", "并发用户数不低于500",
                  "系统年可用性不低于99.9%",
                  "投标人须具有ISO9001质量管理体系认证",
                  "投标人须具有CMMI3级认证", "投标人须具有等级保护三级资质",
                  "项目经理经验不少于5年", "项目经理须具有PMP证书",
                  "质保期不少于2年", "故障到场时间不超过2小时",
                  "驻场工程师不少于2人", "近三年业绩不少于3个类似项目",
                  "单个合同额不低于500万元", "员工人数不少于300人",
                  "注册资本不低于3000万元",
                  "投标人应具备智慧园区平台建设经验",
                  "接入规模不低于1800台"):
        row = _match_by_title(db, title)
        assert row["status"] == "FULL", f"{title} → {row['status']}（{row['reason']}）"


def test_baseline_iso27001_preserved_after_merge(tmp_env, m3_env):
    """REQ-0005/0006 归并后两条资质约束都不丢（成员约束并集）。"""
    db = _setup(tmp_env, m3_env)
    _run(db)
    row = db.query_one("SELECT * FROM canonical_requirements WHERE tender_id = ? "
                       "AND title = '投标人须具有ISO9001质量管理体系认证'",
                       (TENDER_ID,))
    subjects = {c.get("subject") for c in json.loads(row["constraints"])}
    assert {"ISO9001", "ISO27001"} <= subjects, subjects
    assert _match_by_title(db, "投标人须具有ISO9001质量管理体系认证")["status"] == "FULL"


def test_partial_cases(tmp_env, m3_env):
    """PARTIAL：区间卡部分覆盖 / 多约束部分满足。"""
    db = _setup(tmp_env, m3_env)
    _run(db)
    for title in ("设备接入能力不低于2500台", "接入规模综合要求",
                  "配备员工规模不低于500人", "高可用性保障99.99%",
                  "平台设备接入不低于2200台"):
        row = _match_by_title(db, title)
        assert row["status"] == "PARTIAL", f"{title} → {row['status']}（{row['reason']}）"


def test_missing_cases_have_contrary_evidence(tmp_env, m3_env):
    """MISSING 口径：仅当资料明确显示不满足（证据存在且数值低于要求）。"""
    db = _setup(tmp_env, m3_env)
    _run(db)
    for title in ("支持5000台设备接入", "提供五年质保服务",
                  "到场时间不超过30分钟", "员工人数不低于1000人",
                  "类似项目业绩不低于10个"):
        row = _match_by_title(db, title)
        assert row["status"] == "MISSING", f"{title} → {row['status']}（{row['reason']}）"
        assert json.loads(row["evidence_ids"]), f"{title} 缺相反证据"


def test_unknown_cases_no_evidence(tmp_env, m3_env):
    """没有证据 ≠ 不满足：报价/格式/信创/响应/涉密 → UNKNOWN。"""
    db = _setup(tmp_env, m3_env)
    _run(db)
    for title in ("投标报价不得超过预算上限", "投标文件正本1份副本4份并胶装",
                  "系统须支持信创国产化适配", "系统操作响应时间不超过3秒",
                  "投标人须具有涉密信息系统集成资质"):
        row = _match_by_title(db, title)
        assert row["status"] == "UNKNOWN", f"{title} → {row['status']}（{row['reason']}）"
        assert row["status"] != "MISSING"


def test_duration_not_full_historical_only(tmp_env, m3_env):
    """工期≤12月：仅有历史标书证据（10个月）→ 非 FULL（历史标书不能覆盖正式项目资料）。"""
    db = _setup(tmp_env, m3_env)
    _run(db)
    row = _match_by_title(db, "项目工期不超过12个月")
    assert row["status"] in ("PARTIAL", "UNKNOWN")
    ev_ids = json.loads(row["evidence_ids"])
    assert ev_ids
    rows = db.query("SELECT * FROM evidences WHERE tender_id = ?", (TENDER_ID,))
    evs = {e["id"]: e for e in rows}
    # 支撑证据全部来自历史标书，且置信度低于正式资料档
    assert all(evs[eid]["category"] == "历史标书" for eid in ev_ids)
    assert all(evs[eid]["confidence"] <= 0.6 for eid in ev_ids)


def test_method_dispatch(tmp_env, m3_env):
    """RULE 定案结构化约束；HEURISTIC 定案纯证据路径。"""
    db = _setup(tmp_env, m3_env)
    _run(db)
    assert _match_by_title(db, "设备接入不少于1000台")["method"] == "rule"
    assert _match_by_title(db, "项目经理经验不少于5年")["method"] == "rule"
    assert _match_by_title(db, "投标人应具备智慧园区平台建设经验")["method"] == "heuristic"
    assert _match_by_title(db, "投标报价不得超过预算上限")["method"] == "heuristic"
    # 规则结论 FULL 但冲突仲裁 resolved 后仍按规则定案
    row = _match_by_title(db, "接入规模不低于1800台")
    assert row["method"] == "rule" and row["status"] == "FULL"


def test_conflict_time_arbitration_in_pipeline(tmp_env, m3_env):
    """正式资料 2000台 vs 旧版资料 1250台 → 同档位按文档新旧仲裁（新版胜）。"""
    db = _setup(tmp_env, m3_env)
    _run(db)
    row = _match_by_title(db, "接入规模不低于1800台")
    conflicts = json.loads(row["conflicts"])
    assert conflicts, "应检出证据冲突"
    c = conflicts[0]
    assert c["resolution"] == "time"
    # 胜者证据内容为新版 2000 台（09_旧版 1250 台落败）
    winner = db.query_one("SELECT * FROM evidences WHERE id = ?",
                          (c["winner_evidence_id"],))
    assert "2000" in winner["content"]


def test_evidence_chains_full_have_valid_provenance(tmp_env, m3_env):
    """证据链：REQ-C → MAT → EVD；FULL 必有 ≥1 条 VALID 证据且溯源非空。"""
    db = _setup(tmp_env, m3_env)
    _run(db)
    matches = db.query("SELECT * FROM requirement_matches WHERE tender_id = ?",
                       (TENDER_ID,))
    evs = {e["id"]: e for e in db.query(
        "SELECT * FROM evidences WHERE tender_id = ?", (TENDER_ID,))}
    for m in matches:
        # 编号可溯源：MAT-XXXX 挂在 REQ-C-XXXX 上
        assert m["requirement_id"].startswith("REQ-C-")
        ids = json.loads(m["evidence_ids"])
        assert all(eid in evs for eid in ids), f"{m['id']} 引用不存在的证据"
        if m["status"] == "FULL":
            assert ids, f"{m['requirement_id']} FULL 缺证据"
            valid = [evs[eid] for eid in ids
                     if evs[eid]["validation"] == "VALID"]
            assert valid, f"{m['requirement_id']} FULL 无 VALID 证据"
            for e in valid:
                # 四元溯源至少一项非空（资料/章节/页码/块）
                assert (e["document_id"] or e["section_path"]
                        or e["page"] or e["block_id"]), e["id"]
    # 所有入库证据均已回验 VALID（内容来自 chunk/卡片原文）
    assert all(e["validation"] == "VALID" for e in evs.values())
    assert all(e["matched_text"] for e in evs.values())


def test_scoring_rules_excluded_and_m1_writeback(tmp_env, m3_env):
    """评分细则不参与匹配；已匹配原始需求状态回写 已匹配。"""
    db = _setup(tmp_env, m3_env)
    _run(db)
    scoring = db.query_one("SELECT * FROM canonical_requirements WHERE tender_id = ? "
                           "AND is_scoring = 1", (TENDER_ID,))
    assert scoring is not None and scoring["title"] == "技术方案评分细则"
    n = db.query_one("SELECT COUNT(*) AS n FROM requirement_matches m "
                     "JOIN canonical_requirements c ON c.id = m.requirement_id "
                     "WHERE c.is_scoring = 1")["n"]
    assert n == 0
    # M1 需求回写：33 条匹配 → 已匹配；评分细则保持待响应
    rows = db.query("SELECT status, COUNT(*) AS n FROM requirements "
                    "WHERE tender_id = ? GROUP BY status", (TENDER_ID,))
    by_status = {r["status"]: r["n"] for r in rows}
    assert by_status.get("已匹配") == 35, by_status
    assert by_status.get("待响应") == 1, by_status


def test_run_matching_task_state_machine(tmp_env, m3_env):
    """后台任务入口：matching_runs 状态机 匹配中 → 已完成。"""
    db = _setup(tmp_env, m3_env)
    db.insert("tenders", {"id": TENDER_ID, "name": "智慧园区平台建设项目",
                          "created_at": "2026-01-01 00:00:00"})
    result = run_matching_task(TENDER_ID)
    assert result["status"] == "已完成", result
    run = db.query_one("SELECT * FROM matching_runs WHERE tender_id = ?",
                       (TENDER_ID,))
    assert run["status"] == "已完成"
    assert run["canonical_count"] == 34 and run["match_count"] == 33


def test_pipeline_idempotent_rerun(tmp_env, m3_env):
    """重跑幂等：先清表重建，结果一致（不产生重复编号）。"""
    db = _setup(tmp_env, m3_env)
    r1 = _run(db)
    r2 = _run(db)
    assert r1.counts == r2.counts
    n_evd = db.query_one("SELECT COUNT(*) AS n FROM evidences "
                         "WHERE tender_id = ?", (TENDER_ID,))["n"]
    n_match = db.query_one("SELECT COUNT(*) AS n FROM requirement_matches "
                           "WHERE tender_id = ?", (TENDER_ID,))["n"]
    assert n_match == 33
    ids = [e["evidence_id"] for e in db.query(
        "SELECT id AS evidence_id FROM evidences WHERE tender_id = ? "
        "ORDER BY id",
        (TENDER_ID,))]
    assert len(ids) == n_evd and len(set(ids)) == n_evd, "证据编号重复"


def test_response_table_builder_json_and_markdown(tmp_env, m3_env):
    """响应表（M3-15）：JSON 33 行 + 计数一致；Markdown 含表格与逐条证据链。

    回归锚点：evidences 主键是 id（无 evidence_id 列），build() 的
    ORDER BY 曾误用 evidence_id 导致端点 500 —— 本测试兜底。
    """
    db = _setup(tmp_env, m3_env)
    _run(db)
    from app.services.matching.report import ResponseTableBuilder
    builder = ResponseTableBuilder(db)
    payload = json.loads(builder.to_json(TENDER_ID))
    assert payload["total"] == 33
    assert payload["counts"] == {"FULL": 17, "PARTIAL": 6, "MISSING": 5,
                                 "UNKNOWN": 5}
    md = builder.to_markdown(TENDER_ID)
    assert "需求响应表" in md and "逐条证据链" in md and "REQ-C-" in md
    assert "|" in md  # 响应表主体是 markdown 表格
