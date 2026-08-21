# -*- coding: utf-8 -*-
"""
scripts/verify_m6_workbench.py —— M6 标书工作台验收核查（HTTP 端到端）

流程：
  1. 种子（复用 verify_m4）：T-M3 基线（36 条原始需求 + 企业资料/能力卡/知识块）
     → HTTP 匹配 → 章节规划 → 后台生成（轮询至 26 章节全部完成）
  2. 核查（M6-01 工作台聚合 + M6-05 生成 SSE）：
     ① 项目列表 GET /api/workbench/projects：列表含 T-M3 + 聚合字段正确
        （文档统计 / 匹配分布 / 章节进度 / 质量快照 / 交付标记）
     ② 六阶段状态派生（docs/extract/kb/match/generate/quality 逐阶段核验）
     ③ KB 全局统计（资料数 / 能力卡数 / 已处理数，随列表响应带出）
     ④ 单项目概览 GET /api/workbench/projects/T-M3：文档明细 + 待处理问题
        前 N 条（按严重度排序）+ 未知项目 404
     ⑤ 生成 SSE GET /api/generation/tenders/T-M3/jobs/{id}/events：
        已完成 job 推历史日志 + event: done 关闭流 + 未知 job/tender 404
     ⑥ 前端工作台文件核查（7 页面 + 5 组件 + 路由注册 + store，静态存在性；
        前端 build 验证由 M6 里程碑前端验收单独执行）
  3. 报告写 scripts/_m6_verify_report.txt（UTF-8；控制台 GBK 安全打印）

口径声明：工作台聚合为只读派生（SQL 聚合 + 六阶段状态派生），验收基于
项目内置 T-M3 基线数据；前端页面核查为文件级存在性检查，不代表交互验收。

用法:
    python scripts/verify_m6_workbench.py [--host http://127.0.0.1:8001]

前置: 同 verify_m4（服务已启动、Milvus 停或服务以 MILVUS_ENABLED=false 启动）

退出码恒 0（[OK]/[MISS]/[WRONG] 计入报告），便于 CI 收集证据不阻断。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))   # 复用 verify_m4 的种子与 HTTP 工具

from app import config  # noqa: E402  (加载 .env + 控制台 UTF-8 兜底)
from app.db import Database  # noqa: E402

from verify_m4_generation import (  # noqa: E402
    DEFAULT_HOST, TENDER_ID, _get_raw, _request, _verdict,
    check_outline_and_job, seed as seed_baseline)
from verify_m5_quality import _m_get, _m_post  # noqa: E402

POLL_TIMEOUT = 1200
POLL_INTERVAL = 3

# 前端工作台页面/组件/路由清单（M6-01~07 + 支撑组件）
_FE_PAGES = ("views/projects/ProjectList.vue",          # M6-01 项目列表
             "views/projects/ProjectOverview.vue",      # M6-01 单项目概览
             "views/projects/TenderDocs.vue",           # M6-02 招标文件
             "views/projects/RequirementWorkbench.vue",  # M6-03 需求分析
             "views/knowledge/KnowledgeWorkbench.vue",  # M6-04 知识库
             "views/projects/GenerationWorkbench.vue",  # M6-05 标书生成
             "views/projects/QualityWorkbench.vue",     # M6-06 质量检查
             "views/projects/DeliveryPage.vue")         # M6-07 最终交付
_FE_COMPONENTS = ("EvidenceChain.vue", "MarkdownView.vue", "ProjectNav.vue",
                  "SectionTree.vue", "StageSteps.vue")
_FE_ROUTES = ("projects", "project-overview", "tender-docs",
              "requirement-workbench", "knowledge", "generation-workbench",
              "quality-workbench", "delivery")


def _w_get(base: str, path: str) -> dict:
    return _request("GET", f"{base}/api/workbench{path}")


def _sse_get(base: str, path: str, timeout: int = 90) -> tuple[int, str, str]:
    """读 SSE 流至关闭：返回 (http_code, content_type, body 前若干字)。"""
    try:
        req = urllib.request.Request(f"{base}{path}")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = r.headers.get("Content-Type", "")
            body = r.read(64 * 1024).decode("utf-8", "replace")
            return r.status, ctype, body
    except urllib.error.HTTPError as e:
        return e.code, "", e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        return -1, "", str(e.reason)


# ═══════════════════════════════════════════════════════════════════════
# 核查
# ═══════════════════════════════════════════════════════════════════════
def check_project_list(base: str, db: Database, lines: list[str],
                       counters: dict) -> None:
    """① 项目列表 + 聚合字段；② 六阶段派生；③ KB 全局统计。"""
    lines.append("")
    lines.append("══ ① 项目列表聚合（含 T-M3 + 全流程字段）══")
    r = _w_get(base, "/projects")
    if r.get("_http_error"):
        lines.append(f"[WRONG] GET /api/workbench/projects 失败: "
                     f"{r.get('detail', r)[:160]}")
        counters["wrong"] += 1
        return
    projs = r.get("projects") or []
    proj = next((p for p in projs if p["id"] == TENDER_ID), None)
    ok = bool(proj)
    lines.append(f"[{_verdict(ok)}] 项目列表 {len(projs)} 条，含 {TENDER_ID}")
    counters["ok" if ok else "wrong"] += 1
    if not proj:
        lines.append("[MISS] 未找到 T-M3，跳过聚合核查")
        counters["miss"] += 1
        return

    # 基础 + 文档统计（2 成功含 1 OCR + 1 失败 → ok=2/total=3/ocr=1）
    ok = proj["name"] == "智慧园区平台建设项目（M3/M4 验收基线）"
    lines.append(f"[{_verdict(ok)}] 项目名「{proj.get('name')}」")
    counters["ok" if ok else "wrong"] += 1
    d = proj["documents"]
    ok = d == {"total": 3, "ok": 2, "ocr": 1}
    lines.append(f"[{_verdict(ok)}] 文档聚合 total={d.get('total')} "
                 f"ok={d.get('ok')} ocr={d.get('ocr')}（期望 3/2/1）")
    counters["ok" if ok else "wrong"] += 1

    # 匹配分布（基线 FULL17/PARTIAL6/MISSING5/UNKNOWN5）
    dist = proj["matching"]["distribution"]
    ok = (dist == {"FULL": 17, "PARTIAL": 6, "MISSING": 5, "UNKNOWN": 5}
          and proj["matching"]["status"] == "已完成")
    lines.append(f"[{_verdict(ok)}] 匹配 {proj['matching'].get('status')} "
                 f"分布 FULL {dist.get('FULL')}/PARTIAL {dist.get('PARTIAL')}/"
                 f"MISSING {dist.get('MISSING')}/UNKNOWN {dist.get('UNKNOWN')}"
                 f"（基线 17/6/5/5）")
    counters["ok" if ok else "wrong"] += 1

    # 生成进度（26/26 已完成）
    g = proj["generation"]
    ok = g["status"] == "已完成" and g["done_sections"] == 26 \
        and g["total_sections"] == 26
    lines.append(f"[{_verdict(ok)}] 生成 {g.get('status')} "
                 f"{g.get('done_sections')}/{g.get('total_sections')} 章节")
    counters["ok" if ok else "wrong"] += 1

    # 质量快照（注入报告 88.5 分 + 1 待处理 CRITICAL）
    q = proj["quality"]
    ok = q["report_id"] == "QR-M6" and q["score"] == 88.5 \
        and q["pending_issues"] == 1
    lines.append(f"[{_verdict(ok)}] 质量快照 report={q.get('report_id')} "
                 f"score={q.get('score')} pending={q.get('pending_issues')}")
    counters["ok" if ok else "wrong"] += 1

    # 交付标记（无 final 产物 → finalized=False）
    ok = proj["delivery"]["finalized"] is False
    lines.append(f"[{_verdict(ok)}] 交付 finalized="
                 f"{proj['delivery'].get('finalized')}（无终版产物）")
    counters["ok" if ok else "wrong"] += 1

    # ② 六阶段派生
    lines.append("")
    lines.append("══ ② 六阶段状态派生 ══")
    stages = {s["key"]: s for s in proj["stages"]}
    ok = len(stages) == 6
    lines.append(f"[{_verdict(ok)}] 阶段数 {len(stages)}（期望 6）")
    counters["ok" if ok else "wrong"] += 1
    expect = {
        "docs": ("warning", "2/3 解析成功"),       # 1 失败 → warning
        "extract": ("done", "36 条需求"),          # 已提取
        "kb": ("done", None),                      # 能力卡 > 0
        "match": ("done", None),                   # 已完成
        "generate": ("done", "26/26 章节"),        # 已完成
        "quality": ("warning", "88.5 分 · 1 待处理"),  # 有报告 + 待处理
    }
    for key, (want, summary) in expect.items():
        s = stages.get(key)
        ok = bool(s) and s["status"] == want \
            and (summary is None or summary in s.get("summary", ""))
        lines.append(f"[{_verdict(ok)}] {key}（{s.get('label', '?') if s else '—'}）"
                     f" status={s.get('status') if s else '—'} "
                     f"summary={s.get('summary', '')[:40] if s else '—'}"
                     + ("" if ok else f"，期望 {want} {summary or ''}"))
        counters["ok" if ok else "wrong"] += 1

    # ③ KB 全局统计
    lines.append("")
    lines.append("══ ③ KB 全局统计 ══")
    kb = r.get("kb") or {}
    ok = (kb.get("materials") == 8 and kb.get("ready_materials") == 8
          and (kb.get("capabilities") or 0) > 0)
    lines.append(f"[{_verdict(ok)}] materials={kb.get('materials')} "
                 f"ready={kb.get('ready_materials')} "
                 f"capabilities={kb.get('capabilities')}"
                 f"（期望 8/8/>0）")
    counters["ok" if ok else "wrong"] += 1


def check_project_overview(base: str, lines: list[str], counters: dict) -> None:
    """④ 单项目概览：文档明细 + 待处理问题前 N 条 + 404。"""
    lines.append("")
    lines.append("══ ④ 单项目概览（文档明细 + 待处理问题）══")
    r = _w_get(base, f"/projects/{TENDER_ID}")
    if r.get("_http_error"):
        lines.append(f"[WRONG] GET /api/workbench/projects/{TENDER_ID} 失败: "
                     f"{r.get('detail', r)[:160]}")
        counters["wrong"] += 1
        return
    docs = r.get("documents_detail") or []
    ok = len(docs) == 3 and docs[0]["file_name"] == "招标文件正文.pdf" \
        and docs[1]["ocr_pages"] == [3, 4] and docs[2]["parse_error"] == "解析失败"
    lines.append(f"[{_verdict(ok)}] 文档明细 {len(docs)} 条"
                 f"（首「{docs[0].get('file_name') if docs else '—'}」，"
                 f" OCR 页 {docs[1].get('ocr_pages') if len(docs) > 1 else '—'}，"
                 f" 失败原因「{docs[2].get('parse_error') if len(docs) > 2 else '—'}」）")
    counters["ok" if ok else "wrong"] += 1

    issues = r.get("pending_issues") or []
    ok = (len(issues) == 1 and issues[0]["severity"] == "CRITICAL"
          and "设备数量不一致" in issues[0]["message"])
    lines.append(f"[{_verdict(ok)}] 待处理问题 {len(issues)} 条："
                 f"{issues[0].get('severity') if issues else '—'} "
                 f"「{issues[0].get('message', '')[:24] if issues else '—'}」")
    counters["ok" if ok else "wrong"] += 1

    ok = _w_get(base, "/projects/T-NOPE").get("_http_error") == 404
    lines.append(f"[{_verdict(ok)}] 未知项目 → 404")
    counters["ok" if ok else "wrong"] += 1


def check_sse(base: str, job_id: str, lines: list[str], counters: dict) -> None:
    """⑤ 生成 SSE：已完成 job 推历史日志 + done 关闭流 + 404。"""
    lines.append("")
    lines.append("══ ⑤ 生成 SSE 事件流（已完成 job → 历史日志 + done）══")
    code, ctype, body = _sse_get(
        base, f"/api/generation/tenders/{TENDER_ID}/jobs/{job_id}/events")
    ok = (code == 200 and ctype.startswith("text/event-stream")
          and "data:" in body and "event: done" in body and "已完成" in body)
    lines.append(f"[{_verdict(ok)}] HTTP {code} content-type={ctype[:32]}，"
                 f"流内容 {len(body)} 字"
                 f"（data 行 {'✓' if 'data:' in body else '✗'} / "
                 f"done 事件 {'✓' if 'event: done' in body else '✗'} / "
                 f"终态 {'✓' if '已完成' in body else '✗'}）")
    counters["ok" if ok else "wrong"] += 1
    if not ok:
        lines.append(f"[MISS] 流前 200 字: {body[:200]}")
        counters["miss"] += 1

    c2, _, _ = _sse_get(
        base, f"/api/generation/tenders/{TENDER_ID}/jobs/NO-JOB/events")
    c3, _, _ = _sse_get(
        base, f"/api/generation/tenders/T-NOPE/jobs/{job_id}/events")
    ok = c2 == 404 and c3 == 404
    lines.append(f"[{_verdict(ok)}] 未知 job/tender → 404/404（实际 {c2}/{c3}）")
    counters["ok" if ok else "wrong"] += 1


def check_frontend(lines: list[str], counters: dict) -> None:
    """⑥ 前端工作台文件核查（静态存在性；build 验证另计）。"""
    lines.append("")
    lines.append("══ ⑥ 前端工作台文件核查（7 页面 + 组件 + 路由 + store）══")
    fe = REPO_ROOT / "frontend" / "src"
    missing = [p for p in _FE_PAGES if not (fe / p).exists()]
    ok = not missing
    lines.append(f"[{_verdict(ok)}] 页面 {len(_FE_PAGES)} 个文件全部存在"
                 + (f"，缺失 {missing}" if missing else "（M6-01~07）"))
    counters["ok" if ok else "wrong"] += 1

    missing = [c for c in _FE_COMPONENTS
               if not (fe / "components" / c).exists()]
    ok = not missing
    lines.append(f"[{_verdict(ok)}] 组件 {len(_FE_COMPONENTS)} 个全部存在"
                 + (f"，缺失 {missing}" if missing else ""))
    counters["ok" if ok else "wrong"] += 1

    router = (fe / "router" / "index.ts").read_text(encoding="utf-8")
    missing = [n for n in _FE_ROUTES if f"name: '{n}'" not in router]
    ok = not missing
    lines.append(f"[{_verdict(ok)}] 路由 {len(_FE_ROUTES)} 条全部注册"
                 + (f"，缺失 {missing}" if missing else "（含旧入口兼容重定向）"))
    counters["ok" if ok else "wrong"] += 1

    store = fe / "stores" / "workbench.ts"
    ok = store.exists() and store.stat().st_size > 0
    lines.append(f"[{_verdict(ok)}] stores/workbench.ts 存在且非空")
    counters["ok" if ok else "wrong"] += 1


# ═══════════════════════════════════════════════════════════════════════
def _seed_workbench_rows(db: Database) -> None:
    """补插 M6 聚合核查所需的 documents / quality 行（验收专用，可重跑）。"""
    db.execute("DELETE FROM documents WHERE tender_id = ?", (TENDER_ID,))
    for i, (name, err, ocr) in enumerate((
            ("招标文件正文.pdf", "", "[]"),
            ("扫描件.pdf", "", "[3, 4]"),
            ("附件清单.xlsx", "解析失败", "[]")), start=1):
        db.insert("documents", {
            "id": f"DOC-M6-{i}", "tender_id": TENDER_ID,
            "file_name": name, "stored_name": f"DOC-M6-{i}.bin",
            "file_type": name.rsplit(".", 1)[-1],
            "total_pages": 10 if not err else 0,
            "char_count": 100 if not err else 0,
            "ocr_pages": ocr, "raw_hash": "", "parser_version": "1.0.0",
            "parse_error": err, "parsed_file": "" if err else f"DOC-M6-{i}.json",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    db.execute("DELETE FROM quality_reports WHERE tender_id = ?", (TENDER_ID,))
    db.execute("DELETE FROM quality_issues WHERE tender_id = ?", (TENDER_ID,))
    db.insert("quality_reports", {
        "id": "QR-M6", "tender_id": TENDER_ID, "document_version": "1",
        "score": 88.5, "dimensions": "[]", "counts": "{}", "issue_counts": "{}",
        "summary": "M6 验收注入", "status": "草稿", "reviewer": "",
        "review_time": "", "created_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    db.insert("quality_issues", {
        "id": "QI-M6-1", "report_id": "QR-M6", "tender_id": TENDER_ID,
        "document_version": "1", "section_id": "CH-05-2",
        "requirement_id": "", "issue_type": "FACT_MISMATCH",
        "severity": "CRITICAL", "status": "待处理", "message": "设备数量不一致",
        "source_refs": "[]", "suggestion": "", "autofixable": 0,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")})


# ═══════════════════════════════════════════════════════════════════════
def main() -> None:
    ap = argparse.ArgumentParser(description="M6 标书工作台验收核查")
    ap.add_argument("--host", default=DEFAULT_HOST, help="API 地址")
    args = ap.parse_args()
    base = args.host.rstrip("/")

    lines = [f"M6 标书工作台验收 | API {base} "
             f"| 时间 {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    counters = {"ok": 0, "miss": 0, "wrong": 0}

    db = Database(config.DB_PATH)
    emb_backend = seed_baseline(db, lines)          # 清 T-M3 M3/M4 数据 + 重插
    db.execute("DELETE FROM quality_issues WHERE tender_id = ?", (TENDER_ID,))
    db.execute("DELETE FROM quality_reports WHERE tender_id = ?", (TENDER_ID,))
    db.execute("DELETE FROM review_records WHERE issue_id LIKE 'QR-%' "
               "OR issue_id LIKE 'FINALIZE:%'")
    _seed_workbench_rows(db)                        # 补插 M6 聚合核查行
    lines.append("[SEED] T-M3 基线 + M6 聚合核查行（3 文档/1 报告/1 问题）")

    # ── HTTP：匹配 → 规划 → 生成 ──
    job_id = ""
    r = _m_post(base, f"/tenders/{TENDER_ID}/match")
    if r.get("_http_error") == -1:
        lines.append(f"[WRONG] 服务未启动（{base}）：请先 "
                     f"`cd backend && python -m uvicorn app.api.main:app --port 8001`")
        counters["wrong"] += 1
    elif r.get("_http_error"):
        lines.append(f"[WRONG] 启动匹配失败: {r}")
        counters["wrong"] += 1
    else:
        lines.append(f"[POST /match] {r.get('status')}（轮询 GET /tenders/{TENDER_ID}）")
        st = {"status": "匹配中"}
        t0 = time.time()
        while st.get("status") not in ("已完成", "失败"):
            if time.time() - t0 > POLL_TIMEOUT:
                lines.append(f"[WRONG] 匹配轮询超时（>{POLL_TIMEOUT}s）")
                counters["wrong"] += 1
                break
            time.sleep(POLL_INTERVAL)
            st = _m_get(base, f"/tenders/{TENDER_ID}")
        lines.append(f"[{'OK' if st.get('status') == '已完成' else 'WRONG'}] "
                     f"匹配完成: {st.get('status')} | 耗时 {time.time() - t0:.0f}s")
        counters["ok" if st.get("status") == "已完成" else "wrong"] += 1

        if st.get("status") == "已完成":
            flat = check_outline_and_job(base, lines, counters)  # 规划 + 生成 + 轮询
            if flat:
                jb = db.query_one(
                    "SELECT * FROM generation_jobs WHERE tender_id = ? "
                    "ORDER BY id DESC LIMIT 1", (TENDER_ID,))
                job_id = (jb or {}).get("id") or ""
                check_project_list(base, db, lines, counters)
                check_project_overview(base, lines, counters)
                if job_id:
                    check_sse(base, job_id, lines, counters)
                else:
                    lines.append("[MISS] 无 job_id，跳过 SSE 核查")
                    counters["miss"] += 1
                check_frontend(lines, counters)
            else:
                lines.append("[MISS] 章节规划/生成失败，跳过 M6 核查")
                counters["miss"] += 1

    # ── 汇总 ──
    lines.append("")
    lines.append(f"══ 汇总: OK {counters['ok']} / MISS {counters['miss']} / "
                 f"WRONG {counters['wrong']} ══")
    lines.append("口径声明：工作台聚合为只读派生（SQL 聚合 + 六阶段状态派生），"
                 f"验收基于项目内置 T-M3 基线（种子嵌入后端 {emb_backend}）；"
                 "前端页面核查为文件级存在性检查，build 与交互验收由前端里程碑"
                 "验收单独执行。")

    report = "\n".join(lines)
    out = Path(__file__).resolve().parent / "_m6_verify_report.txt"
    out.write_text(report, encoding="utf-8")
    print(f"报告已写入: {out}")
    try:
        print(report)
    except UnicodeEncodeError:
        pass  # GBK 控制台打印失败以报告文件为准


if __name__ == "__main__":
    main()
