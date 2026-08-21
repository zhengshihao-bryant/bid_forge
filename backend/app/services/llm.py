# -*- coding: utf-8 -*-
"""
app/services/llm.py —— LLM 客户端（DeepSeek，OpenAI 兼容协议）

设计要点：
    - 自动重试 + 指数退避（transient errors：429/5xx/超时/连接）
    - 超时控制（120s：需求提取单窗口输出可达 4096 tokens，默认 10s 必挂）
    - 重试次数 3（提取窗口时 Pydantic 校验失败由提取层再兜底一次）
    - Mock 降级（无 Key 时全链路可离线跑通）
    - 工厂函数 create_llm_client

新增 chat_json()：
    - response_format={"type": "json_object"}（DeepSeek 官方支持；prompt 必须含 "json" 字样）
    - 容错解析：剥 markdown 围栏 → 首尾 {} 截取 → json.loads
    - 解析失败返回 None 而非抛异常（提取层自行决定重试策略）
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

from openai import OpenAI

from .. import config
from ..schemas import now_str

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# M7-06 LLM 调用指标（llm_calls 埋点）
# ═══════════════════════════════════════════════════════════════════════
_ctx = threading.local()


class llm_call_context:
    """线程局部调用方上下文（栈式）：包住调用点即可让 chat() 埋 caller。

    用法：
        with llm_call_context("extraction"):
            client.chat_json(...)
    """

    def __init__(self, caller: str):
        self.caller = caller

    def __enter__(self):
        stack = getattr(_ctx, "stack", None)
        if stack is None:
            stack = _ctx.stack = []
        stack.append(self.caller)
        return self

    def __exit__(self, *exc):
        _ctx.stack.pop()


def _current_caller() -> str:
    stack = getattr(_ctx, "stack", None)
    return stack[-1] if stack else ""


def _record_llm_call(caller: str, model: str, usage: Dict[str, int],
                     duration_ms: int, retries: int, success: int,
                     finish_reason: str = "", error: str = "") -> None:
    """写 llm_calls 行（M7-06）。写库异常绝不外抛——监控失败不能打断生成。"""
    try:
        from ..db import Database
        Database(config.DB_PATH).insert("llm_calls", {
            "caller": caller, "model": model,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "duration_ms": duration_ms, "retries": retries,
            "success": success, "finish_reason": finish_reason,
            "error": error, "created_at": now_str(),
        })
    except Exception as e:  # noqa: BLE001
        logger.debug("llm_calls 写入失败（不影响主流程）: %s", str(e)[:200])


class EnhancedLLMClient:
    """OpenAI 兼容 LLM 客户端（企业增强版：重试/超时/Mock 降级）。"""

    _RETRYABLE_STATUS = {429, 500, 502, 503, 504}

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "deepseek-chat",
        temperature: float = 0.1,
        max_tokens: int = 4096,
        timeout: int = 120,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        api_key = api_key or os.environ.get("LLM_API_KEY", "")
        base_url = base_url or os.environ.get(
            "LLM_BASE_URL", "https://api.deepseek.com")

        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,   # 重试由我们自己控制
        )

    # ------------------------------------------------------------------
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """调用 LLM 聊天补全（含自动重试）。

        Returns: {"content", "model", "usage", "finish_reason"}

        M7-06：重试循环外层埋 llm_calls（一次业务调用 = 一行，含重试次数/
        耗时/成败；不在 _call 埋——那会把每次重试记成独立调用）。
        """
        last_error: Optional[Exception] = None
        t0 = time.perf_counter()
        attempts = 0
        caller = _current_caller()

        for attempt in range(self.max_retries + 1):
            attempts += 1
            try:
                result = self._call(messages, temperature, max_tokens,
                                    response_format)
            except Exception as e:
                last_error = e
                if attempt < self.max_retries and self._is_retryable(e):
                    delay = self.retry_delay * (2 ** attempt)
                    logger.warning(
                        f"LLM 调用失败 (attempt {attempt + 1}/{self.max_retries + 1}), "
                        f"{delay:.1f}s 后重试: {e}")
                    time.sleep(delay)
                else:
                    break
            else:
                _record_llm_call(
                    caller=caller, model=result.get("model") or self.model,
                    usage=result.get("usage") or {},
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                    retries=attempts - 1, success=1,
                    finish_reason=result.get("finish_reason") or "")
                return result

        _record_llm_call(
            caller=caller, model=self.model, usage={},
            duration_ms=int((time.perf_counter() - t0) * 1000),
            retries=attempts - 1, success=0,
            error=str(last_error)[:300])
        raise last_error or RuntimeError("LLM call failed with unknown error")

    def _call(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float],
        max_tokens: Optional[int],
        response_format: Optional[dict],
    ) -> Dict[str, Any]:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature if temperature is not None else self.temperature,
            max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
            response_format=response_format,
        )
        choice = response.choices[0]
        usage = response.usage
        return {
            "content": choice.message.content or "",
            "model": response.model,
            "usage": {
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "total_tokens": usage.total_tokens if usage else 0,
            },
            "finish_reason": choice.finish_reason or "",
        }

    # ------------------------------------------------------------------
    def chat_json(
        self,
        system: str,
        user: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """JSON 结构化调用。

        Returns: {"data": dict, "finish_reason": str, "usage": dict}；
        JSON 解析失败返回 None（提取层自行决定重试），不抛异常。
        """
        try:
            resp = self.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            logger.warning(f"chat_json 调用失败: {e}")
            return None
        data = self._parse_json(resp["content"])
        if data is None:
            logger.warning("chat_json 解析失败: %s", resp["content"][:200])
            return None
        return {"data": data, "finish_reason": resp["finish_reason"], "usage": resp["usage"]}

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_json(content: str) -> Optional[Any]:
        """容错 JSON 解析：剥围栏 → 首尾 {} 截取。"""
        if not content:
            return None
        text = content.strip()
        m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
        if m:
            text = m.group(1).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        s, e = text.find("{"), text.rfind("}")
        if s != -1 and e > s:
            try:
                return json.loads(text[s:e + 1])
            except json.JSONDecodeError:
                pass
        return None

    @classmethod
    def _is_retryable(cls, error: Exception) -> bool:
        error_str = str(error).lower()
        if hasattr(error, "status_code"):
            return getattr(error, "status_code") in cls._RETRYABLE_STATUS
        retryable_keywords = [
            "timeout", "connection", "reset", "refused",
            "too many requests", "rate limit", "server error",
            "service unavailable", "internal server error",
        ]
        return any(kw in error_str for kw in retryable_keywords)


# ═══════════════════════════════════════════════════════════════════════
# Mock 客户端（无 Key 时全链路离线可跑）
# ═══════════════════════════════════════════════════════════════════════
class MockLLMClient:
    """Mock LLM 客户端 —— 返回空需求列表（离线验证管线用）。"""

    model = "mock"

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
    ) -> Dict[str, Any]:
        return {
            "content": '{"requirements": []}',
            "model": "mock",
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "finish_reason": "stop",
        }

    def chat_json(
        self,
        system: str,
        user: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        return {"data": {"requirements": []}, "finish_reason": "stop",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


def create_llm_client(
    api_key: str = "",
    base_url: str = "",
    model: str = "",
    temperature: float = 0.1,
    max_tokens: int = 4096,
    timeout: int = 120,
    max_retries: int = 3,
) -> Any:
    """工厂函数：有 API Key → EnhancedLLMClient，无 → MockLLMClient。"""
    api_key = api_key or os.environ.get("LLM_API_KEY", "")
    if api_key:
        return EnhancedLLMClient(
            api_key=api_key,
            base_url=base_url or os.environ.get("LLM_BASE_URL", "https://api.deepseek.com"),
            model=model or os.environ.get("LLM_MODEL", "deepseek-chat"),
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            max_retries=max_retries,
        )
    logger.warning("未检测到 LLM_API_KEY，使用 Mock 客户端（空结果，离线管线可用）")
    return MockLLMClient()


__all__ = ["EnhancedLLMClient", "MockLLMClient", "create_llm_client"]
