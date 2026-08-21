# -*- coding: utf-8 -*-
"""
scripts/verify_m5_quality.py —— M5 标书一致性与质量检查引擎验收核查（HTTP 端到端）

流程：
  1. 种子（复用 verify_m4）：T-M3 基线（36 条原始需求 + 8 份企业资料）
     → HTTP 匹配 → 章节规划 → 后台生成（轮询至 26 章节全部完成）
  2. 基线检查：POST /api/quality/tenders/T-M3/check → 无 CRITICAL/ERROR、
     9 条待确认、score=99.1（内部质量指标，非"准确率"）
  3. 9 组变异逐组（每组应用→检查→还原，互不污染）：
      1 设备接入 2000→5000       → NUMBER_MISMATCH
      2 张伟 6年→3年             → PERSON_MISMATCH
      3 ISO9001→9002             → CERTIFICATE_MISMATCH
      4 合同额 500→800           → PROJECT_MISMATCH
      5 删 canonical（UNKNOWN 需求）→ REQUIREMENT_MISSING
      6 章节清空（CH-06-1）       → SECTION_MISSING
      7 封面项目名替换           → PROJECT_MISMATCH
      8 跨章节冲突（5000 vs 2000）→ CONFLICT
      9 注入 EVD-9999            → INVALID_REFERENCE
  4. 终版闭环：注入 CRITICAL → finalize 409 → PATCH 确认 CRITICAL/ERROR →
     finalize 200 + 三格式产物 + review_records 批准审计
  5. 报告写 scripts/_m5_verify_report.txt（UTF-8；控制台 GBK 安全打印）

口径声明：score 为 BidForge 内部质量指标（按问题严重度扣分的 5 维公式），
**不是**识别/匹配准确率。变异在确定性生成基线（无 Key 时 MockLLM 回退
事实模板）上执行，服务端无 LLM_API_KEY 亦可全绿。

用法:
    python scripts/verify_m5_quality.py [--host http://127.0.0.1:8001]

前置:
    - 服务已启动（uvicorn app.api.main:app --port 8001）
    - Milvus 停止或服务以 MILVUS_ENABLED=false 启动（同 verify_m3/m4 口径）

退出码恒 0（[OK]/[MISS]/[WRONG] 计入报告），便于 CI 收集证据不阻断。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))   # 复用 verify_m4 的种子与 HTTP 工具

from app import config  # noqa: E402  (加载 .env + 控制台 UTF-8 兜底)
from app.db import Database  # noqa: E402

from verify_m4_generation import (  # noqa: E402
    DEFAULT_HOST, TENDER_ID, _get_raw, _m_get, _m_post,
    _request, _verdict, check_outline_and_job, seed as seed_baseline)

TENDER_NAME = "智慧园区平台建设项目（M3/M4 验收基线）"  # 与 verify_m4 seed 同名
POLL_TIMEOUT = 1200
POLL_INTERVAL = 3


# ═══════════════════════════════════════════════════════════════════════
# HTTP 工具（/api/quality）
# ═══════════════════════════════════════════════════════════════════════
def _q_get(base: str, path: str) -> dict:
    return _request("GET", f"{base}/api/quality{path}")


def _q_post(base: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body or {}).encode("utf-8")
    return _request("POST", f"{base}/api/quality{path}", data=data,
                    headers={"Content-Type": "application/json"})


def _q_patch(base: str, path: str, body: dict) -> dict:
    return _request("PATCH", f"{base}/api/quality{path}",
                    data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json"})


# ═══════════════════════════════════════════════════════════════════════
# 变异构造（应用 → 检查 → 还原，全部基于基线内容做定向改写）
# ═══════════════════════════════════════════════════════════════════════
def _replace(db: Database, sid: str, old: str, new: str) -> None:
    db.execute(
        "UPDATE generation_sections SET content_md = REPLACE(content_md, ?, ?) "
        "WHERE section_id = ? AND tender_id = ?", (old, new, sid, TENDER_ID))


_clear_state: dict = {}
_del_state: dict = {}


def _apply_clear(db: Database, sid: str) -> None:
    row = db.query_one("SELECT content_md FROM generation_sections "
                       "WHERE section_id = ? AND tender_id = ?", (sid, TENDER_ID))
    _clear_state["content"] = row["content_md"] if row else ""
    db.execute("UPDATE generation_sections SET content_md = '' "
               "WHERE section_id = ? AND tender_id = ?", (sid, TENDER_ID))


def _revert_clear(db: Database, sid: str) -> None:
    db.execute("UPDATE generation_sections SET content_md = ? "
               "WHERE section_id = ? AND tender_id = ?",
               (_clear_state.get("content", ""), sid, TENDER_ID))


_DEL_TABLES = (("canonical_requirements", "id"),
               ("requirement_matches", "requirement_id"),
               ("requirement_section_maps", "requirement_id"))


def _apply_delete_canonical(db: Database, rid: str) -> None:
    _del_state.clear()
    for table, col in _DEL_TABLES:
        _del_state[table] = db.query(
            f"SELECT * FROM {table} WHERE {col} = ? AND tender_id = ?",
            (rid, TENDER_ID))
        db.execute(f"DELETE FROM {table} WHERE {col} = ? AND tender_id = ?",
                   (rid, TENDER_ID))


def _revert_delete_canonical(db: Database) -> None:
    for table, rows in _del_state.items():
        for r in rows:
            db.insert(table, dict(r))


def _hit(hits: list[dict], section: str = "", requirement: str = "",
         needle: str = "") -> bool:
    for i in hits:
        if section and i.get("section_id") != section:
            continue
        if requirement and i.get("requirement_id") != requirement:
            continue
        if needle and needle not in i.get("message", ""):
            continue
        return True
    return False


_MUTATIONS = [
    {"name": "设备接入 2000→5000", "type": "NUMBER_MISMATCH",
     "apply": lambda db: _replace(db, "CH-05-2", "max_devices=2000",
                                  "max_devices=5000"),
     "revert": lambda db: _replace(db, "CH-05-2", "max_devices=5000",
                                   "max_devices=2000"),
     "expect": lambda h: _hit(h, section="CH-05-2")},
    {"name": "张伟 6年→3年", "type": "PERSON_MISMATCH",
     "apply": lambda db: _replace(db, "CH-06-2", "张伟具有6年", "张伟具有3年"),
     "revert": lambda db: _replace(db, "CH-06-2", "张伟具有3年", "张伟具有6年"),
     "expect": lambda h: _hit(h, section="CH-06-2")},
    {"name": "ISO9001→9002", "type": "CERTIFICATE_MISMATCH",
     "apply": lambda db: _replace(db, "CH-04-2", "ISO9001", "ISO9002"),
     "revert": lambda db: _replace(db, "CH-04-2", "ISO9002", "ISO9001"),
     "expect": lambda h: _hit(h, section="CH-04-2", needle="9002")},
    {"name": "合同额 500→800", "type": "PROJECT_MISMATCH",
     "apply": lambda db: _replace(db, "CH-04-3", "单个合同额500万元",
                                  "单个合同额800万元"),
     "revert": lambda db: _replace(db, "CH-04-3", "单个合同额800万元",
                                   "单个合同额500万元"),
     "expect": lambda h: _hit(h, section="CH-04-3")},
    {"name": "删 canonical（UNKNOWN 需求）", "type": "REQUIREMENT_MISSING",
     "apply": lambda db: _apply_delete_canonical(db, "REQ-C-0020"),
     "revert": lambda db: _revert_delete_canonical(db),
     "expect": lambda h: _hit(h, requirement="REQ-0022")},
    {"name": "章节清空（CH-06-1）", "type": "SECTION_MISSING",
     "apply": lambda db: _apply_clear(db, "CH-06-1"),
     "revert": lambda db: _revert_clear(db, "CH-06-1"),
     "expect": lambda h: _hit(h, section="CH-06-1")},
    {"name": "封面项目名替换", "type": "PROJECT_MISMATCH",
     "apply": lambda db: _replace(db, "CH-01", TENDER_NAME, "虚假项目名称"),
     "revert": lambda db: _replace(db, "CH-01", "虚假项目名称", TENDER_NAME),
     "expect": lambda h: _hit(h, section="CH-01")},
    {"name": "跨章节冲突（5000 vs 2000）", "type": "CONFLICT",
     "apply": lambda db: (_replace(db, "CH-05-2", "max_devices=2000",
                                   "max_devices=5000"),
                          _replace(db, "CH-06-1", "scale=单个合同额500万元。",
                                   "scale=单个合同额500万元。\n设备接入能力为2000台。")),
     "revert": lambda db: (_replace(db, "CH-05-2", "max_devices=5000",
                                    "max_devices=2000"),
                           _replace(db, "CH-06-1",
                                    "scale=单个合同额500万元。\n设备接入能力为2000台。",
                                    "scale=单个合同额500万元。")),
     "expect": lambda h: _hit(h, section="CH-05-2")},
    {"name": "注入 EVD-9999", "type": "INVALID_REFERENCE",
     "apply": lambda db: _replace(db, "CH-04-1", "**本章证据依据：**",
                                  "另参考证据EVD-9999。\n**本章证据依据：**"),
     "revert": lambda db: _replace(db, "CH-04-1",
                                   "另参考证据EVD-9999。\n**本章证据依据：**",
                                   "**本章证据依据：**"),
     "expect": lambda h: _hit(h, section="CH-04-1", needle="EVD-9999")},
]


# ═══════════════════════════════════════════════════════════════════════
# 核查
# ═══════════════════════════════════════════════════════════════════════
def check_baseline(base: str, lines: list[str], counters: dict) -> None:
    """② 基线：无 CRITICAL/ERROR，9 条待确认，score≈99.1。"""
    lines.append("")
    lines.append("══ ② 基线质量检查（无 CRITICAL/ERROR + 9 待确认 + score）══")
    r = _q_post(base, f"/tenders/{TENDER_ID}/check")
    if r.get("_http_error"):
        lines.append(f"[WRONG] POST /check 失败: {r.get('detail', r)[:160]}")
        counters["wrong"] += 1
        return
    report, issues = r.get("report") or {}, r.get("issues") or []
    crit = [i for i in issues if i["severity"] in ("CRITICAL", "ERROR")]
    pending = (report.get("counts") or {}).get("pending")
    ok = (pending == 9 and not crit
          and abs(float(report.get("score", 0)) - 99.1) < 0.05)
    lines.append(f"[{_verdict(ok)}] 基线: score={report.get('score')} "
                 f"pending={pending} critical/error={len(crit)}"
                 + ("" if ok else f"，实际 issue 类型 "
                    f"{sorted({i['issue_type'] for i in issues})[:8]}"))
    counters["ok" if ok else "wrong"] += 1


def run_mutation(base: str, db: Database, lines: list[str], counters: dict,
                 m: dict) -> None:
    """③ 变异：应用 → POST /check → 断言 → 还原。"""
    lines.append("")
    lines.append(f"── 变异 {m['name']}（期望 {m['type']}）──")
    m["apply"](db)
    try:
        r = _q_post(base, f"/tenders/{TENDER_ID}/check")
        if r.get("_http_error"):
            lines.append(f"[WRONG] POST /check 失败: {r.get('detail', r)[:120]}")
            counters["wrong"] += 1
            return
        issues = r.get("issues") or []
        hits = [i for i in issues if i["issue_type"] == m["type"]]
        ok = bool(hits) and m["expect"](hits)
        detail = ""
        if hits:
            detail = "，section=" + ",".join(sorted({i.get("section_id") or "—"
                                                    for i in hits}))
        lines.append(f"[{_verdict(ok)}] {m['name']}: {m['type']} {len(hits)} 条"
                     f"{detail}")
        counters["ok" if ok else "wrong"] += 1
        if not ok:
            lines.append(f"[MISS] 实际 issue 类型: "
                         f"{sorted({i['issue_type'] for i in issues})[:8]}")
            counters["miss"] += 1
    finally:
        m["revert"](db)


def check_finalize_loop(base: str, db: Database, lines: list[str],
                        counters: dict) -> None:
    """④ 终版闭环：409 → 人工确认 → 200 + 产物 + 批准审计。"""
    lines.append("")
    lines.append("══ ④ 终版闭环（finalize 409 → 确认 → 200 + 产物 + 审计）══")
    _apply_clear(db, "CH-06-1")                       # 注入 CRITICAL
    try:
        r = _q_post(base, f"/tenders/{TENDER_ID}/check")
        report_id = (r.get("report") or {}).get("id", "")
        bad = [i for i in (r.get("issues") or [])
               if i["severity"] in ("CRITICAL", "ERROR")]
        ok = bool(bad) and bool(report_id)
        lines.append(f"[{_verdict(ok)}] 注入章节缺失: CRITICAL/ERROR={len(bad)} "
                     f"report={report_id}")
        counters["ok" if ok else "wrong"] += 1

        r2 = _q_post(base, f"/tenders/{TENDER_ID}/finalize",
                     {"reviewer": "验收员"})
        ok = r2.get("_http_error") == 409
        lines.append(f"[{_verdict(ok)}] 未清 CRITICAL → finalize 409"
                     + (f"（{str(r2.get('detail', ''))[:44]}）" if ok
                        else f"，实际 {r2}"))
        counters["ok" if ok else "wrong"] += 1

        patch_fail = 0
        for i in bad:
            p = _q_patch(base, f"/issues/{i['id']}",
                         {"status": "已确认", "reviewer": "验收员"})
            patch_fail += 1 if p.get("_http_error") else 0
        ok = patch_fail == 0
        lines.append(f"[{_verdict(ok)}] PATCH 确认 CRITICAL/ERROR {len(bad)} 条"
                     f"（失败 {patch_fail}）")
        counters["ok" if ok else "wrong"] += 1

        r3 = _q_post(base, f"/tenders/{TENDER_ID}/finalize",
                     {"reviewer": "验收员"})
        ok = not r3.get("_http_error") and r3.get("status") == "已批准"
        lines.append(f"[{_verdict(ok)}] 清状态后 finalize: status="
                     f"{r3.get('status')} score={r3.get('score')}")
        counters["ok" if ok else "wrong"] += 1

        fj = _q_get(base, f"/tenders/{TENDER_ID}/final?format=json")
        ok = not fj.get("_http_error") and fj.get("score") is not None
        lines.append(f"[{_verdict(ok)}] final json 可读（score={fj.get('score')}）")
        counters["ok" if ok else "wrong"] += 1

        raw, ctype = _get_raw(base,
                              f"/api/quality/tenders/{TENDER_ID}/final"
                              f"?format=docx")
        ok = raw.startswith(b"PK") and len(raw) > 5000
        lines.append(f"[{_verdict(ok)}] final docx {len(raw)} 字节 zip 魔数 PK"
                     f"（content-type: {ctype[:36]}）")
        counters["ok" if ok else "wrong"] += 1

        audit = db.query("SELECT * FROM review_records WHERE action = '批准'")
        ok = bool(audit) and audit[0]["reviewer"] == "验收员"
        lines.append(f"[{_verdict(ok)}] review_records 批准审计 {len(audit)} 条"
                     f"（reviewer={audit[0]['reviewer'] if audit else '—'}）")
        counters["ok" if ok else "wrong"] += 1
    finally:
        _revert_clear(db, "CH-06-1")


# ═══════════════════════════════════════════════════════════════════════
def main() -> None:
    ap = argparse.ArgumentParser(description="M5 标书质量检查引擎验收核查")
    ap.add_argument("--host", default=DEFAULT_HOST, help="API 地址")
    args = ap.parse_args()
    base = args.host.rstrip("/")

    lines = [f"M5 标书一致性与质量检查引擎验收 | API {base} "
             f"| 时间 {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    counters = {"ok": 0, "miss": 0, "wrong": 0}

    db = Database(config.DB_PATH)
    emb_backend = seed_baseline(db, lines)          # 清 T-M3 M3/M4 数据 + 重插
    # 清 T-M3 的 M5 检查数据（重跑不累积报告）
    db.execute("DELETE FROM quality_issues WHERE tender_id = ?", (TENDER_ID,))
    db.execute("DELETE FROM quality_reports WHERE tender_id = ?", (TENDER_ID,))
    db.execute("DELETE FROM review_records WHERE issue_id LIKE 'QR-%' "
               "OR issue_id LIKE 'FINALIZE:%'")
    lines.append(f"[SEED] T-M3 的 M5 检查数据已清理（报告/问题/关联审计）")

    # ── HTTP：匹配 → 规划 → 生成 ──
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
                check_baseline(base, lines, counters)
                for m in _MUTATIONS:
                    run_mutation(base, db, lines, counters, m)
                check_finalize_loop(base, db, lines, counters)
            else:
                lines.append("[MISS] 章节规划/生成失败，跳过 M5 核查")
                counters["miss"] += 1

    # ── 汇总 ──
    lines.append("")
    lines.append(f"══ 汇总: OK {counters['ok']} / MISS {counters['miss']} / "
                 f"WRONG {counters['wrong']} ══")
    lines.append("口径声明：score 为 BidForge 内部质量指标（按问题严重度扣分的 "
                 "5 维公式），不是识别/匹配准确率。变异在确定性生成基线"
                 f"（种子嵌入后端 {emb_backend}）上执行；真实样例端到端验收需 "
                 "配置 .env 中 LLM_API_KEY 后先跑 M1/M3/M4。")

    report = "\n".join(lines)
    out = Path(__file__).resolve().parent / "_m5_verify_report.txt"
    out.write_text(report, encoding="utf-8")
    print(f"报告已写入: {out}")
    try:
        print(report)
    except UnicodeEncodeError:
        pass  # GBK 控制台打印失败以报告文件为准


if __name__ == "__main__":
    main()
