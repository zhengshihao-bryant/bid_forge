# -*- coding: utf-8 -*-
"""
app/auth/security.py —— 密码哈希（PBKDF2）与 JWT（HS256）

- 密码哈希：hashlib.pbkdf2_hmac("sha256")，600k 迭代（OWASP 建议），
  存储格式 pbkdf2_sha256$<迭代>$<salt hex>$<hash hex>；不引 passlib/bcrypt
  （passlib 1.7.4 与 bcrypt>=4.1 有兼容警告，内置库足够且零新依赖）
- JWT：pyjwt（钉 2.10.1，本机 mcp 工具链要求 >=2.10.1），
  payload {"sub": user_id, "iat", "exp"}，有效期 config.JWT_EXPIRE_HOURS
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from .. import config

_PBKDF2_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    """PBKDF2-SHA256 哈希，返回 'pbkdf2_sha256$迭代$salt$hash'。"""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"),
        bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验密码（hmac.compare_digest 防时序攻击；格式损坏返回 False）。"""
    try:
        algo, iters, salt, expected = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"),
            bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(dk.hex(), expected)
    except (ValueError, AttributeError):
        return False


def create_access_token(user_id: str) -> str:
    """签发 JWT（HS256 + config.JWT_SECRET）。"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(hours=config.JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> Optional[str]:
    """校验签名与有效期，返回 user_id；无效/过期 → None。"""
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None
