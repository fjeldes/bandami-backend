"""
Auth router: register, login, refresh, verify-email, logout, password reset.
Uses SQLAlchemy ORM — no Supabase dependency.
"""

import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Body, Depends, BackgroundTasks, Request, Response, Cookie
from pydantic import BaseModel, EmailStr, Field
from uuid import uuid4
import hashlib
import secrets

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db.deps import get_db
from app.models.user import UserProfile, RefreshToken
from app.models.subscription import UserCreditPack
from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    create_verification_token,
    decode_verification_token,
)
from app.core.security import decode_token as _decode_token
from app.services.email_service import send_verification_email, send_password_reset_email
from app.core.limiter import limiter
from app.core.auth import _calc_plan_info

logger = logging.getLogger("ielts.auth")
router = APIRouter()


def _set_refresh_cookie(response: Response, token: str, max_age: int = 60 * 60 * 24 * 30):
    response.set_cookie(
        key="refresh_token",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=max_age,
        path="/api/v1/auth",
    )


def _clear_refresh_cookie(response: Response):
    response.delete_cookie(key="refresh_token", path="/api/v1/auth")


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str = Field(..., min_length=1, max_length=200)
    referral_code: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class VerifyEmailRequest(BaseModel):
    token: str


class ResendVerificationRequest(BaseModel):
    email: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str = Field(..., min_length=8, max_length=128)


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    user: dict


class MessageResponse(BaseModel):
    message: str


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _store_refresh_token(db: Session, user_id: str, token: str) -> None:
    db.add(RefreshToken(
        id=str(uuid4()),
        user_id=user_id,
        token_hash=_hash_token(token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    ))
    db.commit()


def _revoke_user_tokens(db: Session, user_id: str) -> None:
    db.query(RefreshToken).filter(
        RefreshToken.user_id == user_id,
        RefreshToken.revoked == False,
    ).update({"revoked": True})
    db.commit()


def _user_response(user: UserProfile, db: Session = None) -> dict:
    tier = user.subscription_tier or "free"
    discounts = user.referral_discounts or 0
    if db:
        plan = _calc_plan_info(db, str(user.id))
        tier = plan["tier"]
        discounts = plan.get("referral_discounts", 0)
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name or "",
        "subscription_tier": tier,
        "role": user.role or "user",
        "referral_code": user.referral_code or "",
        "referral_discounts": discounts,
    }


# ---- Endpoints ----

@router.post("/register", response_model=MessageResponse)
@limiter.limit("5/minute")
async def register(
    request: Request,
    body: RegisterRequest = Body(...),
    db: Session = Depends(get_db),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    existing = db.execute(select(UserProfile.id).where(UserProfile.email == body.email)).scalar()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    user_id = str(uuid4())
    hashed = hash_password(body.password)
    referral_code = secrets.token_hex(4).upper()[:8]

    referred_by = None
    if body.referral_code:
        referrer = db.execute(select(UserProfile).where(UserProfile.referral_code == body.referral_code.upper())).scalar()
        if referrer and str(referrer.id) != user_id:
            referred_by = str(referrer.id)

    db.add(UserProfile(
        id=user_id,
        email=body.email,
        hashed_password=hashed,
        full_name=body.full_name,
        subscription_tier="free",
        referral_code=referral_code,
        referred_by=referred_by,
    ))
    db.commit()

    if referred_by:
        referrer = db.query(UserProfile).filter(UserProfile.id == referred_by).first()
        if referrer:
            referrer.referral_discounts = (referrer.referral_discounts or 0) + 1
            new_user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
            if new_user:
                new_user.referral_discounts = (new_user.referral_discounts or 0) + 1
            db.commit()

    verification_token = create_verification_token(user_id)

    background_tasks.add_task(send_verification_email, body.email, body.full_name, verification_token)

    logger.info("User registered uid=%s email=%s referral=%s", user_id, body.email, bool(referred_by))
    return MessageResponse(message="Account created. Please check your email to verify your account.")


@router.post("/login", response_model=AuthResponse)
@limiter.limit("5/minute")
async def login(
    request: Request,
    body: LoginRequest = Body(...),
    db: Session = Depends(get_db),
):
    user = db.execute(select(UserProfile).where(UserProfile.email == body.email)).scalar()
    if not user or not user.hashed_password:
        logger.warning("Login failed: user not found email=%s", body.email)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not verify_password(body.password, user.hashed_password):
        logger.warning("Login failed: wrong password uid=%s email=%s", user.id, body.email)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.email_confirmed_at:
        raise HTTPException(status_code=403, detail="Email not confirmed. Please verify your email.")

    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))

    _store_refresh_token(db, str(user.id), refresh_token)

    # Update last_active_at
    now = datetime.now(timezone.utc)
    db.query(UserProfile).filter(UserProfile.id == user.id).update({UserProfile.last_active_at: now})
    db.commit()

    logger.info("Login success uid=%s email=%s tier=%s", user.id, user.email, _user_response(user, db)["subscription_tier"])
    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=_user_response(user, db),
    )


