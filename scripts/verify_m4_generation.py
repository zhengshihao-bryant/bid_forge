# -*- coding: utf-8 -*-
"""
scripts/verify_m4_generation.py —— M4 标书生成引擎验收核查（HTTP 端到端）

流程：
  1. 种子数据（确定性、可重跑）：与 verify_m3 同一份基线（T-M3：36 条原始需求
     + 企业资料/能力卡/知识块）直接写 SQLite，并清掉本验收的 M4 生成表
  2. HTTP：POST /match 匹配 → POST /outline 章节规划 → POST /jobs 后台生成
     → 轮询 job 至终态（已完成/部分失败/失败）
  3. 核查（M4-12 四项）：
     ① 完整章节：26 章节 / 四大块（商务/技术/实施/售后）/ 前序顺序 / 全部 content 非空
     ② 需求覆盖：coverage 33 条全映射；响应表 33 行 + 状态口径
        （FULL 满足 / PARTIAL 部分 / MISSING 如实不编造 / UNKNOWN 待确认）
     ③ 可追溯：响应表 REQ→EVD→material→原文 证据链全真实（无编造 EVD）；
        文档所有 EVD- 编号 ∈ evidences 表；能力卡数值原样落章
     ④ 文件生成：Markdown 完整（封面/响应表/生成信息）；DOCX 文件非空（zip 魔数）
  4. 报告写 scripts/_m4_verify_report.txt（UTF-8；控制台 GBK 安全打印）

用法:
    python scripts/verify_m4_generation.py [--host http://127.0.0.1:8001]

前置:
    - 服务已启动（uvicorn app.api.main:app --port 8001）
    - Milvus 停止或服务以 MILVUS_ENABLED=false 启动（同 verify_m3 口径）
    - 无 LLM_API_KEY 时方案型章节走确定性回退（FactTemplate），M4 验收仍全绿

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
BACKEND = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(REPO_ROOT / "tests"))   # 复用测试基线数据源（单一事实来源）

from app import config  # noqa: E402  (加载 .env + 控制台 UTF-8 兜底)
from app.db import Database  # noqa: E402

# 基线数据源：与 tests/test_m3_matcher.py 完全一致（导入而非复制）
import test_m3_matcher as tm  # noqa: E402
from conftest import seed_m3_kb  # noqa: E402

TENDER_ID = tm.TENDER_ID
DEFAULT_HOST = "http://127.0.0.1:8001"
POLL_TIMEOUT = 1200        # 生成轮询超时（秒）——含 26 章节方案型 LLM（启用时）
POLL_INTERVAL = 3
EVD_RE = re.compile(r"EVD-\d{4}")


# ═══════════════════════════════════════════════════════════════════════
# HTTP 工具（urllib，不加依赖；镜像 verify_m3）
# ═══════════════════════════════════════════════════════════════════════
def _request(method: str, url: str, data: bytes | None = None,
             headers: dict | None = None, timeout: int = 180) -> dict:
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return {"_http_error": e.code, "detail": body}
    except urllib.error.URLError as e:
        return {"_http_error": -1, "detail": str(e.reason)}


def _get_raw(base: str, path: str) -> tuple[bytes, str]:
    """原始字节（DOCX FileResponse 用）：返回 (bytes, content-type)。"""
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=180) as r:
            return r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return b"", f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return b"", str(e.reason)


def _m_get(base: str, path: str) -> dict:
    return _request("GET", f"{base}/api/matching{path}")


def _m_post(base: str, path: str) -> dict:
    return _request("POST", f"{base}/api/matching{path}", data=b"",
                    headers={"Content-Type": "application/json"})


def _g_get(base: str, path: str) -> dict:
    return _request("GET", f"{base}/api/generation{path}")


def _g_post(base: str, path: str) -> dict:
    return _request("POST", f"{base}/api/generation{path}", data=b"",
                    headers={"Content-Type": "application/json"})


# ═══════════════════════════════════════════════════════════════════════
# 种子数据（直接写 SQLite；服务端只读，通过 HTTP 跑匹配/生成）
# ═══════════════════════════════════════════════════════════════════════
def seed(db: Database, lines: list[str]) -> str:
    """重建 T-M3 验收数据（含 M4 生成表清理）。返回嵌入后端标识（bge/fake）。"""
    db.init_schema()
    # 1. 清 M4 生成数据（本验收固定 tender，绝不碰用户数据）
    db.execute("DELETE FROM generation_logs WHERE generation_id IN "
               "(SELECT id FROM generation_jobs WHERE tender_id = ?)",
               (TENDER_ID,))
    for table in ("generation_jobs", "requirement_section_maps",
                  "generation_sections"):
        db.execute(f"DELETE FROM {table} WHERE tender_id = ?", (TENDER_ID,))
    # 2. 清 M3 匹配数据
    cap_ids = [c["id"] for c in tm._KB_CAPABILITIES]
    mat_ids = [m["id"] for m in tm._KB_MATERIALS]
    chunk_ids = [c["id"] for c in tm._KB_CHUNKS]
    for table, col in (("requirement_matches", "tender_id"),
                       ("evidences", "tender_id"),
                       ("canonical_requirements", "tender_id"),
                       ("matching_runs", "tender_id"),
                       ("requirements", "tender_id")):
        db.execute(f"DELETE FROM {table} WHERE {col} = ?", (TENDER_ID,))
    for table, ids in (("capabilities", cap_ids),
                       ("kb_materials", mat_ids),
                       ("kb_chunks", chunk_ids)):
        marks = ", ".join("?" for _ in ids)
        db.execute(f"DELETE FROM {table} WHERE id IN ({marks})", tuple(ids))
    db.execute("DELETE FROM tenders WHERE id = ?", (TENDER_ID,))

    # 3. 招标项目 + 36 条原始需求
    db.execute("INSERT INTO tenders (id, name, created_at, extraction_status, "
               "requirement_count) VALUES (?, ?, ?, '已提取', ?)",
               (TENDER_ID, "智慧园区平台建设项目（M3/M4 验收基线）",
                time.strftime("%Y-%m-%d %H:%M:%S"), len(tm._tender_reqs())))
    for r in tm._tender_reqs():
        db.insert("requirements", Database.requirement_to_row(r))

    # 4. 企业包：chunk 向量用服务端同一嵌入后端（BGE）计算
    emb_backend = "bge"
    try:
        from app.services.embedding import BgeEmbedding
        emb = BgeEmbedding()
    except Exception as e:  # noqa: BLE001 —— 无 BGE 环境回退伪嵌入并如实报告
        from app.services.embedding import FakeEmbedding
        emb = FakeEmbedding(dimension=config.EMBEDDING_DIM)
        emb_backend = "fake"
        lines.append(f"[NOTE] BGE 不可用（{str(e)[:80]}）→ 伪嵌入种子；"
                     f"服务端须以 EMBEDDING_BACKEND=fake 启动，否则检索口径不一致")
    seed_m3_kb(db, emb, materials=tm._KB_MATERIALS,
               capabilities=tm._KB_CAPABILITIES, chunks=tm._KB_CHUNKS)
    lines.append(f"[SEED] {TENDER_ID}: {len(tm._tender_reqs())} 条原始需求 + "
                 f"{len(tm._KB_MATERIALS)} 份资料 / {len(tm._KB_CAPABILITIES)} 张能力卡 "
                 f"/ {len(tm._KB_CHUNKS)} 块（嵌入后端 {emb_backend}）")
    return emb_backend


# ═══════════════════════════════════════════════════════════════════════
def _verdict(ok: bool) -> str:
    return "OK" if ok else "WRONG"


def _flat(tree: list) -> list[dict]:
    """章节树 → 平铺（前序；model_dump 的 sections）。"""
    out: list[dict] = []
    for node in tree:
        out.append(node)
        out.extend(_flat(node.get("children") or []))
    return out


# ═══════════════════════════════════════════════════════════════════════
# 核查
# ═══════════════════════════════════════════════════════════════════════
def check_outline_and_job(base: str, lines: list[str], counters: dict) -> list[dict]:
    """① 章节规划 + 生成任务：26 章节四大块、前序、job 全部完成。返回平铺章节。"""
    lines.append("")
    lines.append("══ ① 章节完整性（26 章节 / 四大块 / 前序 / 全部生成）══")

    o = _g_post(base, f"/tenders/{TENDER_ID}/outline")
    if o.get("_http_error"):
        lines.append(f"[WRONG] POST /outline 失败: {o.get('detail', o)[:120]}")
        counters["wrong"] += 1
        return []
    total = o.get("total_sections", -1)
    ok = total == 26
    lines.append(f"[{_verdict(ok)}] 章节规划 total_sections={total}（期望 26）")
    counters["ok" if ok else "wrong"] += 1

    tree = _g_get(base, f"/tenders/{TENDER_ID}/outline").get("sections") or []
    flat = _flat(tree)
    titles = [s.get("title", "") for s in flat]
    blocks = ["商务部分", "技术部分", "实施部分", "售后服务"]
    block_ok = all(any(b in t for t in titles) for b in blocks)
    lines.append(f"[{_verdict(block_ok)}] 四大块齐全："
                 + "、".join(("✓" if any(b in t for t in titles) else "✗" + b)
                             for b in blocks))
    counters["ok" if block_ok else "wrong"] += 1

    # 前序顺序：封面在前、需求响应表在后（父子恒序由扁平化顺序保证）
    first = titles[0] if titles else ""
    last = titles[-1] if titles else ""
    order_ok = ("封面" in first and "需求响应表" in last
                and len(flat) == 26)
    lines.append(f"[{_verdict(order_ok)}] 前序组装：首「{first}」 末「{last}」，"
                 f"共 {len(flat)} 章节")
    counters["ok" if order_ok else "wrong"] += 1

    # 启动生成任务 + 轮询
    j = _g_post(base, f"/tenders/{TENDER_ID}/jobs")
    if j.get("_http_error"):
        lines.append(f"[WRONG] POST /jobs 失败: {j.get('detail', j)[:120]}")
        counters["wrong"] += 1
        return flat
    job_id = j.get("job_id", "")
    lines.append(f"[POST /jobs] job_id={job_id}（轮询 GET /jobs/{job_id}）")
    st = {"status": "未生成"}
    t0 = time.time()
    while st.get("status") in ("未生成", "生成中"):
        if time.time() - t0 > POLL_TIMEOUT:
            lines.append(f"[WRONG] 生成轮询超时（>{POLL_TIMEOUT}s）")
            counters["wrong"] += 1
            break
        time.sleep(POLL_INTERVAL)
        st = _g_get(base, f"/tenders/{TENDER_ID}/jobs/{job_id}")
    ok = st.get("status") == "已完成" and st.get("done_sections") == 26
    lines.append(f"[{_verdict(ok)}] 生成终态 {st.get('status')} "
                 f"（done {st.get('done_sections')}/{st.get('total_sections')}，"
                 f"耗时 {time.time() - t0:.0f}s）{st.get('progress') or ''}")
    counters["ok" if ok else "wrong"] += 1

    # 全部章节 content 非空：文档无占位文案即全生成
    md = _g_get(base, f"/tenders/{TENDER_ID}/document?format=markdown") \
        .get("markdown") or ""
    placeholder_ok = "（本章节未生成" not in md
    lines.append(f"[{_verdict(placeholder_ok)}] 文档无「未生成」占位 → "
                 f"26 章节 content 全部非空（markdown {len(md)} 字）")
    counters["ok" if placeholder_ok else "wrong"] += 1
    return flat


def check_coverage(base: str, lines: list[str], counters: dict) -> None:
    """② 需求覆盖：33 条全映射；响应表 33 行 + 状态口径。"""
    lines.append("")
    lines.append("══ ② 需求覆盖（33 条 / 状态口径不编造）══")

    cov = _g_get(base, f"/tenders/{TENDER_ID}/coverage")
    total = cov.get("total", -1)
    mapped = cov.get("mapped", -1)
    unmapped = cov.get("unmapped", -1)
    ok = total == 33 and mapped == 33 and unmapped == 0
    lines.append(f"[{_verdict(ok)}] coverage: total={total} mapped={mapped} "
                 f"unmapped={unmapped}（期望 33/33/0，评分细则天然排除）")
    counters["ok" if ok else "wrong"] += 1
    if not ok:
        lines.append(f"[MISS] unmapped_reqs={cov.get('unmapped_reqs', [])[:5]}")
        counters["miss"] += 1

    rt = _g_get(base, f"/tenders/{TENDER_ID}/response-table?format=json")
    rows = rt.get("rows") or []
    ok = rt.get("total") == 33 and len(rows) == 33
    lines.append(f"[{_verdict(ok)}] 响应表 {rt.get('total')} 行 / {len(rows)} 行（期望 33）")
    counters["ok" if ok else "wrong"] += 1

    counts = rt.get("counts") or {}
    dist_ok = (counts.get("FULL") == 17 and counts.get("PARTIAL") == 6
               and counts.get("MISSING") == 5 and counts.get("UNKNOWN") == 5)
    lines.append(f"[{_verdict(dist_ok)}] 状态分布 FULL {counts.get('FULL')} / "
                 f"PARTIAL {counts.get('PARTIAL')} / MISSING {counts.get('MISSING')} "
                 f"/ UNKNOWN {counts.get('UNKNOWN')}（基线 17/6/5/5）")
    counters["ok" if dist_ok else "wrong"] += 1

    # 状态口径：FULL 满足 / PARTIAL 部分 / MISSING 如实不声称 / UNKNOWN 待确认
    status_policy_ok = True
    for r in rows:
        resp = r.get("response", "")
        st = r.get("status")
        if st == "FULL":
            if "满足" not in resp:
                status_policy_ok = False
        elif st == "PARTIAL":
            if "部分满足" not in resp:
                status_policy_ok = False
        elif st == "MISSING":
            if ("不满足" not in resp or "我司已具备" in resp
                    or "完全满足" in resp or "能够满足" in resp):
                status_policy_ok = False
        elif st == "UNKNOWN":
            if "待确认" not in resp:
                status_policy_ok = False
    bad = [r.get("title", "") for r in rows if not (
        (r["status"] == "FULL" and "满足" in r.get("response", ""))
        or (r["status"] == "PARTIAL" and "部分满足" in r.get("response", ""))
        or (r["status"] == "MISSING" and "不满足" in r.get("response", "")
            and "我司已具备" not in r.get("response", "")
            and "完全满足" not in r.get("response", ""))
        or (r["status"] == "UNKNOWN" and "待确认" in r.get("response", "")))]
    lines.append(f"[{_verdict(status_policy_ok)}] 状态口径逐条核验"
                 + (f"，异常 {bad[:3]}" if bad else "：FULL 满足 / PARTIAL 部分 / "
                    "MISSING 如实不编造 / UNKNOWN 待确认") )
    counters["ok" if status_policy_ok else "wrong"] += 1

    # Markdown 响应表三列
    rm = _g_get(base, f"/tenders/{TENDER_ID}/response-table?format=markdown")
    content = rm.get("content") or ""
    ok = ("| 招标要求 | 企业响应 | 证据 |" in content
          and "MISSING=资料明确显示不满足" in content)
    lines.append(f"[{_verdict(ok)}] 响应表 Markdown 三列 + 口径说明"
                 f"（{len(content)} 字）")
    counters["ok" if ok else "wrong"] += 1


def check_traceability(base: str, lines: list[str], counters: dict,
                       db: Database) -> None:
    """③ 可追溯：REQ→EVD→material→原文 全真实；能力卡数值原样落章。"""
    lines.append("")
    lines.append("══ ③ 可追溯（REQ→EVD→material→原文；无编造 EVD）══")

    # 证据池：evidences 表全部 id（M4 只允许引用真实编号）
    evd_ids = {r["id"] for r in db.query(
        "SELECT id FROM evidences WHERE tender_id = ?", (TENDER_ID,))}
    lines.append(f"[NOTE] 证据池 {len(evd_ids)} 条（evidences 表）")

    # 响应表每条 evidence_ids 均真实存在 + 至少一条证据带资料出处
    rt = _g_get(base, f"/tenders/{TENDER_ID}/response-table?format=json")
    rows = rt.get("rows") or []
    bad_ids = []
    no_prov = 0
    for r in rows:
        for eid in r.get("evidence_ids") or []:
            if eid not in evd_ids:
                bad_ids.append(f"{r.get('title', '')}:{eid}")
        evs = r.get("evidences") or []
        if evs and not any(e.get("document") for e in evs):
            no_prov += 1
    ok = not bad_ids and no_prov == 0
    lines.append(f"[{_verdict(ok)}] 响应表证据编号全真实"
                 + (f"，异常 {bad_ids[:5]}" if bad_ids else "")
                 + (f"，{no_prov} 行证据缺资料出处" if no_prov else ""))
    counters["ok" if ok else "wrong"] += 1

    # 文档所有 EVD- 编号 ∈ 证据池（整本标书无编造引用）
    md = _g_get(base, f"/tenders/{TENDER_ID}/document?format=markdown") \
        .get("markdown") or ""
    scanned = EVD_RE.findall(md)
    fake = [e for e in scanned if e not in evd_ids]
    ok = not fake
    lines.append(f"[{_verdict(ok)}] 整本文档扫到 {len(scanned)} 处 EVD- 引用，"
                 f"全部 ∈ 证据池" + (f"，伪造 {fake[:5]}" if fake else ""))
    counters["ok" if ok else "wrong"] += 1

    # 能力卡数值原样落章（无【待确认】污染事实段）：
    # CH-04-1 公司概况（注册资本/成立年限/员工）
    sec = _g_get(base, f"/tenders/{TENDER_ID}/sections/CH-04-1")
    content = sec.get("content_md") or ""
    ok = ("注册资本5000万元" in content and "成立已16年" in content
          and "员工规模300-600人" in content and "【待确认】" not in content)
    lines.append(f"[{_verdict(ok)}] CH-04-1 公司概况数值原样（注册资本5000万/"
                 f"16年/300-600人），事实段无待确认")
    counters["ok" if ok else "wrong"] += 1

    # CH-04-2 资质表（资质编号与证据一致）
    sec2 = _g_get(base, f"/tenders/{TENDER_ID}/sections/CH-04-2")
    content2 = sec2.get("content_md") or ""
    ok = ("ISO9001" in content2 and "等保三级" in content2)
    lines.append(f"[{_verdict(ok)}] CH-04-2 资质表含 ISO9001 / 等保三级"
                 f"（与 m2_C0001 证据一致）")
    counters["ok" if ok else "wrong"] += 1

    # CH-06-2 人员（张伟绑定 PMP/6年）
    sec3 = _g_get(base, f"/tenders/{TENDER_ID}/sections/CH-06-2")
    content3 = sec3.get("content_md") or ""
    ok = ("张伟" in content3 and "PMP" in content3 and "6年" in content3)
    lines.append(f"[{_verdict(ok)}] CH-06-2 人员：张伟 + PMP + 6年（不串线）")
    counters["ok" if ok else "wrong"] += 1


def check_files(base: str, lines: list[str], counters: dict) -> None:
    """④ 文件生成：Markdown 完整；DOCX 非空且为 zip 魔数。"""
    lines.append("")
    lines.append("══ ④ 文件生成（Markdown + DOCX）══")

    md = _g_get(base, f"/tenders/{TENDER_ID}/document?format=markdown")
    content = md.get("markdown") or ""
    ok = (len(content) > 1000 and "## 封面" in content
          and "# 需求响应表" in content and "## 生成信息" in content)
    lines.append(f"[{_verdict(ok)}] Markdown 文档 {len(content)} 字，含封面/"
                 f"响应表/生成信息（total_sections={md.get('total_sections')}，"
                 f"done={md.get('done_sections')}）")
    counters["ok" if ok else "wrong"] += 1

    raw, ctype = _get_raw(base,
                          f"/api/generation/tenders/{TENDER_ID}/document"
                          f"?format=docx")
    ok = raw.startswith(b"PK") and len(raw) > 5000
    lines.append(f"[{_verdict(ok)}] DOCX {len(raw)} 字节，zip 魔数 PK"
                 f"（content-type: {ctype[:40]}）")
    counters["ok" if ok else "wrong"] += 1


# ═══════════════════════════════════════════════════════════════════════
def main() -> None:
    ap = argparse.ArgumentParser(description="M4 标书生成引擎验收核查")
    ap.add_argument("--host", default=DEFAULT_HOST, help="API 地址")
    args = ap.parse_args()
    base = args.host.rstrip("/")

    lines = [f"M4 标书生成引擎验收 | API {base} | 时间 {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    counters = {"ok": 0, "miss": 0, "wrong": 0}

    # ── 种子数据（幂等） ──
    db = Database(config.DB_PATH)
    emb_backend = seed(db, lines)

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

        # ── M4 四项核查（HTTP GET） ──
        flat = check_outline_and_job(base, lines, counters)
        if flat:
            check_coverage(base, lines, counters)
            check_traceability(base, lines, counters, db)
            check_files(base, lines, counters)
        else:
            lines.append("[MISS] 章节规划/生成失败，跳过覆盖/可追溯/文件核查")
            counters["miss"] += 1

    # ── 汇总 ──
    lines.append("")
    lines.append(f"══ 汇总: OK {counters['ok']} / MISS {counters['miss']} / "
                 f"WRONG {counters['wrong']} ══")
    lines.append("口径声明：以上结果基于项目内置验收基线（36 条需求 + 8 份企业资料，"
                 "与 tests/test_m3_matcher.py 同源）与离线确定性生成路径，"
                 "不代表通用准确率。真实样例端到端验收需配置 .env 中 LLM_API_KEY "
                 "后先跑 M1 提取与 M3 匹配（scripts/verify_m3_matching.py），"
                 f"生成阶段方案型章节将走真实 LLM（种子嵌入后端 {emb_backend}）。")

    report = "\n".join(lines)
    out = Path(__file__).resolve().parent / "_m4_verify_report.txt"
    out.write_text(report, encoding="utf-8")
    print(f"报告已写入: {out}")
    try:
        print(report)
    except UnicodeEncodeError:
        pass  # GBK 控制台打印失败以报告文件为准


if __name__ == "__main__":
    main()
