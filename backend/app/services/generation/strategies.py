# -*- coding: utf-8 -*-
"""
generation/strategies.py —— M4-08 内容类型与生成策略

| SectionType | 策略               | 说明 |
|-------------|--------------------|------|
| 固定格式    | FixedFormatStrategy | 封面/目录/投标函/报价表：模板直接渲染（报价绝不编数字） |
| 事实型      | FactTemplateStrategy | 公司概况/人员/质保：模板 + 能力卡/证据回填 |
| 方案型      | SolutionLLMStrategy | 技术/实施/售后方案：LLM + 证据（chat_json 结构化） |
| 表格型      | TableTemplateStrategy | 指标响应表/资质表/业绩表：从能力卡生成行 |

strategy_for(section) 按 section_type 分派；方案型 LLM 失败/None → 回退
FactTemplate（不产生空章节）。所有策略产物都是结构化 Paragraph（M4-06），
事实约束由 generator 校验器兜底。
"""
from __future__ import annotations

import logging
from typing import Optional

from ..llm import create_llm_client, llm_call_context
from .context import GenerationContext
from .models import FactClass, Paragraph, SectionType
from .prompts import SOLUTION_SYSTEM, build_solution_user_prompt

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _p(type_: str, text: str = "", fact_class: FactClass = FactClass.INFERENCE,
       level: int = 0, table: Optional[list] = None,
       evidence_ids: Optional[list] = None) -> Paragraph:
    return Paragraph(type=type_, text=text, level=level, table=table or [],
                     fact_class=fact_class, evidence_ids=evidence_ids or [])


def _merge_card_attrs(cards: list) -> dict:
    out: dict = {}
    for c in cards:
        for k, v in c.attributes.items():
            out.setdefault(k, v)
    return out


def _req_status(ctx) -> dict:
    return ctx.metadata.get("req_statuses", {})


# ---------------------------------------------------------------------------
# 固定格式（M4-08：模板直接渲染，不产生企业事实断言）
# ---------------------------------------------------------------------------
class FixedFormatStrategy:
    def generate(self, ctx: GenerationContext) -> list[Paragraph]:
        name = ctx.metadata.get("tender_name", "")
        sid = ctx.section.id
        if sid == "CH-01":                       # 封面
            return [
                _p("paragraph", f"# {name or '项目投标文件'}", FactClass.INFERENCE, level=1),
                _p("paragraph", f"项目名称：{name or '＿＿＿＿＿＿＿＿'}", FactClass.INFERENCE),
                _p("paragraph", "投标人：＿＿＿＿＿＿＿＿＿＿", FactClass.INFERENCE),
                _p("paragraph", "日期：＿＿＿＿年＿＿月＿＿日", FactClass.INFERENCE),
            ]
        if sid == "CH-02":                       # 目录（组装器生成）
            return [_p("paragraph",
                       "（目录由文档组装器按章节顺序自动生成，此处为占位）",
                       FactClass.INFERENCE)]
        if sid == "CH-03":                       # 投标函及授权书
            return [
                _p("paragraph", f"致：{name or '＿＿＿＿＿＿（招标人）'}", FactClass.INFERENCE),
                _p("paragraph",
                   "我司郑重承诺：愿按招标文件规定的条件与要求参与本次投标，"
                   "并保证投标文件所载内容真实、合法、有效。",
                   FactClass.INFERENCE),
                _p("paragraph",
                   "本投标函与授权书为模板章节，具体格式、签署与盖章"
                   "以招标文件《投标文件格式》要求为准。",
                   FactClass.INFERENCE),
            ]
        if sid == "CH-04-5":                     # 报价表（绝不编数字）
            return [
                _p("heading", "报价表", level=2),
                _p("table", "", fact_class=FactClass.INFERENCE, table=[
                    ["序号", "项目", "单位", "数量", "单价（元）", "合价（元）"],
                    ["", "", "", "", "", ""],
                ]),
                _p("paragraph",
                   "本报价表由投标人依据招标文件《报价要求》商务决策后填写，"
                   "当前版本不包含价格，禁止编造报价。",
                   FactClass.INFERENCE),
            ]
        return [_p("paragraph", f"（固定格式章节「{ctx.section.title}」模板输出）",
                   FactClass.INFERENCE)]


