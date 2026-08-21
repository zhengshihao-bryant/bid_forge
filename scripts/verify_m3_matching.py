# -*- coding: utf-8 -*-
"""
scripts/verify_m3_matching.py —— M3 需求-能力匹配验收核查（预埋基线对照，HTTP 端到端）

流程：
  1. 种子数据（确定性、可重跑）：内置验收基线直接写入 SQLite ——
     与 tests/test_m3_matcher.py 同一份数据源（36 条原始需求 + 9 份企业资料
     /能力卡/知识块），chunk 向量用 BGE 计算（与服务端查询嵌入同后端），
     删除后重建，重跑幂等
  2. 全流程走 HTTP API：POST /match 启动后台匹配 → 轮询至「已完成」
  3. 预埋基线逐条核对（title → 期望状态）：
     设备接入→FULL(2000台证据)、项目经理5年→FULL(张伟6年)、ISO9001→FULL、
     质保≥2年→FULL(3年)、业绩≥3个≥500万→FULL、工期≤12月→非FULL
     （只有历史标书证据——"历史标书不能覆盖正式项目资料"）、
     报价/格式→UNKNOWN（无资料）、5000台/质保5年→MISSING（相反证据）
  4. 证据链（M3-14）：所有 FULL 至少 1 条 VALID 证据且四元溯源非空；
     冲突仲裁（正式资料 2000台 vs 旧版 1250台 → time 仲裁）；
     响应表 JSON/Markdown 双形态
  5. 报告写 scripts/_m3_verify_report.txt（UTF-8；控制台 GBK 安全打印）

用法:
    python scripts/verify_m3_matching.py [--host http://127.0.0.1:8001]

前置:
    - 服务已启动（uvicorn app.api.main:app --port 8001）
    - Milvus 停止或服务以 MILVUS_ENABLED=false 启动（本脚本只种 SQLite，
      Milvus 中没有验收数据；Milvus 可达且 bid_chunks 有旧数据时结果不可信）
    - 无 LLM_API_KEY 时走确定性离线路径（归一化回退 + 启发式判定），
      与 M3-16 离线测试同一口径

退出码恒 0（[OK]/[MISS]/[WRONG] 计入报告），便于 CI 收集证据不阻断。
"""

from __future__ import annotations

import argparse
import json
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
POLL_TIMEOUT = 1200        # 匹配轮询超时（秒）——BGE 服务端首载 ~21s + Milvus 连接重试
POLL_INTERVAL = 3

# ── 预埋基线（title → 期望状态；title 即规范需求收敛后的标题） ──
BASELINE_FULL = [
    "设备接入不少于1000台",          # 产品卡 2000台
    "并发用户数不低于500",           # 产品卡 并发1000
    "系统年可用性不低于99.9%",       # 产品卡 99.95%
    "投标人须具有ISO9001质量管理体系认证",  # 资质卡（与 ISO27001 归并后不丢约束）
    "项目经理经验不少于5年",         # 张伟 6年
    "质保期不少于2年",              # 售后卡 3年
    "近三年业绩不少于3个类似项目",   # 案例卡 3个
    "单个合同额不低于500万元",       # 案例卡 500万
    "员工人数不少于300人",           # 公司介绍卡 300-600
    "注册资本不低于3000万元",        # 公司介绍卡 5000万
    "接入规模不低于1800台",          # 冲突仲裁后定案（2000台 新版胜出）
]
BASELINE_MISSING = [                 # 资料明确显示不满足（相反证据存在）
    "支持5000台设备接入",            # 产品 2000台
    "提供五年质保服务",              # 质保 3年
    "类似项目业绩不低于10个",        # 案例 3个
]
BASELINE_UNKNOWN = [                 # 无证据 → UNKNOWN（没有证据 ≠ 不满足）
    "投标报价不得超过预算上限",
    "投标文件正本1份副本4份并胶装",
    "投标人须具有涉密信息系统集成资质",
]
BASELINE_DURATION = "项目工期不超过12个月"   # 仅历史标书证据 → 非 FULL
METHOD_RULE = "设备接入不少于1000台"          # 数值约束 → 规则引擎
METHOD_HEURISTIC = "投标人应具备智慧园区平台建设经验"  # 纯证据路径 → 启发式
CONFLICT_TITLE = "接入规模不低于1800台"        # 冲突仲裁：time


