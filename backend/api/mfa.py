"""
api/mfa.py
-----------
Two-factor login with an authenticator app (TOTP, RFC 6238) - 2.7.

  POST /api/auth/mfa/setup        member   new secret + otpauth URI (not active yet)
  POST /api/auth/mfa/enable       member   confirm with a code -> active; returns 10 recovery codes once
  POST /api/auth/mfa/disable      member   password + code (or recovery code)
  GET  /api/auth/mfa/status       member   enabled?, recovery codes left
  POST /api/auth/mfa/login        public   second login step: mfa_token + code or recovery code
  POST /api/auth/users/{id}/reset-mfa  owner/admin  for a member who lost the device (not the owner;
                                       the operator resets an owner: manage_users.py reset-mfa)

When two-factor login is on, POST /api/auth/login answers a correct password
with {"mfa_required": true, "mfa_token": ...} instead of a session. The
mfa_token is single use, valid 5 minutes, and only its digest is stored.
Wrong codes are throttled per user (5 per 15 minutes).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.auth import (get_current_user, require_role, create_session, revoke_user_sessions,
                      _not_owner_unless_self, _same_org_user, LoginResponse)
from core import totp
from core.audit import audit
from core.ratelimit import limit_login, limiter
from core.security import verify_password
from database.db import get_db
from models.models import AccountToken, User

router = APIRouter(prefix="/api/auth", tags=["two-factor"])
MFA_TOKEN_MIN = 5
FAIL_LIMIT, FAIL_WINDOW_S = 5, 15 * 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CodeRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=20)


class DisableRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=256)
    code: str = Field(..., min_length=6, max_length=20)


class MfaLoginRequest(BaseModel):
    mfa_token: str = Field(..., max_length=256)
    code: str = Field(..., min_length=6, max_length=20)


def is_enabled(u: User) -> bool:
    return bool(u.mfa_enabled_at and u.mfa_secret)


def check_second_factor(db: Session, u: User, code: str) -> str:
    """Accept a TOTP code or an unused recovery code; returns 'totp' or 'recovery'.
    Raises 429 after repeated failures and 400 on a wrong code."""
    key = str(u.id)
    step = totp.verify(u.mfa_secret, code, last_step=u.mfa_last_step)
    if step is not None:
        u.mfa_last_step = step
        return "totp"
    hashes = list(u.mfa_recovery_hashes or [])
    d = totp.digest(code)
    if d in hashes:
        hashes.remove(d)
        u.mfa_recovery_hashes = hashes
        return "recovery"
    try:
        limiter.hit("mfa_fail", key, FAIL_LIMIT, window_s=FAIL_WINDOW_S)
    except HTTPException:
        raise HTTPException(status_code=429, detail="Too many wrong codes. Try again in 15 minutes.",
                            headers={"Retry-After": str(FAIL_WINDOW_S)})
    raise HTTPException(status_code=400, detail="The code is not correct.")


def start_mfa_login(db: Session, u: User) -> dict:
    """Called by /login after a correct password when two-factor login is on."""
    from api.account import issue_token
    db.query(AccountToken).filter(AccountToken.user_id == u.id, AccountToken.purpose == "mfa_login",
                                  AccountToken.used_at.is_(None)).update({"used_at": _utcnow()},
                                                                         synchronize_session=False)
    token, _ = issue_token(db, purpose="mfa_login", org_id=u.org_id, user_id=u.id,
                           ttl=timedelta(minutes=MFA_TOKEN_MIN))
    return {"mfa_required": True, "mfa_token": token, "username": u.username}


@router.get("/mfa/status")
def mfa_status(user: User = Depends(get_current_user)):
    return {"enabled": is_enabled(user),
            "enabled_at": user.mfa_enabled_at.isoformat() if user.mfa_enabled_at else None,
            "recovery_codes_left": len(user.mfa_recovery_hashes or []) if is_enabled(user) else 0,
            "setup_pending": bool(user.mfa_pending_secret)}


@router.post("/mfa/setup")
def mfa_setup(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if is_enabled(user):
        raise HTTPException(status_code=409, detail="Two-factor login is already on. Turn it off first.")
    secret = totp.new_secret()
    user.mfa_pending_secret = secret
    db.commit()
    return {"secret": secret, "otpauth_uri": totp.provisioning_uri(secret, user.username),
            "instructions": "Add this key to an authenticator app (or open the otpauth link on the phone), "
                            "then enter the 6-digit code to switch two-factor login on."}


@router.post("/mfa/enable")
def mfa_enable(req: CodeRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if is_enabled(user):
        raise HTTPException(status_code=409, detail="Two-factor login is already on.")
    if not user.mfa_pending_secret:
        raise HTTPException(status_code=409, detail="Start the setup first.")
    step = totp.verify(user.mfa_pending_secret, req.code)
    if step is None:
        raise HTTPException(status_code=400, detail="The code is not correct. Check the phone's clock and try again.")
    codes = totp.new_recovery_codes()
    user.mfa_secret, user.mfa_pending_secret = user.mfa_pending_secret, None
    user.mfa_enabled_at, user.mfa_last_step = _utcnow(), step
    user.mfa_recovery_hashes = [totp.digest(c) for c in codes]
    audit(db, user.org_id, "MFA_ENABLED", "user", user.id, user=user)
    db.commit()
    return {"enabled": True, "recovery_codes": codes,
            "note": "Save these recovery codes now. Each works once if the phone is lost; they are not shown again."}


@router.post("/mfa/disable")
def mfa_disable(req: DisableRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not is_enabled(user):
        raise HTTPException(status_code=409, detail="Two-factor login is not on.")
    if not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=400, detail="Password is incorrect.")
    check_second_factor(db, user, req.code)
    user.mfa_secret = user.mfa_pending_secret = None
    user.mfa_enabled_at = user.mfa_last_step = None
    user.mfa_recovery_hashes = None
    audit(db, user.org_id, "MFA_DISABLED", "user", user.id, user=user)
    db.commit()
    return {"enabled": False}


@router.post("/mfa/login", response_model=LoginResponse)
def mfa_login(req: MfaLoginRequest, request: Request, db: Session = Depends(get_db)):
    from api.account import _find_token, _consume, _invalid_link
    limit_login(request)
    t = _find_token(db, req.mfa_token, "mfa_login")
    u = db.get(User, t.user_id) if t else None
    if not t or not u or not u.is_active or not is_enabled(u):
        raise HTTPException(status_code=401, detail="The sign-in has expired. Please sign in again.")
    org = u.organization
    if org is None or org.is_active is False:
        raise HTTPException(status_code=401, detail="The sign-in has expired. Please sign in again.")
    method = check_second_factor(db, u, req.code)
    if not _consume(db, t):
        raise _invalid_link()
    token, expires = create_session(db, u)
    u.last_login = datetime.now(timezone.utc)
    audit(db, u.org_id, "LOGIN_MFA", "user", u.id, user=u, details={"method": method,
          "recovery_codes_left": len(u.mfa_recovery_hashes or [])})
    db.commit()
    return LoginResponse(token=token, username=u.username, role=u.role, org_id=u.org_id, org_name=org.name,
                         full_name=u.full_name, expires_at=expires.isoformat() + "Z",
                         is_platform_admin=bool(u.is_platform_admin))


@router.post("/users/{user_id}/reset-mfa")
def admin_reset_mfa(user_id: int, admin: User = Depends(require_role(["admin"])), db: Session = Depends(get_db)):
    u = _same_org_user(db, user_id, admin)
    _not_owner_unless_self(u, admin)
    if u.id == admin.id:
        raise HTTPException(status_code=409, detail="Use 'turn off two-factor login' for your own account.")
    u.mfa_secret = u.mfa_pending_secret = None
    u.mfa_enabled_at = u.mfa_last_step = None
    u.mfa_recovery_hashes = None
    n = revoke_user_sessions(db, u.id)
    audit(db, admin.org_id, "MFA_RESET_BY_ADMIN", "user", u.id, user=admin,
          details={"target": u.username, "sessions_revoked": n})
    db.commit()
    return {"message": f"Two-factor login of {u.username} was switched off; they were signed out.",
            "sessions_revoked": n}