# ---------------------------------------------------------------------------
# 事实型（M4-08：模板 + 能力卡/证据回填）
# ---------------------------------------------------------------------------
def _company_profile(ctx: GenerationContext) -> list[Paragraph]:
    attrs = _merge_card_attrs(ctx.capability_cards)
    parts = []
    if attrs.get("registered_capital"):
        parts.append(f"注册资本{attrs['registered_capital']}")
    if attrs.get("founded_years"):
        parts.append(f"成立已{attrs['founded_years']}年")
    if attrs.get("employees"):
        parts.append(f"员工规模{attrs['employees']}人")
    paras = [_p("heading", "公司概况与综合实力", level=2)]
    paras.append(_p("paragraph", "我司" + "，".join(parts) + "。", FactClass.FACT)
                 if parts else
                 _p("paragraph", "（公司基本信息待补充确认）", FactClass.INFERENCE))
    paras.append(_p("paragraph",
                    "公司专注智慧园区领域，具备成熟的产品研发与项目实施交付能力。",
                    FactClass.INFERENCE))
    return paras


def _personnel(ctx: GenerationContext) -> list[Paragraph]:
    attrs = _merge_card_attrs(ctx.capability_cards)
    name = next((c.name.split("-")[0] for c in ctx.capability_cards if c.name),
                "项目经理")
    exp = attrs.get("experience_years")
    certs = attrs.get("certs")
    paras = [_p("heading", "组织机构与人员配备", level=2)]
    if exp or certs:
        line = f"{name}具有{exp}年智慧园区项目管理经验" if exp else f"{name}具备项目管理经验"
        if certs:
            line += "，持有" + "、".join(certs) + "证书"
        line += "，担任本项目项目经理。"
        paras.append(_p("paragraph", line, FactClass.FACT))
    else:
        paras.append(_p("paragraph", "（项目人员信息待补充确认）", FactClass.INFERENCE))
    paras.append(_p("paragraph",
                    "项目组将配置实施、开发、测试、运维等专职岗位，由项目经理统筹协调，"
                    "保障项目按期保质交付。",
                    FactClass.INFERENCE))
    return paras


def _warranty(ctx: GenerationContext) -> list[Paragraph]:
    attrs = _merge_card_attrs(ctx.capability_cards)
    parts = []
    if attrs.get("warranty"):
        parts.append(f"质保期{attrs['warranty']}")
    if attrs.get("response_time"):
        parts.append(f"故障{attrs['response_time']}")
    if attrs.get("onsite_staff"):
        parts.append(f"驻场工程师{attrs['onsite_staff']}人")
    paras = [_p("heading", "质保期与响应承诺", level=2)]
    paras.append(_p("paragraph", "我司提供" + "，".join(parts) + "，7×24小时技术支持热线。",
                    FactClass.FACT) if parts else
                 _p("paragraph", "（质保与响应承诺待补充确认）", FactClass.INFERENCE))
    paras.append(_p("paragraph",
                    "质保期内免费维修与更换，响应时效与服务等级详见商务文件。",
                    FactClass.INFERENCE))
    return paras


def _onsite(ctx: GenerationContext) -> list[Paragraph]:
    attrs = _merge_card_attrs(ctx.capability_cards)
    paras = [_p("heading", "驻场与运维方案", level=2)]
    staff = attrs.get("onsite_staff")
    if staff:
        paras.append(_p("paragraph",
                        f"我司配置驻场工程师{staff}人，提供7×24小时在线支持与定期巡检。",
                        FactClass.FACT))
    else:
        paras.append(_p("paragraph", "（驻场与运维方案待补充确认）", FactClass.INFERENCE))
    paras.append(_p("paragraph",
                    "运维服务将建立工单闭环管理机制，重大故障快速响应、定期回访。",
                    FactClass.INFERENCE))
    return paras