@router.post("/refresh", response_model=AuthResponse)
async def refresh(
    body: RefreshRequest = Body(...),
    db: Session = Depends(get_db),
):
    raw_token = body.refresh_token
    try:
        payload = _decode_token(raw_token)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    token_hash = _hash_token(raw_token)
    stored = db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked == False,
        )
    ).scalar()

    if not stored:
        raise HTTPException(status_code=401, detail="Token not found or revoked")

    if stored.expires_at and stored.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Refresh token expired")

    stored.revoked = True
    db.commit()

    user_id = payload["sub"]
    user = db.execute(select(UserProfile).where(UserProfile.id == user_id)).scalar()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    new_access = create_access_token(user_id)
    new_refresh = create_refresh_token(user_id)
    _store_refresh_token(db, user_id, new_refresh)

    return AuthResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        user=_user_response(user, db),
    )


@router.post("/verify-email", response_model=AuthResponse)
async def verify_email(
    body: VerifyEmailRequest = Body(...),
    db: Session = Depends(get_db),
):
    try:
        user_id = decode_verification_token(body.token)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")

    user = db.execute(select(UserProfile).where(UserProfile.id == user_id)).scalar()
    if not user:
        raise HTTPException(status_code=400, detail="User not found")

    user.email_confirmed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    access_token = create_access_token(user_id)
    refresh_token = create_refresh_token(user_id)
    _store_refresh_token(db, user_id, refresh_token)

    logger.info("Email verified uid=%s email=%s", user_id, user.email)
    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=_user_response(user, db),
    )


@router.post("/resend-verification", response_model=MessageResponse)
@limiter.limit("3/minute")
async def resend_verification(
    request: Request,
    body: ResendVerificationRequest = Body(...),
    db: Session = Depends(get_db),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    user = db.execute(select(UserProfile).where(UserProfile.email == body.email)).scalar()
    if not user:
        return MessageResponse(message="If this email is registered, a verification email has been sent.")
    if user.email_confirmed_at:
        return MessageResponse(message="Email already verified. You can log in.")

    verification_token = create_verification_token(str(user.id))
    background_tasks.add_task(send_verification_email, body.email, user.full_name or "", verification_token)

    return MessageResponse(message="Verification email sent. Please check your inbox.")


@router.post("/logout", response_model=MessageResponse)
async def logout(
    body: RefreshRequest = Body(...),
    db: Session = Depends(get_db),
):
    token_hash = _hash_token(body.refresh_token)
    db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).update({"revoked": True})
    db.commit()
    logger.info("Logout success")
    return MessageResponse(message="Logged out successfully")