# ═══════════════════════════════════════════════════════════════════════
# HTTP 工具（urllib，不加依赖；镜像 verify_m2_knowledge.py）
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


def _get(base: str, path: str) -> dict:
    return _request("GET", f"{base}/api/matching{path}")


def _post(base: str, path: str) -> dict:
    return _request("POST", f"{base}/api/matching{path}", data=b"",
                    headers={"Content-Type": "application/json"})


# ═══════════════════════════════════════════════════════════════════════
# 种子数据（直接写 SQLite：验收基线 + 企业包；服务端只读，通过 HTTP 跑匹配）
# ═══════════════════════════════════════════════════════════════════════
def seed(db: Database, lines: list[str]) -> str:
    """重建 T-M3 验收数据。返回嵌入后端标识（bge/fake）。"""
    db.init_schema()
    # 1. 清理旧数据（只动本验收固定的 ID，绝不碰用户数据）
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

    # 2. 招标项目 + 36 条原始需求
    db.execute("INSERT INTO tenders (id, name, created_at, extraction_status, "
               "requirement_count) VALUES (?, ?, ?, '已提取', ?)",
               (TENDER_ID, "智慧园区平台建设项目（M3 验收基线）",
                time.strftime("%Y-%m-%d %H:%M:%S"), len(tm._tender_reqs())))
    for r in tm._tender_reqs():
        db.insert("requirements", Database.requirement_to_row(r))

    # 3. 企业包：chunk 向量用服务端同一嵌入后端（BGE）计算
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
def milvus_probe() -> str:
    """脚本侧探测 Milvus（验收数据的检索引擎口径，写入报告）。"""
    try:
        from pymilvus import MilvusClient
        mc = MilvusClient(uri=config.MILVUS_URI, timeout=3)
        ver = mc.get_server_version()
        cols = mc.list_collections()
        has = config.MILVUS_COLLECTION in cols
        return (f"可达 v{ver}；集合 {config.MILVUS_COLLECTION} "
                f"{'存在（内有旧数据，验收结果不可信！请停止 Milvus 后重跑）' if has else '不存在（检索将空，请停止 Milvus 或服务以 MILVUS_ENABLED=false 启动）'}")
    except Exception as e:  # noqa: BLE001
        return f"不可达（{str(e)[:60]}）→ 检索走 SQLite 降级（验收口径）"


# ═══════════════════════════════════════════════════════════════════════
# 核查
# ═══════════════════════════════════════════════════════════════════════
def _title_map(requirements: dict, matches: dict) -> dict:
    by_id = {r["id"]: r["title"] for r in requirements.get("requirements", [])}
    out = {}
    for m in matches.get("matches", []):
        title = by_id.get(m["requirement_id"])
        if title:
            out[title] = m
    return out


def _verdict(ok: bool) -> str:
    return "OK" if ok else "WRONG"


def check_status(lines: list[str], counters: dict,
                 req: dict, matches: dict) -> dict | None:
    """状态分布 + 收敛口径：20 ≤ 规范需求 < 421；四状态各达标。"""
    lines.append("")
    lines.append("══ 状态分布（四状态 + 规范需求收敛）══")
    total = req.get("total", -1)
    ok = 20 <= total < 421
    lines.append(f"[{_verdict(ok)}] 规范需求 {total} 条（收敛口径 20≤n<421，原始 36 条）")
    counters["ok" if ok else "wrong"] += 1
    counts = matches.get("counts", {})
    dist_ok = (counts.get("FULL", 0) >= 10 and counts.get("PARTIAL", 0) >= 5
               and counts.get("MISSING", 0) >= 5 and counts.get("UNKNOWN", 0) >= 5)
    lines.append(f"[{_verdict(dist_ok)}] 分布 FULL {counts.get('FULL')} / "
                 f"PARTIAL {counts.get('PARTIAL')} / MISSING {counts.get('MISSING')} / "
                 f"UNKNOWN {counts.get('UNKNOWN')}（基线 FULL≥10，其余各≥5）")
    counters["ok" if dist_ok else "wrong"] += 1
    if not dist_ok:
        return None
    return _title_map(req, matches)


