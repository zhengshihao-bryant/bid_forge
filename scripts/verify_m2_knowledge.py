# -*- coding: utf-8 -*-
"""
scripts/verify_m2_knowledge.py —— M2 企业知识库验收核查（预埋基线对照）

流程：
  1. （默认）逐文件上传样例企业资料包（8 类）→ 触发后台处理 → 轮询至「已完成」
     （BGE 首载约 21s，轮询超时 10 分钟；--skip-ingest 复用已入库数据）
  2. 语义检索基线：6 条查询 → 期望 类别/文件/内容关键词，逐命中打印四元溯源
     （文件/章节路径/页码/块号 + snippet）与 engine（milvus/sqlite 降级透明）
  3. 能力卡事实核对：张伟（6 年/项目经理/PMP）、ISO9001（证书编号原样）
  4. 历史标书验收靶：处理完成且 0 张能力卡（只切块嵌入）
  5. 报告写 scripts/_m2_verify_report.txt（UTF-8；控制台 GBK 安全打印）

用法:
    python scripts/verify_m2_knowledge.py [--skip-ingest | --reprocess] [--host http://127.0.0.1:8001]

退出码恒 0（[OK]/[MISS]/[WRONG] 计入报告），便于 CI 收集证据不阻断。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
from app import config  # noqa: E402

DEFAULT_HOST = "http://127.0.0.1:8001"
POLL_TIMEOUT = 600          # 处理轮询超时（秒）——BGE 首载 ~21s + 8 文件嵌入
POLL_INTERVAL = 3
_BOUNDARY = "----kbverify7f2c3a9d"

# 8 类样例资料（类别 → 文件名，与样例包一致）
INGEST_PLAN = [
    ("产品", "01_产品介绍.pdf"),
    ("项目案例", "02_项目案例.docx"),
    ("公司资质", "03_公司资质.docx"),
    ("人员资质", "04_人员资质.docx"),
    ("技术方案", "05_技术方案.pdf"),
    ("售后服务", "06_售后服务.docx"),
    ("公司介绍", "07_公司介绍.pdf"),
    ("历史标书", "08_历史标书.docx"),
]

# 检索基线：查询 → (期望类别, 期望文件, 内容关键词)
SEARCH_BASELINE = [
    ("项目经理张伟经验", "人员资质", "04_人员资质.docx", ["张伟", "6"]),
    ("平台设备接入能力", "产品", "01_产品介绍.pdf", ["设备接入", "2000"]),
    ("公司员工数量", "公司介绍", "07_公司介绍.pdf", ["员工", "320"]),
    ("质保期多长时间", "售后服务", "06_售后服务.docx", ["质保", "3"]),
    ("ISO9001 证书编号", "公司资质", "03_公司资质.docx", ["00222Q12345R0S"]),
    ("智慧园区项目案例", "项目案例", "02_项目案例.docx", ["1250"]),
]


# ═══════════════════════════════════════════════════════════════════════
# HTTP 工具（urllib，不加依赖）
# ═══════════════════════════════════════════════════════════════════════
def _request(method: str, url: str, data: bytes | None = None,
             headers: dict | None = None, timeout: int = 120) -> dict:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return {"_http_error": e.code, "detail": body}


def _get(base: str, path: str) -> dict:
    return _request("GET", f"{base}/api/knowledge{path}")


def _post(base: str, path: str, data: bytes, ctype: str) -> dict:
    return _request("POST", f"{base}/api/knowledge{path}", data=data,
                    headers={"Content-Type": ctype})


def _multipart(category: str, path: Path) -> bytes:
    """手写 multipart 边界（单文件 + category 字段；文件名原样 UTF-8）。"""
    body = bytearray()
    body += (f"--{_BOUNDARY}\r\n"
             f'Content-Disposition: form-data; name="files"; filename="{path.name}"\r\n'
             "Content-Type: application/octet-stream\r\n\r\n").encode("utf-8")
    body += path.read_bytes() + b"\r\n"
    body += (f"--{_BOUNDARY}\r\n"
             'Content-Disposition: form-data; name="category"\r\n\r\n'
             ).encode("utf-8")
    body += category.encode("utf-8") + b"\r\n"
    body += f"--{_BOUNDARY}--\r\n".encode("utf-8")
    return bytes(body)


# ═══════════════════════════════════════════════════════════════════════
# 入库 + 处理
# ═══════════════════════════════════════════════════════════════════════
def ingest(base: str, sample_dir: Path, lines: list[str]) -> dict[str, dict]:
    """逐文件上传 → 处理 → 轮询至终态。返回 {file_name: material_dict}。"""
    mats: dict[str, dict] = {}
    for category, fname in INGEST_PLAN:
        fpath = sample_dir / fname
        if not fpath.exists():
            lines.append(f"[MISS] 样例文件缺失: {fpath}")
            continue
        r = _post(base, "/materials", _multipart(category, fpath),
                  f"multipart/form-data; boundary={_BOUNDARY}")
        if "_http_error" in r:
            lines.append(f"[WRONG] 上传 {fname} 失败: {r}")
            continue
        results = r.get("results") or []
        ok = [x for x in results if x.get("ok")]
        bad = [x for x in results if not x.get("ok")]
        if not ok:
            lines.append(f"[WRONG] 上传 {fname} 解析失败: {bad}")
            continue
        mid = ok[0]["material_id"]
        pr = _post(base, f"/materials/{mid}/process", b"", "application/json")
        if pr.get("_http_error"):
            lines.append(f"[WRONG] 处理启动失败 {fname}: {pr}")
            continue
        # 轮询至终态（TestClient 同步跑完是测试事；真实服务是后台任务）
        mat = {"process_status": "处理中"}
        t0 = time.time()
        while mat.get("process_status") not in ("已完成", "失败"):
            if time.time() - t0 > POLL_TIMEOUT:
                lines.append(f"[WRONG] 处理超时 {fname}（>{POLL_TIMEOUT}s）")
                break
            time.sleep(POLL_INTERVAL)
            mat = _get(base, f"/materials/{mid}")
        mats[fname] = mat
        status = mat.get("process_status")
        lines.append(
            f"[{'OK' if status == '已完成' else 'WRONG'}] {fname}（{status}）: "
            f"{mat.get('chunk_count')} 块 / {mat.get('capability_count')} 卡 / "
            f"索引 {mat.get('index_status')}")
    return mats


def reprocess(base: str, lines: list[str]) -> dict[str, dict]:
    """对已入库资料全部重跑处理（幂等；Milvus 恢复后重写向量索引用）。"""
    mats = _get(base, "/materials")
    out: dict[str, dict] = {}
    for m in mats:
        mid = m["id"]
        pr = _post(base, f"/materials/{mid}/process", b"", "application/json")
        if pr.get("_http_error"):
            lines.append(f"[WRONG] 重处理启动失败 {m['file_name']}: {pr}")
            continue
        mat = {"process_status": "处理中"}
        t0 = time.time()
        while mat.get("process_status") not in ("已完成", "失败"):
            if time.time() - t0 > POLL_TIMEOUT:
                lines.append(f"[WRONG] 重处理超时 {m['file_name']}")
                break
            time.sleep(POLL_INTERVAL)
            mat = _get(base, f"/materials/{mid}")
        out[m["file_name"]] = mat
        lines.append(
            f"[{'OK' if mat.get('process_status') == '已完成' else 'WRONG'}] "
            f"重处理 {m['file_name']}: {mat.get('chunk_count')} 块 / "
            f"{mat.get('capability_count')} 卡 / 索引 {mat.get('index_status')}")
    return out


# ═══════════════════════════════════════════════════════════════════════
# 核查
# ═══════════════════════════════════════════════════════════════════════
def check_search(base: str, lines: list[str], counters: dict) -> None:
    lines.append("")
    lines.append("══ 语义检索基线（命中文件 + 内容关键词 + 四元溯源）══")
    for query, exp_cat, exp_file, kws in SEARCH_BASELINE:
        r = _get(base, f"/search?q={urllib.parse.quote(query)}&top_k=5")
        if "_http_error" in r:
            lines.append(f"[WRONG] 检索失败 {query}: {r}")
            counters["wrong"] += 1
            continue
        engine = r.get("engine", "?")
        hits = r.get("hits") or []
        rank = next((i for i, h in enumerate(hits) if h["file_name"] == exp_file), None)
        verdict, detail = "MISS", ""
        if rank is not None:
            texts = [h.get("content", "") for h in hits]
            if all(any(kw in t for t in texts) for kw in kws):
                verdict = "OK" if rank == 0 else "OK(第%d位)" % (rank + 1)
            else:
                verdict = "WRONG"
            detail = f"期望类别[{exp_cat}] 实际[{hits[rank]['category']}]"
            if hits[rank]["category"] != exp_cat:
                verdict = "WRONG"
        lines.append(f"[{verdict}] {query} → {exp_file} | engine={engine} | {detail}")
        for h in hits[:3]:
            a = h.get("anchor") or {}
            lines.append(
                f"     #{h.get('chunk_id')} cos={h.get('score')} | {a.get('document')}"
                f" | {a.get('section_path') or '-'} | p{a.get('page')} | blk {a.get('block_id')}"
                f" | {a.get('snippet', '')[:60]}")
        counters["ok" if verdict.startswith("OK") else "miss" if verdict == "MISS"
                  else "wrong"] += 1


def check_capabilities(base: str, mats: dict, lines: list[str], counters: dict) -> None:
    lines.append("")
    lines.append("══ 能力卡事实核对（结构化字段原样）══")
    caps = _get(base, "/capabilities")
    if "_http_error" in caps:
        lines.append(f"[WRONG] 能力卡列表失败: {caps}")
        counters["wrong"] += 1
        return
    by_name = {c["name"]: c for c in caps}
    lines.append(f"共 {len(caps)} 张能力卡")

    def _fact(label: str, name_kw: str, checks: list[tuple[str, str]]) -> None:
        card = next((c for n, c in by_name.items() if name_kw in n), None)
        if card is None:
            lines.append(f"[MISS] {label}（无名称含「{name_kw}」的卡片）")
            counters["miss"] += 1
            return
        attrs = card.get("attributes") or {}
        fails = []
        for key, expect in checks:
            val = attrs.get(key)
            ok = (expect in val) if isinstance(val, (str, list)) else (val == expect)
            if not ok:
                fails.append(f"{key}={val!r}(期望含 {expect})")
        verdict = "OK" if not fails else "WRONG"
        lines.append(f"[{verdict}] {label}（{card['name']}）: {card['id']} | "
                     f"出处 {card['source_doc']}#p{card['source_page']}")
        if fails:
            lines.append(f"     字段不符: {'; '.join(fails)}")
        counters["ok" if not fails else "wrong"] += 1

    _fact("张伟", "张伟", [
        ("experience_years", "6"), ("role", "项目经理"), ("certs", "PMP"),
    ])
    _fact("ISO9001 证书编号", "ISO9001", [("cert_no", "00222Q12345R0S")])

    # 历史标书验收靶：只切块嵌入、不提取卡片
    hist = mats.get("08_历史标书.docx") or {}
    n_hist_caps = len([c for c in caps if c["source_doc"] == "08_历史标书.docx"])
    ok = hist.get("process_status") == "已完成" and n_hist_caps == 0
    lines.append(f"[{'OK' if ok else 'WRONG'}] 历史标书跳过卡片提取: "
                 f"状态 {hist.get('process_status')} / 卡片 {n_hist_caps}（期望 0）")
    counters["ok" if ok else "wrong"] += 1


# ═══════════════════════════════════════════════════════════════════════
def main() -> None:
    ap = argparse.ArgumentParser(description="M2 企业知识库验收核查")
    ap.add_argument("--skip-ingest", action="store_true", help="跳过上传/处理（复用已入库数据）")
    ap.add_argument("--reprocess", action="store_true",
                    help="对已入库资料全部重跑处理（Milvus 恢复后重写索引）")
    ap.add_argument("--host", default=DEFAULT_HOST, help="API 地址")
    args = ap.parse_args()
    base = args.host.rstrip("/")

    lines = [f"M2 企业知识库验收 | API {base} | 时间 {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    counters = {"ok": 0, "miss": 0, "wrong": 0}

    mats: dict[str, dict] = {}
    if args.reprocess:
        lines.append("[--reprocess] 重跑全部已入库资料的处理任务")
        mats = reprocess(base, lines)
    elif args.skip_ingest:
        mats = {m["file_name"]: m for m in _get(base, "/materials")}
        lines.append(f"[--skip-ingest] 复用已入库资料 {len(mats)} 份")
    else:
        sample_dir = config.SAMPLES_DIR / "企业资料包"
        lines.append(f"样例包: {sample_dir}")
        mats = ingest(base, sample_dir, lines)

    check_search(base, lines, counters)
    check_capabilities(base, mats, lines, counters)

    lines.append("")
    lines.append(f"══ 汇总: OK {counters['ok']} / MISS {counters['miss']} / WRONG {counters['wrong']} ══")
    lines.append("口径声明：以上结果基于本项目样例企业资料包与离线测试集，不代表通用准确率。")

    report = "\n".join(lines)
    out = Path(__file__).resolve().parent / "_m2_verify_report.txt"
    out.write_text(report, encoding="utf-8")
    print(f"报告已写入: {out}")
    try:
        print(report)
    except UnicodeEncodeError:
        pass  # GBK 控制台打印失败以报告文件为准


if __name__ == "__main__":
    main()