@router.post("/forgot-password", response_model=MessageResponse)
@limiter.limit("3/minute")
async def forgot_password(
    request: Request,
    body: ForgotPasswordRequest = Body(...),
    db: Session = Depends(get_db),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    user = db.execute(select(UserProfile).where(UserProfile.email == body.email)).scalar()
    if not user:
        return MessageResponse(message="If this email is registered, a reset link has been sent.")

    reset_token = create_verification_token(str(user.id))
    background_tasks.add_task(send_password_reset_email, body.email, user.full_name or "", reset_token)

    return MessageResponse(message="Password reset email sent. Please check your inbox.")


@router.post("/reset-password", response_model=MessageResponse)
@limiter.limit("5/minute")
async def reset_password(
    request: Request,
    body: ResetPasswordRequest = Body(...),
    db: Session = Depends(get_db),
):
    try:
        user_id = decode_verification_token(body.token)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    hashed = hash_password(body.password)

    db.query(UserProfile).filter(UserProfile.id == user_id).update({"hashed_password": hashed})
    _revoke_user_tokens(db, user_id)

    logger.info("Password reset uid=%s", user_id)
    return MessageResponse(message="Password reset successfully. You can now sign in with your new password.")


# ---- Google OAuth ----

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

from app.core.config import get_settings
from fastapi.responses import RedirectResponse
import requests
from urllib.parse import urlencode


@router.get("/google/login")
async def google_login(request: Request):
    settings = get_settings()
    host = request.headers.get("host", "")
    callback_url = f"https://{host}/api/v1/auth/google/callback"

    state = uuid4().hex

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": callback_url,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    google_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    response = RedirectResponse(google_url)
    response.set_cookie("oauth_state", state, httponly=True, secure=True, samesite="lax", max_age=300)
    return response


@router.get("/google/callback")
async def google_callback(
    code: str,
    state: str | None = None,
    request: Request = None,
    db: Session = Depends(get_db),
):
    settings = get_settings()

    if not state:
        return RedirectResponse(f"{settings.frontend_url}/auth/callback?error=missing_state")

    expected = request.cookies.get("oauth_state") if request else None
    if expected != state:
        return RedirectResponse(f"{settings.frontend_url}/auth/callback?error=invalid_state")

    host = request.headers.get("host", "") if request else ""
    callback_url = f"https://{host}/api/v1/auth/google/callback"

    token_res = requests.post(GOOGLE_TOKEN_URL, data={
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": callback_url,
    })

    if token_res.status_code != 200:
        return RedirectResponse(f"{settings.frontend_url}/auth/callback?error=google_token_failed")

    tokens = token_res.json()
    access_token_google = tokens.get("access_token")

    user_res = requests.get(GOOGLE_USERINFO_URL, headers={
        "Authorization": f"Bearer {access_token_google}",
    })

    if user_res.status_code != 200:
        return RedirectResponse(f"{settings.frontend_url}/auth/callback?error=google_userinfo_failed")

    google_user = user_res.json()
    email = google_user.get("email")
    full_name = google_user.get("name", "")
    google_id = google_user.get("id", "")

    if not email:
        return RedirectResponse(f"{settings.frontend_url}/auth/callback?error=no_email")

    user = db.execute(select(UserProfile).where(UserProfile.email == email)).scalar()

    if user:
        if not user.google_id:
            user.google_id = google_id
            db.commit()
        user_id = str(user.id)
    else:
        user_id = str(uuid4())
        db.add(UserProfile(
            id=user_id,
            email=email,
            full_name=full_name,
            google_id=google_id,
            email_confirmed_at=datetime.now(timezone.utc),
            subscription_tier="free",
        ))
        db.commit()

    access_token = create_access_token(user_id)
    refresh_token = create_refresh_token(user_id)
    _store_refresh_token(db, user_id, refresh_token)

    redirect = RedirectResponse(
        f"{settings.frontend_url}/auth/callback"
        f"?access_token={access_token}"
        f"&refresh_token={refresh_token}",
        status_code=302,
    )
    _set_refresh_cookie(redirect, refresh_token)
    redirect.delete_cookie("oauth_state")
    return redirect