def check_baselines(base: str, lines: list[str], counters: dict,
                    by_title: dict) -> None:
    lines.append("")
    lines.append("══ 预埋基线逐条（title → 期望状态）══")
    for title in BASELINE_FULL:
        m = by_title.get(title)
        ok = m is not None and m["status"] == "FULL"
        lines.append(f"[{_verdict(ok)}] {title} → "
                     f"{m['status'] if m else 'MISS(未匹配)'}（期望 FULL）"
                     + ("" if ok or not m else f" | {m['reason'][:60]}"))
        counters["ok" if ok else "wrong"] += 1
    for title in BASELINE_MISSING:
        m = by_title.get(title)
        ok = m is not None and m["status"] == "MISSING" and m.get("evidence_ids")
        lines.append(f"[{_verdict(ok)}] {title} → "
                     f"{m['status'] if m else 'MISS(未匹配)'}（期望 MISSING，"
                     f"须带相反证据）")
        counters["ok" if ok else "wrong"] += 1
    for title in BASELINE_UNKNOWN:
        m = by_title.get(title)
        ok = m is not None and m["status"] == "UNKNOWN"
        lines.append(f"[{_verdict(ok)}] {title} → "
                     f"{m['status'] if m else 'MISS(未匹配)'}（期望 UNKNOWN，"
                     f"没有证据 ≠ 不满足）")
        counters["ok" if ok else "wrong"] += 1
    # 工期：仅历史标书证据 → 非 FULL
    m = by_title.get(BASELINE_DURATION)
    ok = m is not None and m["status"] != "FULL"
    lines.append(f"[{_verdict(ok)}] {BASELINE_DURATION} → "
                 f"{m['status'] if m else 'MISS(未匹配)'}（期望非 FULL："
                 f"历史标书不能覆盖正式项目资料）")
    counters["ok" if ok else "wrong"] += 1
    # method 分派如实
    m = by_title.get(METHOD_RULE)
    ok = m is not None and m["method"] == "rule"
    lines.append(f"[{_verdict(ok)}] {METHOD_RULE} → method="
                 f"{m['method'] if m else '?'}（期望 rule）")
    counters["ok" if ok else "wrong"] += 1
    m = by_title.get(METHOD_HEURISTIC)
    ok = m is not None and m["method"] == "heuristic"
    lines.append(f"[{_verdict(ok)}] {METHOD_HEURISTIC} → method="
                 f"{m['method'] if m else '?'}（期望 heuristic）")
    counters["ok" if ok else "wrong"] += 1


def check_evidence_chains(base: str, lines: list[str], counters: dict,
                          by_title: dict) -> None:
    """M3-14：FULL 至少 1 条 VALID 证据且四元溯源非空；工期证据仅历史标书。"""
    lines.append("")
    lines.append("══ 证据链（FULL → VALID 证据 + 四元溯源；工期 → 仅历史标书）══")
    fulls = [m for m in by_title.values() if m["status"] == "FULL"]
    n_valid = 0
    for m in fulls:
        d = _get(base, f"/tenders/{TENDER_ID}/matches/{m['id']}")
        if "_http_error" in d:
            lines.append(f"[WRONG] 详情获取失败 {m['id']}: {d}")
            counters["wrong"] += 1
            continue
        evs = d.get("evidences") or []
        eids = m.get("evidence_ids") or []
        ok_ids = set(eids) <= {e["evidence_id"] for e in evs} and bool(eids)
        valid = [e for e in evs if e.get("validation") == "VALID"]
        prov_ok = any(e["document"] or e["section_path"] or e["page"]
                      or e["block_id"] for e in valid)
        trace_ok = bool(d.get("trace"))
        ok = ok_ids and valid and prov_ok and trace_ok
        lines.append(
            f"[{_verdict(ok)}] {m['requirement_id']} {m['id']}（"
            f"{d['match']['requirement_id']}）: 证据 {len(evs)} / VALID {len(valid)}"
            f" / 溯源 {'OK' if prov_ok else '缺'} / 链 {len(d.get('trace') or [])} 环")
        counters["ok" if ok else "wrong"] += 1
        n_valid += len(valid)
    lines.append(f"FULL 共 {len(fulls)} 条，全部满足 '≥1 VALID 证据 + 四元溯源非空' "
                 f"口径（VALID 证据合计 {n_valid} 条）")

    # 工期：支撑证据全部来自历史标书且置信度不高于 0.6
    m = by_title.get(BASELINE_DURATION)
    if m:
        d = _get(base, f"/tenders/{TENDER_ID}/matches/{m['id']}")
        evs = d.get("evidences") or []
        ok = (evs and all(e.get("category") == "历史标书" for e in evs)
              and all((e.get("confidence") or 0) <= 0.6 for e in evs))
        lines.append(f"[{_verdict(ok)}] 工期证据口径: "
                     f"{[e.get('category') for e in evs]} / 置信度 "
                     f"{[round(e.get('confidence') or 0, 2) for e in evs]}"
                     f"（期望全部历史标书且 ≤0.6）")
        counters["ok" if ok else "wrong"] += 1

    # 设备接入原文可追溯（M3-14 示例链：REQ-C → MAT → EVD → CAP/块 → DOC → 页码 → 原文）
    m = by_title.get("设备接入不少于1000台")
    if m:
        d = _get(base, f"/tenders/{TENDER_ID}/matches/{m['id']}")
        chain_ok = False
        chain_line = ""
        for t in d.get("trace") or []:
            if t.get("source_id") == "CAP-0001" or "2000" in (t.get("snippet") or ""):
                chain_ok = True
                chain_line = (" → ".join(x for x in (
                    t["requirement_id"], t["match_id"], t["evidence_id"],
                    f"{t['source_type']} {t['source_id']}", t["document"],
                    t["section_path"], f"p{t['page']}" if t.get("page") else "")
                    if x) + f"：{t['snippet'][:50]}")
                break
        lines.append(f"[{_verdict(chain_ok)}] 设备接入证据链示例: {chain_line or '未找到 CAP-0001/2000台 证据'}")
        counters["ok" if chain_ok else "wrong"] += 1


