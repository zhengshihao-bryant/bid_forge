# -*- coding: utf-8 -*-
"""
matching/similarity.py —— 文本相似度与规范化工具（M3 共享）

全部确定性、零依赖：字符 bigram Jaccard / 重叠系数。离线测试与在线
（LLM 兜底）共用同一套阈值口径。原文回验的「精确匹配」= 去空白标点后的
包含关系 + 数字证据逐项回查，见 normalize_text / contains_normalized。
"""
from __future__ import annotations

import re

# 数字 token（含小数/百分号/千分位），用于"量化指标回原文核对"
_NUM_RE = re.compile(r"\d+(?:\.\d+)?%?")

# 操作符/语气词（用于原文比对时剔除"不少于/不高于"等不影响数值存在的词）
_NOISE_RE = re.compile(r"[\s　，。；：、（）()《》〈〉【】\[\]“”\"'‘’—…·~～！!？?%%,，.．]")


def normalize_text(text: str) -> str:
    """去空白 + 全半角标点 → 紧凑小写串（精确匹配 / 相似度的统一口径）。"""
    if not text:
        return ""
    t = _NOISE_RE.sub("", text)
    return t.lower()


def char_bigrams(text: str) -> set[str]:
    """字符 bigram 集合（中文友好，双字窗口）。"""
    t = normalize_text(text)
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def jaccard_similarity(a: str, b: str) -> float:
    """字符 bigram Jaccard 相似度 0-1（跨类型聚类阈值 0.6 的口径）。"""
    ga, gb = char_bigrams(a), char_bigrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def overlap_similarity(a: str, b: str) -> float:
    """重叠系数：共享 bigram / min(两侧长度)——短文本对长文本的包含更敏感。"""
    ga, gb = char_bigrams(a), char_bigrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / min(len(ga), len(gb))


def contains_normalized(haystack: str, needle: str) -> bool:
    """原文精确匹配：needle 去空白标点后是 haystack 的连续子串。"""
    n = normalize_text(needle)
    if not n:
        return False
    return n in normalize_text(haystack)


def find_longest_match(haystack: str, needle: str) -> str:
    """回验命中：返回 haystack 中与 needle 归一化后匹配的最长原文片段。

    找不到返回空串。用于 EvidenceValidator 回填 matched_text（M3-05）。
    """
    n = normalize_text(needle)
    if not n:
        return ""
    h = normalize_text(haystack)
    if not h or n not in h:
        return ""
    pos = h.index(n)
    # 从归一化位置映射回原文：按归一化前缀长度推进原文
    raw, walked = "", 0
    for ch in haystack:
        raw += ch
        if normalize_text(ch) == "":
            continue
        walked += 1
        if walked >= pos + len(n):
            break
    return raw


def numbers_in(text: str) -> list[str]:
    """文本中的数字 token 列表（原文顺序，供量化证据回查）。"""
    return _NUM_RE.findall(text or "")


def key_overlap(query: str, text: str) -> float:
    """关键词重叠得分 0-1：query 的 bigram 有多少出现在 text 中。

    （Rerank / 能力卡打分共用；比 Jaccard 更适合"查询短、文档长"的检索场景。）
    """
    gq = char_bigrams(query)
    if not gq:
        return 0.0
    gt = char_bigrams(text)
    if not gt:
        return 0.0
    return len(gq & gt) / len(gq)
