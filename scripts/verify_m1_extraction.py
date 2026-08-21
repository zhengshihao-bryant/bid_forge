# -*- coding: utf-8 -*-
"""
scripts/verify_m1_extraction.py —— M1 提取结果验收核查（预埋基线对照）

用法: python scripts/verify_m1_extraction.py <tender_id>
输出: 核查报告（控制台 + 写文件避免 GBK 乱码）
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
from app import config  # noqa: E402

API = "http://127.0.0.1:8001/api"


def fetch(path: str):
    with urllib.request.urlopen(f"{API}{path}", timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def main(tender_id: str) -> None:
    tender = fetch(f"/tenders/{tender_id}")
    reqs = fetch(f"/tenders/{tender_id}/requirements")
    points = fetch(f"/tenders/{tender_id}/score-points")

    lines: list[str] = []
    lines.append(f"项目: {tender['name']}（{tender_id}）")
    lines.append(f"状态: {tender['extraction_status']} | {tender['extraction_progress']}")
    lines.append(f"需求 {len(reqs)} 条 / 评分点 {len(points)} 个")
    lines.append("")

    # ── 预埋基线核查 ──
    baseline = {
        "设备接入≥1000": ("设备接入", "1000"),
        "并发≥500": ("并发", "500"),
        "可用性99.9%": ("99.9", "99.9"),
        "工期12个月": ("工期", "12"),
        "项目经理5年": ("项目经理", "5"),
        "质保2年": ("质保", "2"),
        "业绩3个": ("业绩", "3"),
        "正本1副本4": ("正本", "1"),
        "副本4": ("副本", "4"),
        "人脸识别99.5%": ("人脸", "99.5"),
        "存储90天": ("90", "90"),
        "评分50/20/30": ("评分", "50"),
        "响应2小时": ("响应", "2"),
        "驻场2人": ("驻场", "2"),
    }
    lines.append("══ 预埋基线核查（关键词 → 命中条目）══")
    haystack = [(r["id"], r["title"], r["original_text"],
                 r["quantitative"], r["is_star"], r["importance"],
                 (r["source"] or {}).get("document", ""),
                 (r["source"] or {}).get("page"))
                for r in reqs]
    for label, (kw, _) in baseline.items():
        hits = [h for h in haystack if kw in h[1] or kw in h[2]]
        lines.append(f"[{'OK' if hits else 'MISS'}] {label}（{kw}）: {len(hits)} 条")
        for h in hits[:3]:
            q = " ".join(f"{x['op']}{x['value']}{x['unit']}" for x in h[3])
            lines.append(f"      {h[0]} {h[1][:40]} | 量化[{q}] | ★={h[4]} 重要={h[5]} | {h[6]}#p{h[7]}")

    # ── 关键数值原样核对 ──
    lines.append("")
    lines.append("══ 事实约束抽查（数字必须原样）══")
    for title_kw, expect in [
        ("人脸", "99.5"),      # 规格书预埋 99.5%
        ("车牌", "99"),        # 白天 ≥99%
        ("设备接入", "1000"),
        ("并发", "500"),
        ("MTBF", "50000"),
    ]:
        for h in haystack:
            if title_kw in h[1]:
                q = " ".join(f"{x['op']}{x['value']}{x['unit']}" for x in h[3])
                ok = expect in q
                lines.append(f"[{'OK' if ok else 'WRONG'}] 含[{title_kw}] 期望含 {expect} → 量化[{q}] | {h[1][:40]} | 原文: {h[2][:60]}")

    # ── ★ 条款 ──
    lines.append("")
    lines.append("══ ★条款（is_star=True，应含 项目经理5年 / ISO 三件套）══")
    for h in [x for x in haystack if x[4]]:
        lines.append(f"  ★ {h[1][:45]} | 重要={h[5]} | {h[6]}")

    # ── 评分点 ──
    lines.append("")
    lines.append("══ 评分点（期望 技术9 + 商务4 = 13，权重和 70）══")
    by_cat: dict[str, int] = {}
    for p in points:
        by_cat[p["category"]] = by_cat.get(p["category"], 0) + 1
    lines.append(f"类别分布: {by_cat} | 权重和: {sum(p['weight'] for p in points)}")
    for p in points:
        lines.append(f"  {p['category']} {p['item']} = {p['max_score']}分 | {p['source_ref']}")

    # ── 类型分布 ──
    lines.append("")
    lines.append("══ 需求类型分布 ══")
    by_type: dict[str, int] = {}
    for h in haystack:
        t = next((r["type"] for r in reqs if r["id"] == h[0]), "?")
        by_type[t] = by_type.get(t, 0) + 1
    for t, n in sorted(by_type.items(), key=lambda x: -x[1]):
        lines.append(f"  {t}: {n}")

    report = "\n".join(lines)
    out = Path(__file__).resolve().parent / "_m1_verify_report.txt"
    out.write_text(report, encoding="utf-8")
    print(f"报告已写入: {out}")
    # 控制台也打印（GBK 可能乱码，以文件为准）
    try:
        print(report)
    except UnicodeEncodeError:
        pass


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "10be0c4c18aa")
