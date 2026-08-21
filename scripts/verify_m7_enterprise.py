# -*- coding: utf-8 -*-
"""
scripts/verify_m7_enterprise.py —— M7 企业级能力验收核查（HTTP 端到端）

流程（单阶段自包含）：
  1. 种子：seed_rbac 幂等兜底 + 清理 M7 验收表（tasks/agent_traces/agent_spans/
     knowledge_versions/llm_calls/audit_logs + T-M3 成员与质量行）
     + 复用 verify_m4.seed 重建 T-M3 基线（36 条原始需求 + 企业包）
  2. 全部请求带 JWT（服务以 AUTH_ENABLED=true 启动）——M7 起业务端点全面鉴权
  3. 核查（对应 M7-01~07，报告按此顺序输出）：
     ① 认证：错误口令 401 + login_failed 审计 / 5 演示账号登录 /
        /api/auth/me / 无 token 与伪造 token → 401
     ② RBAC：权限矩阵抽查（staff/editor/reviewer 越权 403、manager 放行、
        admin 旁路）+ 项目成员闭环（加成员→final 放行→移除→403、重复 409）
     ③ 审计：audit-logs 含 login/generate_bid/quality_check/task_cancel 等；
        manager 访问 admin 端点 403
     ④ KB 版本：资料重处理 → material_reprocess 版本行；能力卡 PATCH →
        capability_edit；新生成任务 kb_version = 最新 label 快照
     ⑤ 任务中心：extract/kb_process/match/generate/quality_check 5 类全 success；
        cancel 语义（own pending→cancelled / 他人 403 / running 409 / 不存在 404）；
        staff 只见自己任务
     ⑥ 链路/监控：/api/admin/traces 有 5 类 trace+spans（user_id 正确）；
        llm-calls 结构与汇总（无 LLM_API_KEY 时 MockLLM 不记录调用——已知口径）
     ⑦ 评估：retrieval/generation/trends/summary 均带 disclaimer；
        未知项目 404；staff → 403
  4. 报告写 scripts/_m7_verify_report.txt（UTF-8；控制台 GBK 安全打印）

口径声明：验收基于项目内置 T-M3 基线（verify_m4 种子）+ 样例文件；评估数字为
BidForge 内部离线评估集口径，不代表通用准确率；本脚本会清空 M7 验收表
（audit_logs/tasks/agent_traces/agent_spans/knowledge_versions/llm_calls）
以保证可重跑确定性，不触碰用户业务数据。

用法:
    python scripts/verify_m7_enterprise.py [--host http://127.0.0.1:8001]

前置: 服务以默认 AUTH_ENABLED=true 启动（同 README 一键脚本）；
Milvus 停或 MILVUS_ENABLED=false（同 verify_m3 口径）；无 LLM_API_KEY 时走 Mock（离线口径）

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
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))   # 复用 verify_m4 的种子与 HTTP 工具

from app import config  # noqa: E402  (加载 .env + 控制台 UTF-8 兜底)
from app.db import Database, seed_rbac  # noqa: E402

from verify_m4_generation import (  # noqa: E402
    DEFAULT_HOST, TENDER_ID, seed as seed_baseline)

POLL_TIMEOUT = 1200        # 后台任务轮询超时（秒）——与 verify_m4 同口径
POLL_INTERVAL = 3
TODAY = time.strftime("%Y-%m-%d")

# 5 演示账号（README 记录；admin 口令取 config，其余 同名+123）
DEMO_USERS = [
    (config.ADMIN_USERNAME, config.ADMIN_PASSWORD, "U-ADMIN", "admin"),
    ("manager", "manager123", "U-MANAGER", "bid_manager"),
    ("editor", "editor123", "U-EDITOR", "bid_editor"),
    ("reviewer", "reviewer123", "U-REVIEWER", "reviewer"),
    ("staff", "staff123", "U-STAFF", "staff"),
]

# 报告分组（按 M7-01~07 顺序输出；执行顺序按依赖排布）
GROUP_ORDER = ("auth", "rbac", "audit", "kb_version", "tasks", "trace", "eval")


# ═══════════════════════════════════════════════════════════════════════
# HTTP 工具（urllib + Bearer；镜像 verify_m4 风格）
# ═══════════════════════════════════════════════════════════════════════
def _http(method: str, base: str, path: str, token: str = "",
          body: bytes = b"", headers: dict | None = None,
          timeout: int = 180) -> dict:
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{base}{path}", data=body, method=method,
                                 headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code,
                "detail": e.read().decode("utf-8", "replace")}
    except urllib.error.URLError as e:
        return {"_http_error": -1, "detail": str(e.reason)}


def _get(base: str, path: str, token: str = "", **params) -> dict:
    if params:
        path += "?" + urllib.parse.urlencode(params)
    return _http("GET", base, path, token)


def _post(base: str, path: str, token: str = "", payload=None) -> dict:
    body = (json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None else b"")
    return _http("POST", base, path, token, body=body,
                 headers={"Content-Type": "application/json"})


def _patch(base: str, path: str, token: str = "", payload=None) -> dict:
    return _http("PATCH", base, path, token,
                 body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                 headers={"Content-Type": "application/json"})


def _delete(base: str, path: str, token: str = "") -> dict:
    return _http("DELETE", base, path, token)


def _upload(base: str, path: str, token: str, fields: dict,
            files: list, timeout: int = 300) -> dict:
    """multipart/form-data 上传（filename 用 ASCII 避免 urllib 头编码问题）。"""
    boundary = "----bidforge" + uuid.uuid4().hex
    buf = bytearray()
    for k, v in fields.items():
        buf += (f"--{boundary}\r\nContent-Disposition: form-data; "
                f"name=\"{k}\"\r\n\r\n{v}\r\n").encode("utf-8")
    for name, fname, data in files:
        buf += (f"--{boundary}\r\nContent-Disposition: form-data; "
                f"name=\"{name}\"; filename=\"{fname}\"\r\n"
                f"Content-Type: application/octet-stream\r\n\r\n").encode("utf-8")
        buf += data + b"\r\n"
    buf += f"--{boundary}--\r\n".encode("utf-8")
    return _http("POST", base, path, token, body=bytes(buf),
                 headers={"Content-Type":
                          f"multipart/form-data; boundary={boundary}"},
                 timeout=timeout)


def _poll(base: str, path: str, token: str, key: str, done: tuple,
          lines: list[str] | None = None,
          timeout: int = POLL_TIMEOUT) -> tuple[dict, bool]:
    """轮询 JSON 字段直至终态；超时/错误返回 (r, False)。"""
    t0 = time.time()
    while True:
        r = _get(base, path, token)
        if r.get("_http_error") or r.get(key) in done:
            return r, True
        if time.time() - t0 > timeout:
            return r, False
        time.sleep(POLL_INTERVAL)


def _verdict(ok: bool) -> str:
    return "OK" if ok else "WRONG"


def _mark(counters: dict, ok: bool) -> None:
    counters["ok" if ok else "wrong"] += 1


# ═══════════════════════════════════════════════════════════════════════
# 登录
# ═══════════════════════════════════════════════════════════════════════
def login(base: str, username: str, password: str) -> dict:
    return _post(base, "/api/auth/login",
                 payload={"username": username, "password": password})


# ═══════════════════════════════════════════════════════════════════════
# ① 认证（M7-01）
# ═══════════════════════════════════════════════════════════════════════
def check_auth(base: str, db: Database, tokens: dict, lines: list[str],
               counters: dict) -> None:
    # 错误口令 → 401
    r = login(base, config.ADMIN_USERNAME, "wrong-pass-xyz")
    ok = r.get("_http_error") == 401
    lines.append(f"[{_verdict(ok)}] 错误口令 → 401"
                 + ("" if ok else f"（实际 {r.get('_http_error')}）"))
    _mark(counters, ok)

    # 5 演示账号登录成功 + 角色正确（口令 = 同名+123 / admin 取 config）
    ok_all = True
    for uname, pwd, _, role in DEMO_USERS:
        r = login(base, uname, pwd)
        if not r.get("token") or role not in r.get("user", {}).get("roles", []):
            ok_all = False
            lines.append(f"[WRONG] {uname} 登录异常: {str(r)[:120]}")
    ok = ok_all
    lines.append(f"[{_verdict(ok)}] 5 演示账号登录全部成功且角色正确"
                 f"（{', '.join(u for u, _, _, _ in DEMO_USERS)}）")
    _mark(counters, ok)

    # admin 权限集 = 全部 17 项
    n_perms = db.query_one("SELECT COUNT(*) AS n FROM permissions")["n"]
    admin_user = tokens["user_admin"]
    perms = admin_user.get("permissions") or []
    ok = (admin_user.get("roles") == ["admin"]
          and len(perms) == n_perms and "project:view" in perms)
    lines.append(f"[{_verdict(ok)}] admin 响应含 token + roles=[admin] + "
                 f"{len(perms)}/{n_perms} 项权限")
    _mark(counters, ok)

    # /me
    r = _get(base, "/api/auth/me", tokens["admin"])
    ok = r.get("username") == config.ADMIN_USERNAME and r.get("id") == "U-ADMIN"
    lines.append(f"[{_verdict(ok)}] GET /api/auth/me → username="
                 f"{r.get('username')}")
    _mark(counters, ok)

    # 无 token / 伪造 token → 401
    r1 = _get(base, "/api/tenders")
    r2 = _get(base, "/api/tenders", token="garbage.token.value")
    ok = r1.get("_http_error") == 401 and r2.get("_http_error") == 401
    lines.append(f"[{_verdict(ok)}] 无 token / 伪造 token GET /api/tenders → "
                 f"401/401（实际 {r1.get('_http_error')}/{r2.get('_http_error')}）")
    _mark(counters, ok)


# ═══════════════════════════════════════════════════════════════════════
# ② RBAC（M7-02）
# ═══════════════════════════════════════════════════════════════════════
def check_rbac(base: str, tokens: dict, lines: list[str],
               counters: dict) -> None:
    staff, editor = tokens["staff"], tokens["editor"]
    reviewer, manager, admin = tokens["reviewer"], tokens["manager"], tokens["admin"]

    # staff：仅 final:view/export → 业务端点全 403
    cases = [
        ("staff", "GET", "/api/tenders", staff),
        ("staff", "GET", "/api/knowledge/materials", staff),
        ("staff", "POST", f"/api/generation/tenders/{TENDER_ID}/jobs", staff),
        ("staff", "POST", f"/api/quality/tenders/{TENDER_ID}/check", staff),
        ("staff", "GET", "/api/eval/retrieval", staff),
    ]
    codes = []
    for _, method, path, token in cases:
        r = (_post(base, path, token) if method == "POST"
             else _get(base, path, token))
        codes.append(r.get("_http_error"))
    ok = codes == [403] * len(cases)
    lines.append(f"[{_verdict(ok)}] staff 越权 5 项全 403（tenders/knowledge/"
                 f"generate:jobs/quality:check/eval:retrieval → "
                 f"{'/'.join(map(str, codes))}）")
    _mark(counters, ok)

    # editor：可生成但无 quality:check/confirm、无 knowledge:edit → 403
    r1 = _post(base, f"/api/quality/tenders/{TENDER_ID}/check", editor)
    r2 = _post(base, f"/api/quality/tenders/{TENDER_ID}/finalize", editor,
               payload={"force": True})
    r3 = _patch(base, "/api/knowledge/capabilities/CAP-0001", editor,
                payload={"attributes": {"年限": "6年"}})
    ok = all(r.get("_http_error") == 403 for r in (r1, r2, r3))
    lines.append(f"[{_verdict(ok)}] editor 越权 3 项全 403（quality:check/"
                 f"finalize/capabilities PATCH → "
                 f"{r1.get('_http_error')}/{r2.get('_http_error')}/"
                 f"{r3.get('_http_error')}）")
    _mark(counters, ok)

    # reviewer：无 bid:generate → 403；project:view → 200
    r1 = _post(base, f"/api/generation/tenders/{TENDER_ID}/jobs", reviewer)
    r2 = _get(base, f"/api/tenders/{TENDER_ID}", reviewer)
    ok = r1.get("_http_error") == 403 and r2.get("_http_error") is None
    lines.append(f"[{_verdict(ok)}] reviewer：POST /jobs → "
                 f"{r1.get('_http_error')}（403）；GET /api/tenders/T-M3 → "
                 f"{r2.get('_http_error') or 200}（放行）")
    _mark(counters, ok)

    # manager：全流程放行（project:view）
    r = _get(base, f"/api/tenders/{TENDER_ID}", manager)
    ok = r.get("_http_error") is None and r.get("id") == TENDER_ID
    lines.append(f"[{_verdict(ok)}] manager GET /api/tenders/T-M3 → "
                 f"{r.get('_http_error') or 200}（全流程放行）")
    _mark(counters, ok)

    # admin 旁路：/api/admin/users 全量 5 用户；manager 同端点 403
    r1 = _get(base, "/api/admin/users", admin)
    r2 = _get(base, "/api/admin/users", manager)
    names = sorted(u["username"] for u in (r1.get("users") or []))
    ok = (r2.get("_http_error") == 403
          and names == sorted(u for u, _, _, _ in DEMO_USERS))
    lines.append(f"[{_verdict(ok)}] admin GET /api/admin/users → "
                 f"{len(names)} 用户（{','.join(names)}）；manager 同端点 → "
                 f"{r2.get('_http_error')}（403 旁路阻断）")
    _mark(counters, ok)

    # ── 项目成员闭环（final:* 唯一强制成员校验的资源）──
    # staff 有 final:view 但非成员 → 403
    r = _get(base, f"/api/quality/tenders/{TENDER_ID}/final", staff)
    ok = r.get("_http_error") == 403
    lines.append(f"[{_verdict(ok)}] 成员闭环①：staff 非成员 GET final → "
                 f"{r.get('_http_error')}（403，虽持 final:view）")
    _mark(counters, ok)

    # manager 加成员 → staff 放行
    r = _post(base, f"/api/projects/{TENDER_ID}/members", manager,
              payload={"username": "staff"})
    ok = r.get("user_id") == "U-STAFF"
    lines.append(f"[{_verdict(ok)}] 成员闭环②：manager 添加成员 staff → "
                 f"{r.get('_http_error') or 201}（role={r.get('role')}）")
    _mark(counters, ok)

    r = _get(base, f"/api/quality/tenders/{TENDER_ID}/final", staff)
    ok = r.get("_http_error") is None
    lines.append(f"[{_verdict(ok)}] 成员闭环③：staff 成为成员后 GET final → "
                 f"{r.get('_http_error') or 200}（终版放行）")
    _mark(counters, ok)

    # staff 仍无 project:view → permissions 端点 403
    r = _get(base, f"/api/projects/{TENDER_ID}/permissions", staff)
    ok = r.get("_http_error") == 403
    lines.append(f"[{_verdict(ok)}] 成员闭环④：staff GET "
                 f"/api/projects/T-M3/permissions → {r.get('_http_error')} "
                 f"（403，成员身份不授予 project:view）")
    _mark(counters, ok)

    # 成员列表含 staff；重复添加 409
    r = _get(base, f"/api/projects/{TENDER_ID}/members", manager)
    members = {m["user_id"]: m for m in (r.get("members") or [])}
    r2 = _post(base, f"/api/projects/{TENDER_ID}/members", manager,
               payload={"username": "staff"})
    ok = "U-STAFF" in members and r2.get("_http_error") == 409
    lines.append(f"[{_verdict(ok)}] 成员闭环⑤：成员列表含 staff；重复添加 → "
                 f"{r2.get('_http_error')}（409）")
    _mark(counters, ok)

    # 移除成员 → staff 再 403
    r = _delete(base, f"/api/projects/{TENDER_ID}/members/U-STAFF", manager)
    r2 = _get(base, f"/api/quality/tenders/{TENDER_ID}/final", staff)
    ok = r.get("deleted") is True and r2.get("_http_error") == 403
    lines.append(f"[{_verdict(ok)}] 成员闭环⑥：移除成员 → staff GET final → "
                 f"{r2.get('_http_error')}（403，恢复阻断）")
    _mark(counters, ok)

    # staff 工作台仅交付视图（只要求登录；按角色裁剪）
    r = _get(base, "/api/workbench/projects", staff)
    ok = r.get("_http_error") is None and r.get("delivery_only") is True
    lines.append(f"[{_verdict(ok)}] staff GET /api/workbench/projects → "
                 f"{r.get('_http_error') or 200}（delivery_only="
                 f"{r.get('delivery_only')}）")
    _mark(counters, ok)


# ═══════════════════════════════════════════════════════════════════════
# ③ 审计（M7-03）
# ═══════════════════════════════════════════════════════════════════════
def check_audit(base: str, tokens: dict, lines: list[str],
                counters: dict) -> None:
    admin, manager = tokens["admin"], tokens["manager"]
    r = _get(base, "/api/admin/audit-logs", admin, limit=200)
    if r.get("_http_error") is not None:
        lines.append(f"[WRONG] GET /api/admin/audit-logs 失败: {str(r)[:160]}")
        counters["wrong"] += 1
        return
    actions = {log["action"] for log in r.get("logs", [])}
    expect = {"login", "login_failed", "upload_tender", "generate_outline",
              "generate_bid", "upload_knowledge", "edit_capability",
              "quality_check", "finalize_bid", "task_cancel", "member_add",
              "member_remove", "view_final"}
    missing = sorted(expect - actions)
    ok = not missing
    lines.append(f"[{_verdict(ok)}] audit-logs {r.get('total')} 条，含 "
                 f"{len(expect & actions)}/{len(expect)} 类关键动作"
                 + (f"，缺 {missing}" if missing else
                    f"（{', '.join(sorted(expect & actions))}）"))
    _mark(counters, ok)

    r = _get(base, "/api/admin/audit-logs", admin, action="login_failed",
             limit=50)
    ok = (r.get("total") or 0) >= 1
    lines.append(f"[{_verdict(ok)}] ?action=login_failed 过滤 → "
                 f"total={r.get('total')}（≥1）")
    _mark(counters, ok)

    r = _get(base, "/api/admin/audit-logs", manager)
    ok = r.get("_http_error") == 403
    lines.append(f"[{_verdict(ok)}] manager GET /api/admin/audit-logs → "
                 f"{r.get('_http_error')}（403，仅 admin）")
    _mark(counters, ok)


# ═══════════════════════════════════════════════════════════════════════
# ④ KB 版本（M7-04）——执行期函数：上传/处理/修订/快照，返回最新 label
# ═══════════════════════════════════════════════════════════════════════
def run_kb_versions(base: str, db: Database, tokens: dict,
                    lines: list[str], counters: dict) -> str:
    manager = tokens["manager"]
    kb_file = Path(config.SAMPLES_DIR) / "企业资料包" / "07_公司介绍.pdf"
    if not kb_file.exists():
        lines.append(f"[MISS] 样例 {kb_file.name} 不存在，跳过 KB 版本核查")
        counters["miss"] += 1
        return ""

    r = _upload(base, "/api/knowledge/materials", manager,
                {"category": "公司介绍"},
                [("files", kb_file.name.replace("公司介绍", "intro"),
                  kb_file.read_bytes())])
    if r.get("_http_error") is not None or not r.get("results") \
            or not r["results"][0].get("ok"):
        lines.append(f"[WRONG] 上传企业资料失败: {str(r)[:160]}")
        counters["wrong"] += 1
        return ""
    mid = r["results"][0]["material_id"]
    lines.append(f"[OK] 上传 {kb_file.name} → {mid}（parse "
                 f"{'ok' if r['results'][0]['ok'] else 'fail'}）")

    r = _post(base, f"/api/knowledge/materials/{mid}/process", manager)
    if r.get("_http_error") is not None:
        lines.append(f"[WRONG] 启动重处理失败: {str(r)[:160]}")
        counters["wrong"] += 1
        return ""
    pr, done = _poll(base, f"/api/knowledge/materials/{mid}", manager,
                     "process_status", ("已完成", "失败"))
    ok = done and pr.get("process_status") == "已完成"
    lines.append(f"[{_verdict(ok)}] 重处理 → {pr.get('process_status')}"
                 f"（chunks={pr.get('chunk_count')}）")
    _mark(counters, ok)

    row = db.query_one(
        "SELECT * FROM knowledge_versions WHERE change_type = "
        "'material_reprocess' ORDER BY id DESC LIMIT 1")
    ok = bool(row) and row["label"] == f"{TODAY}-v1" \
        and row["material_id"] == mid
    lines.append(f"[{_verdict(ok)}] material_reprocess 版本行 label="
                 f"{(row or {}).get('label')}（期望 {TODAY}-v1）")
    _mark(counters, ok)

    # 能力卡人工修订 → capability_edit 版本行（规格示例：张伟 5年→6年）
    r = _patch(base, "/api/knowledge/capabilities/CAP-0001", manager,
               payload={"attributes": {"年限": "6年"}})
    ok = r.get("version") == 2
    lines.append(f"[{_verdict(ok)}] PATCH CAP-0001 年限 5年→6年 → "
                 f"version={r.get('version')}（期望 2）")
    _mark(counters, ok)

    rows = db.query("SELECT * FROM knowledge_versions ORDER BY id DESC")
    ok = (len(rows) == 2 and rows[0]["change_type"] == "capability_edit"
          and rows[0]["label"] == f"{TODAY}-v2"
          and rows[0]["capability_id"] == "CAP-0001")
    latest = rows[0]["label"] if rows else ""
    lines.append(f"[{_verdict(ok)}] capability_edit 版本行 label="
                 f"{latest}（期望 {TODAY}-v2；共 {len(rows)} 行）")
    _mark(counters, ok)

    r = _get(base, "/api/knowledge/versions", manager)
    ok = r.get("total") == len(rows) and (r.get("versions") or [{}])[0].get("label") == latest
    lines.append(f"[{_verdict(ok)}] GET /api/knowledge/versions → "
                 f"total={r.get('total')}，最新 label="
                 f"{(r.get('versions') or [{}])[0].get('label')}")
    _mark(counters, ok)

    # 生成快照：新任务 kb_version = 最新 label
    r = _post(base, f"/api/generation/tenders/{TENDER_ID}/jobs", manager)
    if r.get("_http_error") is not None or not r.get("job_id"):
        lines.append(f"[WRONG] 启动快照生成任务失败: {str(r)[:160]}")
        counters["wrong"] += 1
        return latest
    job_id = r["job_id"]
    jr, done = _poll(base, f"/api/generation/tenders/{TENDER_ID}/jobs/{job_id}",
                     manager, "status", ("已完成", "部分失败", "失败"))
    ok = done and jr.get("status") == "已完成" and jr.get("kb_version") == latest
    lines.append(f"[{_verdict(ok)}] 新生成任务 {job_id} kb_version="
                 f"{jr.get('kb_version')}（期望快照 {latest}）")
    _mark(counters, ok)
    return latest


# ═══════════════════════════════════════════════════════════════════════
# ⑤ 任务中心（M7-05）——执行期：extract 上传/质量检查/终版/断言/cancel
# ═══════════════════════════════════════════════════════════════════════
def run_tasks(base: str, db: Database, tokens: dict, lines: list[str],
              counters: dict) -> None:
    manager, reviewer = tokens["manager"], tokens["reviewer"]
    staff, admin = tokens["staff"], tokens["admin"]

    # extract：上传新项目 → 提取
    sample = Path(config.SAMPLES_DIR) / "智慧园区项目" / "02_技术规格书.pdf"
    if not sample.exists():
        lines.append(f"[MISS] 样例 {sample.name} 不存在，跳过 extract 核查")
        counters["miss"] += 1
        new_tid = ""
    else:
        r = _upload(base, "/api/tenders", manager, {"name": "M7 任务中心验收项目"},
                    [("files", "02_spec.pdf", sample.read_bytes())])
        if r.get("_http_error") is not None or not r.get("id"):
            lines.append(f"[WRONG] 上传招标项目失败: {str(r)[:160]}")
            counters["wrong"] += 1
            new_tid = ""
        else:
            new_tid = r["id"]
            r = _post(base, f"/api/tenders/{new_tid}/extract", manager)
            ok = r.get("_http_error") is None \
                and r.get("task_id", "").startswith("TSK-")
            lines.append(f"[{_verdict(ok)}] 上传 {sample.name} → {new_tid}；"
                         f"POST /extract → 202 + {r.get('task_id')}")
            _mark(counters, ok)
            tr, done = _poll(base, f"/api/tenders/{new_tid}", admin,
                             "extraction_status", ("已完成", "失败"))
            ok = done and tr.get("extraction_status") == "已完成"
            lines.append(f"[{_verdict(ok)}] 提取完成 → "
                         f"{tr.get('extraction_status')}"
                         f"（需求 {tr.get('requirement_count')} 条）")
            _mark(counters, ok)

    # quality_check（reviewer；同步执行包一层任务）——跑两次给趋势一个 delta
    score = None
    for i, label in enumerate(("初检", "复检"), start=1):
        r = _post(base, f"/api/quality/tenders/{TENDER_ID}/check", reviewer)
        ok = r.get("_http_error") is None \
            and r.get("task_id", "").startswith("TSK-")
        score = (r.get("report") or {}).get("score")
        lines.append(f"[{_verdict(ok)}] quality_check {label}（reviewer）→ "
                     f"200 score={score} + {r.get('task_id')}")
        _mark(counters, ok)

    # 终版闭环（CRITICAL/ERROR 未清时 force；验收口径同 verify_m5）
    r = _post(base, f"/api/quality/tenders/{TENDER_ID}/finalize", reviewer,
              payload={"force": True})
    ok = r.get("_http_error") is None
    lines.append(f"[{_verdict(ok)}] finalize（force=true）→ "
                 f"{r.get('_http_error') or 200}（final.md/docx/"
                 f"quality-report.json）")
    _mark(counters, ok)

    # 断言：5 类任务全 success（DB 直查，含 started_by/ref_id）
    rows = db.query("SELECT * FROM tasks")
    by_type: dict[str, list] = {}
    for t in rows:
        by_type.setdefault(t["task_type"], []).append(t)
    ok = set(by_type) >= {"extract", "kb_process", "match", "generate",
                          "quality_check"}
    if not ok:
        lines.append(f"[WRONG] 任务类型缺失: {sorted(by_type)}")
        counters["wrong"] += 1
        return
    bad = [(t["task_type"], t["status"], t["error"]) for ts in by_type.values()
           for t in ts if t["status"] != "success"]
    ok = not bad
    lines.append(f"[{_verdict(ok)}] 5 类任务全部 success"
                 + (f"，异常: {bad}" if bad else
                    f"（共 {len(rows)} 条任务）"))
    _mark(counters, ok)

    ok = (all(t["started_by"] == "U-MANAGER" for t in by_type["extract"])
          and all(t["started_by"] == "U-REVIEWER"
                  for t in by_type["quality_check"])
          and all(t["started_by"] == "U-ADMIN" for t in by_type["match"]))
    lines.append(f"[{_verdict(ok)}] started_by：extract=U-MANAGER / "
                 f"quality_check=U-REVIEWER / match=U-ADMIN")
    _mark(counters, ok)

    job_row = db.query_one(
        "SELECT id FROM generation_jobs WHERE tender_id = ? "
        "ORDER BY created_at DESC, id DESC LIMIT 1", (TENDER_ID,))
    ok = bool(job_row) and any(t["ref_id"] == job_row["id"]
                               for t in by_type["generate"])
    lines.append(f"[{_verdict(ok)}] generate 任务 ref_id → generation_jobs.id"
                 f"（{job_row['id'] if job_row else '—'}）")
    _mark(counters, ok)

    # cancel 语义（合成行：own pending / 他人 pending / running / 终态）
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    synthetic = [
        ("TSK-VP1", "extract", "pending", "U-STAFF"),
        ("TSK-VP2", "match", "pending", "U-MANAGER"),
        ("TSK-VR1", "generate", "running", "U-STAFF"),
        ("TSK-VS1", "quality_check", "success", "U-STAFF"),
    ]
    for tid, ttype, status, by in synthetic:
        db.insert("tasks", {"id": tid, "task_type": ttype, "target_id": "",
                            "ref_id": "", "status": status, "progress": "",
                            "progress_pct": 100 if status == "success" else 0,
                            "total": 0, "done": 0, "error": "",
                            "started_by": by, "created_at": now,
                            "started_at": now if status == "running" else "",
                            "updated_at": now})

    r1 = _post(base, "/api/tasks/TSK-VP1/cancel", staff)
    r2 = _post(base, "/api/tasks/TSK-VP2/cancel", staff)
    r3 = _post(base, "/api/tasks/TSK-VR1/cancel", staff)
    r4 = _post(base, "/api/tasks/TSK-VS1/cancel", staff)
    r5 = _post(base, "/api/tasks/TSK-NOPE/cancel", staff)
    ok = (r1.get("status") == "cancelled" and r2.get("_http_error") == 403
          and r3.get("_http_error") == 409 and r4.get("_http_error") == 409
          and r5.get("_http_error") == 404)
    lines.append(f"[{_verdict(ok)}] cancel 语义：own pending → cancelled；"
                 f"他人 → 403；running → 409；终态 → 409；不存在 → 404"
                 f"（{r1.get('status') or r1.get('_http_error')}/"
                 f"{r2.get('_http_error')}/{r3.get('_http_error')}/"
                 f"{r4.get('_http_error')}/{r5.get('_http_error')}）")
    _mark(counters, ok)

    # 可见性：staff 只见自己任务；admin 全量 + 过滤
    r = _get(base, "/api/tasks", staff)
    ids = {t["id"] for t in r.get("tasks", [])}
    ok = ids == {"TSK-VP1", "TSK-VR1", "TSK-VS1"}
    lines.append(f"[{_verdict(ok)}] staff GET /api/tasks 只见自己任务 "
                 f"{sorted(ids) or '∅'}")
    _mark(counters, ok)

    r = _get(base, "/api/tasks", admin)
    ids = {t["id"] for t in r.get("tasks", [])}
    ok = {"TSK-VP1", "TSK-VP2", "TSK-VR1", "TSK-VS1"} <= ids \
        and len(ids) >= len(rows) + 4
    lines.append(f"[{_verdict(ok)}] admin GET /api/tasks 全量 {len(ids)} 条"
                 f"（含合成行与 5 类真实任务）")
    _mark(counters, ok)

    r = _get(base, "/api/tasks", admin, status="cancelled")
    ids = {t["id"] for t in r.get("tasks", [])}
    ok = "TSK-VP1" in ids
    lines.append(f"[{_verdict(ok)}] ?status=cancelled 过滤 → "
                 f"{sorted(ids)}（含 TSK-VP1）")
    _mark(counters, ok)


# ═══════════════════════════════════════════════════════════════════════
# ⑥ Agent 链路 + LLM 监控（M7-06）
# ═══════════════════════════════════════════════════════════════════════
def check_trace(base: str, db: Database, tokens: dict, lines: list[str],
                counters: dict) -> None:
    admin, manager = tokens["admin"], tokens["manager"]

    r = _get(base, "/api/admin/traces", admin, limit=100)
    traces = r.get("traces") or []
    by_type: dict[str, list] = {}
    for t in traces:
        by_type.setdefault(t["task_type"], []).append(t)
    want = {"extract", "kb_process", "match", "generate", "quality_check"}
    ok = set(by_type) >= want and all(
        any(t["status"] == "success" and len(t.get("spans") or []) >= 1
            for t in by_type[tt])
        for tt in want)
    lines.append(f"[{_verdict(ok)}] traces 含 5 类任务且各有一条 success "
                 f"trace（spans≥1）：{sorted(by_type)}")
    _mark(counters, ok)

    row = db.query_one(
        "SELECT * FROM agent_traces WHERE task_type = 'extract' "
        "ORDER BY id DESC LIMIT 1")
    row2 = db.query_one(
        "SELECT * FROM agent_traces WHERE task_type = 'quality_check' "
        "ORDER BY id DESC LIMIT 1")
    ok = (row and row["user_id"] == "U-MANAGER"
          and row2 and row2["user_id"] == "U-REVIEWER")
    lines.append(f"[{_verdict(ok)}] trace user_id：extract="
                 f"{(row or {}).get('user_id')}（期望 U-MANAGER）/ "
                 f"quality_check={(row2 or {}).get('user_id')}"
                 f"（期望 U-REVIEWER）")
    _mark(counters, ok)

    r = _get(base, "/api/admin/traces", admin, task_type="extract")
    filtered = r.get("traces") or []
    ok = bool(filtered) and all(t["task_type"] == "extract"
                                for t in filtered)
    lines.append(f"[{_verdict(ok)}] ?task_type=extract 过滤 → "
                 f"{len(filtered)} 条（全部 extract）")
    _mark(counters, ok)

    # llm-calls：结构 + 汇总（MockLLM 不记录调用——已知口径）
    r = _get(base, "/api/admin/llm-calls", admin)
    ok = r.get("_http_error") is None and "total" in r and "calls" in r \
        and "summary" in r
    total_calls = (r.get("summary") or {}).get("total_calls")
    if not config.LLM_API_KEY:
        ok = ok and total_calls == 0
    else:
        ok = ok and (total_calls or 0) >= 1
    lines.append(f"[{_verdict(ok)}] llm-calls 结构 + summary.total_calls="
                 f"{total_calls}（LLM "
                 f"{'未配置 → Mock 不记录调用（已知口径）' if not config.LLM_API_KEY else '已配置 → 应有调用记录'}）")
    _mark(counters, ok)

    r = _get(base, "/api/admin/traces", manager)
    ok = r.get("_http_error") == 403
    lines.append(f"[{_verdict(ok)}] manager GET /api/admin/traces → "
                 f"{r.get('_http_error')}（403，仅 admin）")
    _mark(counters, ok)


# ═══════════════════════════════════════════════════════════════════════
# ⑦ 评估体系（M7-07）
# ═══════════════════════════════════════════════════════════════════════
def check_eval(base: str, tokens: dict, lines: list[str],
               counters: dict) -> None:
    manager, staff = tokens["manager"], tokens["staff"]
    DISCLAIMER = "基于项目内离线评估集，不代表通用准确率"

    r = _get(base, "/api/eval/retrieval", manager, k=10)
    comb = r.get("combined") or {}
    ok = (r.get("_http_error") is None and r.get("disclaimer") == DISCLAIMER
          and (comb.get("evaluated") or 0) >= 1
          and "recall_at_k" in comb and "mrr" in comb)
    lines.append(f"[{_verdict(ok)}] retrieval：disclaimer + combined "
                 f"Recall@10={comb.get('recall_at_k')} / MRR={comb.get('mrr')}"
                 f"（evaluated={comb.get('evaluated')}）")
    _mark(counters, ok)

    r = _get(base, "/api/eval/generation", manager, tender_id=TENDER_ID)
    keys = ("citation_completeness", "citation_accuracy", "fact_consistency",
            "requirement_coverage")
    ok = (r.get("_http_error") is None and r.get("disclaimer") == DISCLAIMER
          and r.get("no_content") is False and all(k in r for k in keys))
    vals = {k: r.get(k) for k in keys}
    lines.append(f"[{_verdict(ok)}] generation：disclaimer + 4 指标"
                 f"（引用完整 {vals['citation_completeness']} / 引用准确 "
                 f"{vals['citation_accuracy']} / 事实一致 "
                 f"{vals['fact_consistency']} / 需求覆盖 "
                 f"{vals['requirement_coverage']}）")
    _mark(counters, ok)

    r = _get(base, "/api/eval/trends", manager, tender_id=TENDER_ID)
    reports = r.get("reports") or []
    deltas = r.get("deltas") or []
    ok = (r.get("_http_error") is None and r.get("disclaimer") == DISCLAIMER
          and len(reports) >= 2 and len(deltas) >= 1)
    d0 = deltas[0] if deltas else {}
    lines.append(f"[{_verdict(ok)}] trends：disclaimer + 报告序列 "
                 f"{len(reports)} 期 + 相邻 delta {len(deltas)} 个"
                 f"（{d0.get('from')}→{d0.get('to')} "
                 f"score_delta={d0.get('score_delta')}）")
    _mark(counters, ok)

    r = _get(base, "/api/eval/summary", manager, tender_id=TENDER_ID)
    ok = (r.get("_http_error") is None and r.get("disclaimer") == DISCLAIMER
          and "retrieval" in r and "generation" in r and "trends" in r)
    lines.append(f"[{_verdict(ok)}] summary：retrieval+generation+trends "
                 f"三合一（均带 disclaimer）")
    _mark(counters, ok)

    r = _get(base, "/api/eval/generation", manager, tender_id="T-NOPE")
    ok = r.get("_http_error") == 404
    lines.append(f"[{_verdict(ok)}] generation?tender_id=T-NOPE → "
                 f"{r.get('_http_error')}（404）")
    _mark(counters, ok)

    r = _get(base, "/api/eval/retrieval", staff)
    ok = r.get("_http_error") == 403
    lines.append(f"[{_verdict(ok)}] staff GET /api/eval/retrieval → "
                 f"{r.get('_http_error')}（403，无 knowledge:view）")
    _mark(counters, ok)


# ═══════════════════════════════════════════════════════════════════════
def main() -> None:
    ap = argparse.ArgumentParser(description="M7 企业级能力验收核查")
    ap.add_argument("--host", default=DEFAULT_HOST, help="API 地址")
    args = ap.parse_args()
    base = args.host.rstrip("/")

    head = [f"M7 企业级能力验收 | API {base} "
            f"| 时间 {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    counters = {"ok": 0, "miss": 0, "wrong": 0}
    groups: dict[str, list[str]] = {k: [] for k in GROUP_ORDER}
    seed_lines: list[str] = []

    # ── 种子：RBAC 兜底 + 清 M7 验收表 + T-M3 基线 ──
    db = Database(config.DB_PATH)
    db.init_schema()
    seed_rbac(db)          # 幂等：服务已启动则无操作；未启动也能保证演示账号
    for table in ("tasks", "agent_spans", "agent_traces",
                  "knowledge_versions", "llm_calls", "audit_logs"):
        db.execute(f"DELETE FROM {table}")
    db.execute("DELETE FROM project_members WHERE project_id = ?", (TENDER_ID,))
    db.execute("DELETE FROM quality_issues WHERE tender_id = ?", (TENDER_ID,))
    db.execute("DELETE FROM quality_reports WHERE tender_id = ?", (TENDER_ID,))
    db.execute("DELETE FROM review_records WHERE issue_id LIKE 'QR-%' "
               "OR issue_id LIKE 'FINALIZE:%'")
    seed_lines.append("[SEED] 清理 M7 验收表（tasks/traces/spans/"
                      "knowledge_versions/llm_calls/audit_logs + "
                      f"{TENDER_ID} 成员与质量行）——保证可重跑确定性")
    emb_backend = seed_baseline(db, seed_lines)   # T-M3 基线（36 需求 + 企业包）

    if not config.AUTH_ENABLED:
        seed_lines.append("[WARN] AUTH_ENABLED=false：鉴权关闭，"
                          "认证/RBAC 核查结果无效——请以默认配置重启服务")

    # ── 登录（顺带探测服务是否在跑）──
    probe = login(base, config.ADMIN_USERNAME, config.ADMIN_PASSWORD)
    if probe.get("_http_error") == -1:
        groups["auth"].append(f"[WRONG] 服务未启动（{base}）：请先 "
                              f"`cd backend && python -m uvicorn "
                              f"app.api.main:app --port 8001`")
        counters["wrong"] += 1
        _finish(head + seed_lines, groups, counters, emb_backend)
        return

    tokens: dict[str, str] = {}
    users: dict[str, dict] = {}
    for uname, pwd, _, _ in DEMO_USERS:
        r = login(base, uname, pwd)
        tokens[uname] = r.get("token") or ""
        users[uname] = r.get("user") or {}
    tokens["admin"] = tokens[config.ADMIN_USERNAME]   # admin 别名（用户名可能被 env 覆盖）
    tokens["user_admin"] = users[config.ADMIN_USERNAME]

    # ── ① 认证 ──
    check_auth(base, db, tokens, groups["auth"], counters)

    # ── 基线流程（admin：匹配 → 章节规划 → 生成；为任务中心/质量/评估铺路）──
    r = _post(base, f"/api/matching/tenders/{TENDER_ID}/match", tokens["admin"])
    baseline_lines = seed_lines
    if r.get("_http_error"):
        baseline_lines.append(f"[WRONG] 启动匹配失败: {str(r)[:160]}")
        counters["wrong"] += 1
    else:
        mr, done = _poll(base, f"/api/matching/tenders/{TENDER_ID}",
                         tokens["admin"], "status", ("已完成", "失败"))
        ok = done and mr.get("status") == "已完成"
        baseline_lines.append(f"[{_verdict(ok)}] 匹配（admin）→ "
                              f"{mr.get('status')}（match_count="
                              f"{mr.get('match_count')}）")
        _mark(counters, ok)

        o = _post(base, f"/api/generation/tenders/{TENDER_ID}/outline",
                  tokens["admin"])
        ok = o.get("total_sections") == 26
        baseline_lines.append(f"[{_verdict(ok)}] 章节规划（admin）→ "
                              f"{o.get('total_sections')} 章节（期望 26）")
        _mark(counters, ok)

        g = _post(base, f"/api/generation/tenders/{TENDER_ID}/jobs",
                  tokens["admin"])
        if g.get("_http_error") is not None or not g.get("job_id"):
            baseline_lines.append(f"[WRONG] 启动生成失败: {str(g)[:160]}")
            counters["wrong"] += 1
        else:
            gr, done = _poll(
                base, f"/api/generation/tenders/{TENDER_ID}/jobs/{g['job_id']}",
                tokens["admin"], "status", ("已完成", "部分失败", "失败"))
            ok = done and gr.get("status") == "已完成"
            baseline_lines.append(f"[{_verdict(ok)}] 生成（admin）→ "
                                  f"{gr.get('status')}"
                                  f"（{gr.get('done_sections')}/"
                                  f"{gr.get('total_sections')} 章节）")
            _mark(counters, ok)

    # ── ④ KB 版本（含 manager 二次生成任务的快照核查）──
    run_kb_versions(base, db, tokens, groups["kb_version"], counters)

    # ── ⑤ 任务中心（extract + 质量检查 + 终版 + cancel/可见性）──
    run_tasks(base, db, tokens, groups["tasks"], counters)

    # ── ② RBAC（403 矩阵 + 成员闭环——finalize 已就绪）──
    check_rbac(base, tokens, groups["rbac"], counters)

    # ── ③ 审计（全部动作完成后清点）──
    check_audit(base, tokens, groups["audit"], counters)

    # ── ⑥ 链路监控 ──
    check_trace(base, db, tokens, groups["trace"], counters)

    # ── ⑦ 评估 ──
    check_eval(base, tokens, groups["eval"], counters)

    _finish(head + seed_lines, groups, counters, emb_backend)


def _finish(head: list[str], groups: dict[str, list[str]],
            counters: dict, emb_backend: str) -> None:
    lines = list(head)
    for key in GROUP_ORDER:
        lines.append("")
        lines.append({
            "auth": "══ ① 认证（M7-01：PBKDF2 600k + JWT）══",
            "rbac": "══ ② RBAC（M7-02：5 角色 × 17 权限 + 项目成员三段判定）══",
            "audit": "══ ③ 操作审计（M7-03）══",
            "kb_version": "══ ④ 知识库版本（M7-04：KV 编号 + {日期}-v{n} + 生成快照）══",
            "tasks": "══ ⑤ 任务中心（M7-05：5 类任务 + cancel 语义）══",
            "trace": "══ ⑥ Agent 链路 + LLM 监控（M7-06：trace/span + llm_calls）══",
            "eval": "══ ⑦ 评估体系（M7-07：检索/生成/趋势/汇总）══",
        }[key])
        lines.extend(groups[key])

    lines.append("")
    lines.append(f"══ 汇总: OK {counters['ok']} / MISS {counters['miss']} / "
                 f"WRONG {counters['wrong']} ══")
    lines.append("口径声明：验收基于项目内置 T-M3 基线（verify_m4 种子，嵌入后端 "
                 f"{emb_backend}）+ 样例文件；评估数字为 BidForge 内部离线评估集"
                 "口径，不代表通用准确率；本脚本清空 M7 验收表（audit_logs/"
                 "tasks/agent_traces/agent_spans/knowledge_versions/llm_calls）"
                 "以保可重跑，不触碰用户业务数据；无 LLM_API_KEY 时全程 Mock"
                 "（离线口径，llm_calls 恒 0 为已知行为）。")

    report = "\n".join(lines)
    out = Path(__file__).resolve().parent / "_m7_verify_report.txt"
    out.write_text(report, encoding="utf-8")
    print(f"报告已写入: {out}")
    try:
        print(report)
    except UnicodeEncodeError:
        pass  # GBK 控制台打印失败以报告文件为准


if __name__ == "__main__":
    main()