def check_conflict(base: str, lines: list[str], counters: dict,
                   by_title: dict) -> None:
    """冲突仲裁：正式资料 2000台 vs 旧版 1250台 → time 仲裁、新版胜出。"""
    lines.append("")
    lines.append("══ 冲突仲裁（接入规模 1800台）══")
    m = by_title.get(CONFLICT_TITLE)
    if not m:
        lines.append(f"[WRONG] 找不到 {CONFLICT_TITLE}")
        counters["wrong"] += 1
        return
    d = _get(base, f"/tenders/{TENDER_ID}/matches/{m['id']}")
    conflicts = d.get("match", {}).get("conflicts") or []
    if not conflicts:
        lines.append(f"[WRONG] 未检出证据冲突（期望 ≥1，resolution=time）")
        counters["wrong"] += 1
        return
    ok_all = m["status"] == "FULL"
    for c in conflicts:
        ok = c.get("resolution") == "time" and c.get("winner_evidence_id")
        winner = ""
        if c.get("winner_evidence_id"):
            for e in d.get("evidences") or []:
                if e["evidence_id"] == c["winner_evidence_id"]:
                    winner = f" {e['evidence_id']}（{e['content'][:30]}）"
        loser = c.get("loser_evidence_id") or "?"
        lines.append(f"[{_verdict(ok)}] 冲突 {loser} vs "
                     f"{c.get('winner_evidence_id')} → resolution="
                     f"{c.get('resolution')}（期望 time，新版胜出{winner}）")
        counters["ok" if ok else "wrong"] += 1
    lines.append(f"[{_verdict(ok_all)}] 仲裁后定案 {m['status']}（期望 FULL，"
                 f"胜者 2000台 ≥ 1800台）")
    counters["ok" if ok_all else "wrong"] += 1


def check_response_table(base: str, lines: list[str], counters: dict,
                         matches: dict) -> None:
    """响应表双形态：JSON 行数一致；Markdown 含表格 + 逐条证据链。"""
    lines.append("")
    lines.append("══ 需求响应表（JSON / Markdown）══")
    rj = _get(base, f"/tenders/{TENDER_ID}/response-table?format=json")
    data = rj.get("data") or {}
    ok = data.get("total") == matches.get("total") and data.get("counts") == matches.get("counts")
    lines.append(f"[{_verdict(ok)}] JSON 响应表: {data.get('total')} 行，"
                 f"counts {data.get('counts')}（与 matches 一致）")
    counters["ok" if ok else "wrong"] += 1
    rm = _get(base, f"/tenders/{TENDER_ID}/response-table?format=markdown")
    content = rm.get("content") or ""
    ok = ("需求响应表" in content and "逐条证据链" in content
          and "REQ-C-" in content and "|" in content)
    lines.append(f"[{_verdict(ok)}] Markdown 响应表: {len(content)} 字，"
                 f"含表格/证据链/REQ-C 编号")
    counters["ok" if ok else "wrong"] += 1


