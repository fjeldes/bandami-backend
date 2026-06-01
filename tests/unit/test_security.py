"""
Unit tests for app/core/security.py — password hashing and JWT tokens.
"""

import time
from datetime import timedelta

import pytest
from jose import jwt

from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    create_verification_token,
    decode_token,
    decode_access_token,
    decode_verification_token,
)


# ── Password hashing ──

class TestPasswordHashing:
    def test_hash_returns_different_from_input(self):
        hashed = hash_password("mypassword")
        assert hashed != "mypassword"
        assert hashed.startswith("$2b$") or hashed.startswith("$2a$")

    def test_verify_correct_password(self):
        hashed = hash_password("correct-horse-battery-staple")
        assert verify_password("correct-horse-battery-staple", hashed)

    def test_verify_wrong_password(self):
        hashed = hash_password("correct")
        assert not verify_password("wrong", hashed)

    def test_verify_empty_password(self):
        hashed = hash_password("")
        assert verify_password("", hashed)

    def test_verify_unicode_password(self):
        pwd = "contraseña-日本語-🚀"
        hashed = hash_password(pwd)
        assert verify_password(pwd, hashed)

    def test_same_password_produces_different_hash(self):
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2

    def test_very_long_password_limited_to_72_bytes(self):
        pwd = "a" * 71
        hashed = hash_password(pwd)
        assert verify_password(pwd, hashed)

    def test_special_characters_password(self):
        pwd = "!@#$%^&*()_+-=[]{}|;:',.<>?/`~"
        hashed = hash_password(pwd)
        assert verify_password(pwd, hashed)


# ── JWT Tokens ──

USER_ID = "550e8400-e29b-41d4-a716-446655440000"


class TestAccessToken:
    def test_create_and_decode(self):
        token = create_access_token(USER_ID)
        assert token.count(".") == 2
        result = decode_access_token(token)
        assert result == USER_ID

    def test_includes_type_claim(self):
        token = create_access_token(USER_ID)
        payload = decode_token(token)
        assert payload["type"] == "access"
        assert payload["sub"] == USER_ID

    def test_includes_expiration(self):
        token = create_access_token(USER_ID)
        payload = decode_token(token)
        assert "exp" in payload
        assert "iat" in payload

    def test_custom_expiration(self):
        token = create_access_token(USER_ID, expires_delta=timedelta(seconds=5))
        payload = decode_token(token)
        assert "exp" in payload

    def test_expired_token_raises_value_error(self):
        token = create_access_token(USER_ID, expires_delta=timedelta(seconds=-1))
        with pytest.raises(ValueError, match="Invalid or expired token"):
            decode_access_token(token)

    def test_tampered_token_raises_value_error(self):
        token = create_access_token(USER_ID)
        tampered = token[:-5] + "abcde"
        with pytest.raises(ValueError, match="Invalid or expired token"):
            decode_access_token(tampered)

    def test_token_signed_with_different_key_rejected(self):
        from jose import jwt as jose_jwt
        signed = jose_jwt.encode({"sub": USER_ID}, "a-different-wrong-secret-not-the-app-one", algorithm="HS256")
        with pytest.raises(ValueError):
            decode_token(signed)

    def test_empty_token_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid or expired token"):
            decode_access_token("")

    def test_nonsense_token_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid or expired token"):
            decode_access_token("not.a.jwt")


class TestRefreshToken:
    def test_create_refresh_has_type_refresh(self):
        token = create_refresh_token(USER_ID)
        payload = decode_token(token)
        assert payload["type"] == "refresh"
        assert payload["sub"] == USER_ID

    def test_refresh_token_decoded_as_access_raises(self):
        token = create_refresh_token(USER_ID)
        with pytest.raises(ValueError, match="Invalid token type"):
            decode_access_token(token)


class TestVerificationToken:
    def test_create_and_decode(self):
        token = create_verification_token(USER_ID)
        result = decode_verification_token(token)
        assert result == USER_ID

    def test_has_type_verification(self):
        token = create_verification_token(USER_ID)
        payload = decode_token(token)
        assert payload["type"] == "verification"

    def test_verification_used_as_access_raises(self):
        token = create_verification_token(USER_ID)
        with pytest.raises(ValueError, match="Invalid token type"):
            decode_access_token(token)

    def test_expired_verification_token_raises(self):
        import app.core.security as sec
        old = sec.VERIFICATION_TOKEN_EXPIRE_HOURS
        sec.VERIFICATION_TOKEN_EXPIRE_HOURS = -1
        token = create_verification_token(USER_ID)
        sec.VERIFICATION_TOKEN_EXPIRE_HOURS = old
        with pytest.raises(ValueError, match="Invalid or expired token"):
            decode_verification_token(token)


class TestDecodeToken:
    def test_decode_returns_full_payload(self):
        token = create_access_token(USER_ID)
        payload = decode_token(token)
        assert payload["sub"] == USER_ID
        assert payload["type"] == "access"
        assert "exp" in payload
        assert "iat" in payload

    def test_none_token_raises(self):
        with pytest.raises(ValueError):
            decode_token(None)

    def test_blank_token_raises(self):
        with pytest.raises(ValueError, match="Invalid or expired token"):
            decode_token("   ")