def _generic_fact(ctx: GenerationContext) -> list[Paragraph]:
    paras = [_p("heading", ctx.section.title, level=2)]
    for c in ctx.capability_cards:
        attrs = "；".join(f"{k}={v}" for k, v in c.attributes.items())
        if attrs:
            paras.append(_p("paragraph", f"{c.name}：{attrs}。", FactClass.FACT))
        elif c.description:
            paras.append(_p("paragraph", c.description, FactClass.FACT))
    if len(paras) == 1:
        paras.append(_p("paragraph", "（本章节企业信息待补充确认）", FactClass.INFERENCE))
    return paras


class FactTemplateStrategy:
    """事实型章节：模板 + 能力卡回填（LLM 方案型失败时的兜底也用它）。"""

    _TEMPLATES = {
        "CH-04-1": _company_profile,
        "CH-06-2": _personnel,
        "CH-07-2": _warranty,
        "CH-07-3": _onsite,
    }

    def generate(self, ctx: GenerationContext) -> list[Paragraph]:
        fn = self._TEMPLATES.get(ctx.section.id, _generic_fact)
        return fn(ctx)


# ---------------------------------------------------------------------------
# 表格型（M4-08：结构化数据 + 模板）
# ---------------------------------------------------------------------------
def _cert_table(ctx: GenerationContext) -> list[Paragraph]:
    certs: list[str] = []
    for c in ctx.capability_cards:
        certs.extend(c.attributes.get("certs", []) or [])
    seen: set[str] = set()
    rows = [["序号", "资质证书", "状态"]]
    for i, cert in enumerate(certs, 1):
        if cert in seen:
            continue
        seen.add(cert)
        rows.append([str(i), cert, "具备"])
    paras = [_p("heading", "企业资质与证书", level=2)]
    paras.append(_p("table", "", fact_class=FactClass.FACT,
                    table=rows or [["序号", "资质证书", "状态"], ["1", "", ""]]))
    paras.append(_p("paragraph", "以上资质以企业资料《公司资质》为准。", FactClass.INFERENCE))
    return paras


def _case_table(ctx: GenerationContext) -> list[Paragraph]:
    attrs = _merge_card_attrs(ctx.capability_cards)
    rows = [["序号", "项目类型", "数量", "规模"]]
    rows.append(["1", "智慧园区类项目",
                 str(attrs.get("project_count", "待确认")),
                 str(attrs.get("scale", "待确认"))])
    paras = [_p("heading", "类似项目业绩", level=2)]
    paras.append(_p("table", "", fact_class=FactClass.FACT, table=rows))
    paras.append(_p("paragraph", "以上业绩以企业资料《项目案例》为准，具体合同金额见业绩证明材料。",
                    FactClass.INFERENCE))
    return paras


def _metric_response_table(ctx: GenerationContext) -> list[Paragraph]:
    statuses = _req_status(ctx)
    rows = [["指标", "招标要求", "企业响应", "状态"]]
    for r in ctx.requirements:
        st = statuses.get(r.id, "UNKNOWN")
        ev = next((e.content for e in ctx.evidences
                   if e.requirement_id == r.id), "")
        ev = ev[:50] if ev else ""
        if st == "FULL":
            resp = f"满足（{ev}）" if ev else "满足"
        elif st == "PARTIAL":
            resp = f"部分满足（{ev}）" if ev else "部分满足"
        elif st == "MISSING":
            resp = f"不满足（当前{ev}）" if ev else "不满足"
        else:
            resp = "待确认【待确认】"
        rows.append([r.title, r.text[:60], resp, st])
    paras = [_p("heading", "技术指标响应表", level=2)]
    paras.append(_p("table", "", fact_class=FactClass.FACT, table=rows))
    paras.append(_p("paragraph",
                    "本表响应口径与《需求响应表》一致：FULL/PARTIAL 引企业资料，"
                    "MISSING 如实说明，UNKNOWN 待确认不编造。",
                    FactClass.INFERENCE))
    return paras