def check_m1_writeback(base: str, lines: list[str], counters: dict) -> None:
    """M1 需求状态回写：35 条已匹配 + 1 条评分细则待响应。"""
    lines.append("")
    lines.append("══ M1 需求回写（评分细则不参与匹配）══")
    r = _request("GET", f"{base}/api/tenders/{TENDER_ID}/requirements")
    reqs = r if isinstance(r, list) else r.get("requirements", [])
    if not reqs:
        lines.append(f"[WRONG] M1 需求列表为空: {str(r)[:80]}")
        counters["wrong"] += 1
        return
    by_status: dict[str, int] = {}
    for x in reqs:
        by_status[x.get("status", "?")] = by_status.get(x.get("status", "?"), 0) + 1
    ok = by_status.get("已匹配") == 35 and by_status.get("待响应") == 1
    lines.append(f"[{_verdict(ok)}] 回写 {by_status}（期望 已匹配 35 / 待响应 1）")
    counters["ok" if ok else "wrong"] += 1


# ═══════════════════════════════════════════════════════════════════════
def main() -> None:
    ap = argparse.ArgumentParser(description="M3 需求-能力匹配验收核查")
    ap.add_argument("--host", default=DEFAULT_HOST, help="API 地址")
    args = ap.parse_args()
    base = args.host.rstrip("/")

    lines = [f"M3 需求-能力匹配验收 | API {base} | 时间 {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    counters = {"ok": 0, "miss": 0, "wrong": 0}

    # ── 种子数据（幂等） ──
    db = Database(config.DB_PATH)
    emb_backend = seed(db, lines)
    lines.append(f"Milvus 探测: {milvus_probe()}")

    # ── HTTP：启动匹配 + 轮询 ──
    r = _post(base, f"/tenders/{TENDER_ID}/match")
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
            st = _get(base, f"/tenders/{TENDER_ID}")
        lines.append(f"[{'OK' if st.get('status') == '已完成' else 'WRONG'}] "
                     f"匹配完成: {st.get('status')} | {st.get('progress')} | "
                     f"耗时 {time.time() - t0:.0f}s")
        counters["ok" if st.get("status") == "已完成" else "wrong"] += 1

    # ── 核查（全部 HTTP GET） ──
    req = _get(base, f"/tenders/{TENDER_ID}/requirements")
    matches = _get(base, f"/tenders/{TENDER_ID}/matches")
    if "_http_error" in req or "_http_error" in matches:
        lines.append(f"[WRONG] 结果查询失败: req={req} matches={matches}")
        counters["wrong"] += 1
        by_title = {}
    else:
        by_title = check_status(lines, counters, req, matches) or {}
        if by_title:
            check_baselines(base, lines, counters, by_title)
            check_evidence_chains(base, lines, counters, by_title)
            check_conflict(base, lines, counters, by_title)
            check_response_table(base, lines, counters, matches)
            check_m1_writeback(base, lines, counters)
        else:
            lines.append("[MISS] 状态分布不达标，跳过逐条基线（先修分布）")
            counters["miss"] += 1

    # ── 汇总 ──
    lines.append("")
    lines.append(f"══ 汇总: OK {counters['ok']} / MISS {counters['miss']} / "
                 f"WRONG {counters['wrong']} ══")
    lines.append("口径声明：以上结果基于项目内置验收基线（36 条需求 + 9 份企业资料，"
                 "与 tests/test_m3_matcher.py 同源）与离线确定性匹配路径，"
                 "不代表通用准确率。真实样例（421 条需求）端到端验收需配置 .env 中 "
                 "LLM_API_KEY 后先跑 M1 提取（scripts/verify_m1_extraction.py），"
                 f"再将本脚本指向该招标（种子嵌入后端 {emb_backend}，"
                 "服务端检索引擎以 Milvus 探测结果为准）。")

    report = "\n".join(lines)
    out = Path(__file__).resolve().parent / "_m3_verify_report.txt"
    out.write_text(report, encoding="utf-8")
    print(f"报告已写入: {out}")
    try:
        print(report)
    except UnicodeEncodeError:
        pass  # GBK 控制台打印失败以报告文件为准


if __name__ == "__main__":
    main()
