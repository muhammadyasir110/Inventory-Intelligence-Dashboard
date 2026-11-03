"""
api/account.py
---------------
Account lifecycle (2.6): invitations, self-service password reset and e-mail
verification. All three use single-use tokens from ``account_tokens``: the
token is shown once (in the e-mail link), only its SHA-256 digest is stored,
it expires, and it is consumed atomically so it cannot be used twice.

No endpoint reveals whether an e-mail address or username exists: password
reset always answers with the same message, and token errors are generic.
E-mail delivery status comes from core/email.py and is never overstated.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, update
from sqlalchemy.orm import Session

from api.auth import (get_current_user, require_role, create_session, revoke_user_sessions,
                      _strength_or_422, _USERNAME_RE)
from core import email as mail
from core.audit import audit
from core.permissions import ROLE_LABELS
from core.plans import check_limit
from core.ratelimit import limit_login
from core.security import hash_password, new_session_token, token_digest
from core.settings import ASSIGNABLE_ROLES
from database.db import get_db
from models.models import AccountToken, Organization, User

router = APIRouter(prefix="/api/auth", tags=["account"])

INVITE_DAYS = 7
RESET_MINUTES = 60
VERIFY_HOURS = 48
GENERIC_RESET_REPLY = ("If an active account uses this e-mail address, a reset link has been sent. "
                       "If you do not receive it, ask your organisation administrator.")
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,63}$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _norm_email(v: str) -> str:
    v = (v or "").strip()
    if not _EMAIL_RE.match(v):
        raise ValueError("not a valid e-mail address")
    return v


def issue_token(db: Session, *, purpose: str, org_id: int, ttl: timedelta, user_id=None,
                email=None, role=None, created_by=None) -> tuple[str, AccountToken]:
    token = new_session_token()
    now = _utcnow()
    row = AccountToken(purpose=purpose, token_hash=token_digest(token), org_id=org_id, user_id=user_id,
                       email=email, role=role, created_by=created_by, created_at=now,
                       expires_at=now + ttl)
    db.add(row)
    db.flush()
    return token, row


def _find_token(db: Session, token: str, purpose: str) -> Optional[AccountToken]:
    if not token or len(token) > 256:
        return None
    row = db.query(AccountToken).filter(AccountToken.token_hash == token_digest(token),
                                        AccountToken.purpose == purpose).first()
    if not row or row.used_at or row.revoked_at or row.expires_at <= _utcnow():
        return None
    return row


def _consume(db: Session, row: AccountToken) -> bool:
    """Atomically mark a token used; False if another request used it first."""
    res = db.execute(update(AccountToken).where(AccountToken.id == row.id, AccountToken.used_at.is_(None),
                                                AccountToken.revoked_at.is_(None))
                     .values(used_at=_utcnow()))
    return res.rowcount == 1


def _invalid_link() -> HTTPException:
    return HTTPException(status_code=400, detail="This link is invalid, expired or has already been used.")


# ------------------------------------------------------------------ invitations

class InviteRequest(BaseModel):
    email: str = Field(..., max_length=255)
    role: str

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        return _norm_email(v)

    @field_validator("role")
    @classmethod
    def _role(cls, v):
        if v not in ASSIGNABLE_ROLES:
            raise ValueError(f"role must be one of {', '.join(ASSIGNABLE_ROLES)}")
        return v


class AcceptInviteRequest(BaseModel):
    token: str = Field(..., max_length=256)
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=1, max_length=256)
    full_name: Optional[str] = Field(None, max_length=255)

    @field_validator("username")
    @classmethod
    def _uname(cls, v):
        if not _USERNAME_RE.match(v):
            raise ValueError("username may contain letters, digits and . _ @ - (3-100 characters)")
        return v


def _invite_public(r: AccountToken) -> dict:
    now = _utcnow()
    state = ("accepted" if r.used_at else "revoked" if r.revoked_at
             else "expired" if r.expires_at <= now else "pending")
    return {"id": r.id, "email": r.email, "role": r.role, "invited_by": r.created_by,
            "created_at": r.created_at.isoformat(), "expires_at": r.expires_at.isoformat(), "state": state}


@router.post("/invitations")
def create_invitation(req: InviteRequest, admin: User = Depends(require_role(["admin"])),
                      db: Session = Depends(get_db)):
    org = admin.organization
    check_limit(db, org, "users")                       # fail early; re-checked at acceptance
    if db.query(User).filter(func.lower(User.email) == req.email.lower()).first():
        raise HTTPException(status_code=409, detail="A user with this e-mail address already exists.")
    # one pending invitation per address: revoke older ones
    db.query(AccountToken).filter(AccountToken.org_id == org.id, AccountToken.purpose == "invite",
                                  func.lower(AccountToken.email) == req.email.lower(),
                                  AccountToken.used_at.is_(None), AccountToken.revoked_at.is_(None)
                                  ).update({"revoked_at": _utcnow()}, synchronize_session=False)
    token, row = issue_token(db, purpose="invite", org_id=org.id, ttl=timedelta(days=INVITE_DAYS),
                             email=req.email, role=req.role, created_by=admin.username)
    link = f"{mail.public_base_url()}/#/invite?token={token}"
    subject, body = mail.invitation(org.name, admin.full_name or admin.username,
                                    ROLE_LABELS.get(req.role, req.role), link, INVITE_DAYS)
    delivery = mail.send(db, to=req.email, template="invite", subject=subject, body=body, org_id=org.id)
    audit(db, org.id, "INVITE_USER", "account_token", row.id, user=admin,
          details={"role": req.role, "email_status": delivery})
    db.commit()
    out = {**_invite_public(row), "email_status": delivery}
    if delivery != "sent":
        # nothing was delivered: the inviting administrator shares the link by hand
        out["accept_url"] = link
        out["note"] = "No e-mail was sent. Share this single-use link with the invitee yourself."
    return out


@router.get("/invitations")
def list_invitations(admin: User = Depends(require_role(["admin"])), db: Session = Depends(get_db)):
    rows = db.query(AccountToken).filter(AccountToken.org_id == admin.org_id,
                                         AccountToken.purpose == "invite"
                                         ).order_by(AccountToken.id.desc()).limit(200).all()
    return [_invite_public(r) for r in rows]


@router.delete("/invitations/{invite_id}")
def revoke_invitation(invite_id: int, admin: User = Depends(require_role(["admin"])),
                      db: Session = Depends(get_db)):
    r = db.query(AccountToken).filter(AccountToken.id == invite_id, AccountToken.org_id == admin.org_id,
                                      AccountToken.purpose == "invite").first()
    if not r:
        raise HTTPException(status_code=404, detail="Invitation not found.")
    if r.used_at:
        raise HTTPException(status_code=409, detail="This invitation has already been accepted.")
    r.revoked_at = r.revoked_at or _utcnow()
    audit(db, admin.org_id, "REVOKE_INVITATION", "account_token", r.id, user=admin)
    db.commit()
    return _invite_public(r)


@router.get("/invitations/lookup")
def lookup_invitation(token: str, request: Request, db: Session = Depends(get_db)):
    limit_login(request)
    r = _find_token(db, token, "invite")
    if not r:
        raise _invalid_link()
    org = db.get(Organization, r.org_id)
    if not org or org.is_active is False:
        raise _invalid_link()
    return {"organization": org.name, "email": r.email, "role": r.role,
            "role_label": ROLE_LABELS.get(r.role, r.role), "expires_at": r.expires_at.isoformat()}


@router.post("/invitations/accept")
def accept_invitation(req: AcceptInviteRequest, request: Request, db: Session = Depends(get_db)):
    limit_login(request)
    r = _find_token(db, req.token, "invite")
    if not r:
        raise _invalid_link()
    org = db.get(Organization, r.org_id)
    if not org or org.is_active is False:
        raise _invalid_link()
    from core.subscriptions import access_state
    if access_state(org)["state"] == "read_only":
        raise HTTPException(status_code=402, detail="This organisation cannot add members at the moment.")
    _strength_or_422(req.password)
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(status_code=400, detail="Username already in use.")
    if r.email and db.query(User).filter(func.lower(User.email) == r.email.lower()).first():
        raise HTTPException(status_code=409, detail="A user with this e-mail address already exists.")
    check_limit(db, org, "users")
    if not _consume(db, r):
        raise _invalid_link()
    now = _utcnow()
    sent = _invite_was_emailed(db, r)
    u = User(org_id=org.id, username=req.username, email=r.email, password_hash=hash_password(req.password),
             role=r.role, full_name=req.full_name or req.username, is_active=True, is_platform_admin=False,
             failed_login_count=0, password_changed_at=now,
             email_verified_at=now if sent else None)
    db.add(u)
    db.flush()
    audit(db, org.id, "ACCEPT_INVITATION", "user", u.id, user=u,
          details={"role": u.role, "invited_by": r.created_by})
    token, expires = create_session(db, u)
    db.commit()
    return {"token": token, "username": u.username, "role": u.role, "org_id": org.id,
            "org_name": org.name, "expires_at": expires.isoformat() + "Z",
            "email_verified": bool(u.email_verified_at)}


def _invite_was_emailed(db: Session, r: AccountToken) -> bool:
    """The e-mail address counts as verified only if the invitation actually went out by e-mail
    (opening the link then proves control of the mailbox)."""
    from models.models import EmailOutbox
    return db.query(EmailOutbox).filter(EmailOutbox.org_id == r.org_id, EmailOutbox.template == "invite",
                                        EmailOutbox.to_address == r.email, EmailOutbox.status == "sent",
                                        EmailOutbox.created_at >= r.created_at).first() is not None


# --------------------------------------------------------------- password reset

class ResetRequest(BaseModel):
    email: str = Field(..., max_length=255)


class ResetConfirm(BaseModel):
    token: str = Field(..., max_length=256)
    new_password: str = Field(..., min_length=1, max_length=256)


@router.post("/password-reset/request")
def request_password_reset(req: ResetRequest, request: Request, db: Session = Depends(get_db)):
    limit_login(request)
    addr = (req.email or "").strip()
    if _EMAIL_RE.match(addr):
        u = db.query(User).filter(func.lower(User.email) == addr.lower(), User.is_active == True  # noqa: E712
                                  ).first()
        org = u.organization if u else None
        if u and org is not None and org.is_active is not False:
            db.query(AccountToken).filter(AccountToken.user_id == u.id, AccountToken.purpose == "password_reset",
                                          AccountToken.used_at.is_(None), AccountToken.revoked_at.is_(None)
                                          ).update({"revoked_at": _utcnow()}, synchronize_session=False)
            token, row = issue_token(db, purpose="password_reset", org_id=u.org_id, user_id=u.id,
                                     email=u.email, ttl=timedelta(minutes=RESET_MINUTES))
            link = f"{mail.public_base_url()}/#/reset-password?token={token}"
            subject, body = mail.password_reset(link, RESET_MINUTES)
            status = mail.send(db, to=u.email, template="password_reset", subject=subject, body=body,
                               org_id=u.org_id)
            audit(db, u.org_id, "PASSWORD_RESET_REQUESTED", "user", u.id, details={"email_status": status})
            db.commit()
    return {"message": GENERIC_RESET_REPLY}


@router.post("/password-reset/confirm")
def confirm_password_reset(req: ResetConfirm, request: Request, db: Session = Depends(get_db)):
    limit_login(request)
    r = _find_token(db, req.token, "password_reset")
    u = db.get(User, r.user_id) if r else None
    if not r or not u or not u.is_active:
        raise _invalid_link()
    _strength_or_422(req.new_password)
    if not _consume(db, r):
        raise _invalid_link()
    u.password_hash = hash_password(req.new_password)
    u.password_changed_at = _utcnow()
    u.failed_login_count = 0
    u.locked_until = None
    if u.email and r.email and u.email.lower() == r.email.lower():
        u.email_verified_at = u.email_verified_at or _utcnow()    # the link reached this mailbox
    n = revoke_user_sessions(db, u.id)
    audit(db, u.org_id, "PASSWORD_RESET_COMPLETED", "user", u.id, user=u, details={"sessions_revoked": n})
    db.commit()
    return {"message": "Password changed. Please sign in with your new password.", "sessions_revoked": n}


# ------------------------------------------------------------ e-mail verification

class VerifyRequest(BaseModel):
    token: str = Field(..., max_length=256)


def send_verification(db: Session, u: User) -> Optional[str]:
    """Issue a verification link for u.email (at most one every 10 minutes)."""
    if not u.email or u.email_verified_at:
        return None
    recent = db.query(AccountToken).filter(AccountToken.user_id == u.id, AccountToken.purpose == "verify_email",
                                           AccountToken.created_at > _utcnow() - timedelta(minutes=10)).first()
    if recent:
        return "throttled"
    token, _ = issue_token(db, purpose="verify_email", org_id=u.org_id, user_id=u.id, email=u.email,
                           ttl=timedelta(hours=VERIFY_HOURS))
    subject, body = mail.verify_email(f"{mail.public_base_url()}/#/verify-email?token={token}", VERIFY_HOURS)
    return mail.send(db, to=u.email, template="verify_email", subject=subject, body=body, org_id=u.org_id)


@router.post("/verify-email")
def verify_email(req: VerifyRequest, request: Request, db: Session = Depends(get_db)):
    limit_login(request)
    r = _find_token(db, req.token, "verify_email")
    u = db.get(User, r.user_id) if r else None
    if not r or not u or (u.email or "").lower() != (r.email or "").lower() or not _consume(db, r):
        raise _invalid_link()
    u.email_verified_at = _utcnow()
    audit(db, u.org_id, "EMAIL_VERIFIED", "user", u.id, user=u)
    db.commit()
    return {"message": "E-mail address confirmed."}


@router.post("/verify-email/resend")
def resend_verification(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not user.email:
        raise HTTPException(status_code=409, detail="Your account has no e-mail address.")
    if user.email_verified_at:
        return {"message": "Your e-mail address is already confirmed.", "email_status": None}
    st = send_verification(db, user)
    db.commit()
    return {"message": "If e-mail delivery is configured, a confirmation link has been sent.",
            "email_status": st}


@router.get("/email-status")
def email_delivery_status(_: User = Depends(require_role(["admin"]))):
    """Whether this deployment actually delivers e-mail (shown to administrators)."""
    return mail.status()