def _schedule_table(ctx: GenerationContext) -> list[Paragraph]:
    rows = [["阶段", "主要工作", "时间安排", "交付物"],
            ["1", "需求调研与方案设计", "按合同工期约定【待确认】", "需求规格说明书"],
            ["2", "系统开发与集成", "按合同工期约定【待确认】", "测试版本"],
            ["3", "测试与试运行", "按合同工期约定【待确认】", "试运行报告"],
            ["4", "验收与上线", "按合同工期约定【待确认】", "验收报告"]]
    paras = [_p("heading", "项目进度计划", level=2)]
    paras.append(_p("table", "", fact_class=FactClass.INFERENCE, table=rows))
    paras.append(_p("paragraph",
                    "具体里程碑时间以中标后合同约定为准。",
                    FactClass.INFERENCE))
    return paras


class TableTemplateStrategy:
    """表格型章节：能力卡/需求 → 表格行。"""

    _TEMPLATES = {
        "CH-04-2": _cert_table,
        "CH-04-3": _case_table,
        "CH-05-4": _metric_response_table,
        "CH-06-3": _schedule_table,
    }

    def generate(self, ctx: GenerationContext) -> list[Paragraph]:
        fn = self._TEMPLATES.get(ctx.section.id)
        if fn:
            return fn(ctx)
        return [_p("heading", ctx.section.title, level=2),
                _p("paragraph", "（本章节以表格形式展示，数据待补充）", FactClass.INFERENCE)]


# ---------------------------------------------------------------------------
# 方案型（M4-08：LLM + 证据；失败回退 FactTemplate）
# ---------------------------------------------------------------------------
class SolutionLLMStrategy:
    def __init__(self, llm=None):
        self.llm = llm

    def generate(self, ctx: GenerationContext) -> list[Paragraph]:
        client = self.llm or create_llm_client()
        try:
            with llm_call_context("generate_section"):
                resp = client.chat_json(SOLUTION_SYSTEM, build_solution_user_prompt(ctx))
        except Exception as e:                    # noqa: BLE001
            logger.warning("方案型 LLM 调用异常，回退事实模板: %s", str(e)[:120])
            return FactTemplateStrategy().generate(ctx)
        paras = _parse_paragraphs(resp)
        if paras:
            return paras
        logger.warning("方案型 LLM 无有效段落（%s），回退事实模板", ctx.section.id)
        return FactTemplateStrategy().generate(ctx)


def _parse_paragraphs(resp: Optional[dict]) -> list[Paragraph]:
    """chat_json 响应 → Paragraph[]（非法项跳过，不阻断）。"""
    if not resp or not isinstance(resp.get("data"), dict):
        return []
    raw = resp["data"].get("paragraphs")
    if not isinstance(raw, list):
        return []
    out: list[Paragraph] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            out.append(Paragraph(**item))
        except Exception as e:                    # noqa: BLE001
            logger.debug("段落解析失败: %s", str(e)[:100])
    return out


# ---------------------------------------------------------------------------
# 分派
# ---------------------------------------------------------------------------
def strategy_for(section, llm=None):
    """按 section_type 分派策略（方案型注入 llm 客户端）。"""
    if section.section_type == SectionType.SOLUTION:
        return SolutionLLMStrategy(llm=llm)
    if section.section_type == SectionType.FACT:
        return FactTemplateStrategy()
    if section.section_type == SectionType.TABLE:
        return TableTemplateStrategy()
    return FixedFormatStrategy()


__all__ = ["strategy_for", "FixedFormatStrategy", "FactTemplateStrategy",
           "TableTemplateStrategy", "SolutionLLMStrategy", "_parse_paragraphs"]
