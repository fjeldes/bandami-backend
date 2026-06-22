"""
JWT token generation/validation and password hashing utilities.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import UUID

import bcrypt
from jose import jwt, JWTError

from app.core.config import get_settings

settings = get_settings()

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24 hours
REFRESH_TOKEN_EXPIRE_DAYS = 7
VERIFICATION_TOKEN_EXPIRE_HOURS = 24


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())


def create_access_token(user_id: str, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = {"sub": str(user_id), "type": "access"}
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode["exp"] = expire
    to_encode["iat"] = datetime.now(timezone.utc)
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=ALGORITHM)


def create_refresh_token(user_id: str, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = {"sub": str(user_id), "type": "refresh"}
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))
    to_encode["exp"] = expire
    to_encode["iat"] = datetime.now(timezone.utc)
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=ALGORITHM)


def create_verification_token(user_id: str) -> str:
    to_encode = {"sub": str(user_id), "type": "verification"}
    expire = datetime.now(timezone.utc) + timedelta(hours=VERIFICATION_TOKEN_EXPIRE_HOURS)
    to_encode["exp"] = expire
    to_encode["iat"] = datetime.now(timezone.utc)
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    if not token or not isinstance(token, str) or not token.strip():
        raise ValueError("Invalid or expired token")
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[ALGORITHM])
        return payload
    except Exception:
        raise ValueError("Invalid or expired token")


def decode_access_token(token: str) -> str:
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise ValueError("Invalid token type")
    return payload["sub"]


def decode_verification_token(token: str) -> str:
    payload = decode_token(token)
    if payload.get("type") != "verification":
        raise ValueError("Invalid token type")
    return payload["sub"]
