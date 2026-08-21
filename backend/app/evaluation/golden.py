# -*- coding: utf-8 -*-
"""
app/evaluation/golden.py —— M7-07 评估基线集（代码常量）

数据来源（人工标注，见样例说明）：
- RETRIEVAL_QUERIES 8 条 ↔ backend/data/samples/企业资料包/样例说明.md:24-33
  「检索基线」小节，逐条：查询 → 期望类别/文件/关键事实。
- REQUIREMENT_BASELINE 15 条 ↔ backend/data/samples/智慧园区项目/样例说明.md:17-33
  「需求基线」小节，逐条：类型/标题/关键词。

口径声明（铁律）：基于项目内离线评估集，不代表通用准确率。
"""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════════════════
# 检索基线（8 条）—— 企业资料包/样例说明.md:24-33
# expect_fact 只作人工核对参考，不参与指标计算（指标只认 file 命中）。
# ═══════════════════════════════════════════════════════════════════════
RETRIEVAL_QUERIES: list[dict] = [
    {"query": "项目经理张伟有多少年经验",
     "expect_category": "人员资质", "expect_file": "04_人员资质.docx",
     "expect_fact": "6年"},
    {"query": "平台支持多少台设备接入",
     "expect_category": "产品", "expect_file": "01_产品介绍.pdf",
     "expect_fact": "2000台"},
    {"query": "公司有多少名员工",
     "expect_category": "公司介绍", "expect_file": "07_公司介绍.pdf",
     "expect_fact": "320人"},
    {"query": "质保期多长时间",
     "expect_category": "售后服务", "expect_file": "06_售后服务.docx",
     "expect_fact": "3年"},
    {"query": "ISO9001证书编号是什么",
     "expect_category": "公司资质", "expect_file": "03_公司资质.docx",
     "expect_fact": "00222Q12345R0S"},
    {"query": "智慧园区项目案例合同额多少",
     "expect_category": "项目案例", "expect_file": "02_项目案例.docx",
     "expect_fact": "1250万"},
    {"query": "系统响应时间多久",
     "expect_category": "技术方案", "expect_file": "05_技术方案.pdf",
     "expect_fact": "3秒"},
    {"query": "历史标书技术方案怎么写的",
     "expect_category": "历史标书", "expect_file": "08_历史标书.docx",
     "expect_fact": "四层架构"},
]

# ═══════════════════════════════════════════════════════════════════════
# 需求基线（15 条）—— 智慧园区项目/样例说明.md:17-33
# expect_file：需求→知识库证据的人工映射。仅标注样例说明中已核实可对齐的
# 条目；"" 表示该需求无 KB 证据（工期/商务/格式类）或未做人工验证——
# RAG 评估只跑 expect_file 非空的子集，并在结果中明示被排除条目。
# ═══════════════════════════════════════════════════════════════════════
REQUIREMENT_BASELINE: list[dict] = [
    {"type": "技术要求", "title": "平台支持人脸识别",
     "keywords": ["人脸识别"], "expect_file": ""},
    {"type": "技术要求", "title": "设备接入能力不低于1000台",
     "keywords": ["设备接入", "1000"], "expect_file": "01_产品介绍.pdf"},
    {"type": "技术要求", "title": "系统并发访问不低于500",
     "keywords": ["并发", "500"], "expect_file": ""},
    {"type": "技术要求", "title": "系统可用性不低于99.9%",
     "keywords": ["可用性", "99.9"], "expect_file": ""},
    {"type": "技术要求", "title": "满足信创要求",
     "keywords": ["信创"], "expect_file": ""},
    {"type": "技术要求", "title": "包含五个子系统",
     "keywords": ["子系统"], "expect_file": ""},
    {"type": "实施要求", "title": "建设工期不超过12个月",
     "keywords": ["工期", "12"], "expect_file": ""},
    {"type": "人员要求", "title": "★项目经理5年以上经验且具备PMP证书",
     "keywords": ["项目经理", "5年", "PMP"], "expect_file": "04_人员资质.docx"},
    {"type": "商务资质", "title": "★近三年类似项目业绩不少于3个且合同额不低于500万",
     "keywords": ["业绩", "500万"], "expect_file": "02_项目案例.docx"},
    {"type": "公司资质", "title": "★具备ISO9001、ISO27001、CMMI3认证",
     "keywords": ["ISO9001", "ISO27001", "CMMI3"], "expect_file": "03_公司资质.docx"},
    {"type": "售后服务", "title": "质保期不少于2年，响应不超过2小时，驻场2人",
     "keywords": ["质保", "响应", "驻场"], "expect_file": "06_售后服务.docx"},
    {"type": "商务要求", "title": "总价包干，预留5%质保金",
     "keywords": ["总价包干", "质保金"], "expect_file": ""},
    {"type": "评标方法", "title": "技术50分商务20分价格30分",
     "keywords": ["技术", "商务", "价格"], "expect_file": ""},
    {"type": "格式要求", "title": "正本1份副本4份",
     "keywords": ["正本", "副本"], "expect_file": ""},
    {"type": "时间要求", "title": "投标截止时间顺延至2024-10-15",
     "keywords": ["截止", "2024-10-15"], "expect_file": ""},
]


def requirement_rag_queries() -> list[dict]:
    """需求→KB 检索评估查询集（15 条全量）。

    expect_file 非空者参与 Recall/MRR；"" 者由 run_retrieval_eval 过滤
    并在结果 excluded_queries 中明示（无 KB 证据的工期/商务/格式类需求）。
    """
    return [{"query": " ".join(r["keywords"]),
             "expect_category": "", "expect_file": r["expect_file"],
             "requirement_title": r["title"], "expect_fact": ""}
            for r in REQUIREMENT_BASELINE]
