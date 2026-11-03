"""
api/auth.py
------------
Authentication, sessions, organisation user management and role-based access.

* Passwords: PBKDF2-HMAC-SHA256, per-user salt (core/security.py); legacy
  v2.1 hashes are upgraded on the next successful login.
* Sessions: random bearer tokens, stored server-side as SHA-256 digests in
  `user_sessions` with an expiry (IIP_SESSION_TTL_MIN). A missing, malformed,
  unknown, revoked or expired token - or a deactivated user or organisation -
  is ALWAYS rejected with 401; there is no fallback user.
* Brute force: per-IP request throttling and per-account lockout after
  IIP_LOGIN_MAX_FAILURES failed attempts (stored in the database). Unknown
  usernames are throttled the same way, so responses do not reveal whether an
  account exists.
* Tenancy: every authenticated request carries the user's org_id; routers
  scope their queries by it. A client-supplied organisation id is never used
  for authorisation.
* Roles: admin (organisation administrator), inventory_manager,
  procurement_manager, warehouse_manager, executive (read-only). The separate
  `is_platform_admin` flag grants the platform console (api/platform.py),
  which exposes organisation metadata only.
* Password reset: an organisation administrator sets a temporary password
  (all of that user's sessions are revoked). Self-service e-mail reset needs
  an SMTP service and is NOT implemented in this release.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from core.audit import audit
from core.plans import DEFAULT_NEW_ORG_PLAN, check_limit
from core.ratelimit import limit_login, limiter
from core.settings import settings, ASSIGNABLE_ROLES, ADMIN_ROLES
from core.security import (
    hash_password, verify_password, needs_rehash, new_session_token, token_digest,
    validate_password_strength,
)
from database.db import get_db
from models.models import User, Organization, OrgConfig, UserSession

router = APIRouter(prefix="/api/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)

_USERNAME_RE = re.compile(r"^[A-Za-z0-9._@-]{3,100}$")
_GENERIC_LOGIN_ERROR = "Invalid username or password."


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=256)


class LoginResponse(BaseModel):
    """A session, or (two-factor login on) mfa_required + mfa_token for POST /api/auth/mfa/login."""
    token: Optional[str] = None
    username: str
    role: Optional[str] = None
    org_id: Optional[int] = None
    org_name: Optional[str] = None
    full_name: Optional[str] = None
    expires_at: Optional[str] = None
    demo_mode: bool = False
    is_platform_admin: bool = False
    mfa_required: bool = False
    mfa_token: Optional[str] = None


class RegisterOrgRequest(BaseModel):
    org_name: str = Field(..., min_length=2, max_length=200)
    industry: Optional[str] = Field("FMCG / Distribution", max_length=100)
    admin_username: str = Field(..., min_length=3, max_length=100)
    admin_email: Optional[str] = Field(None, max_length=255)
    admin_password: str = Field(..., min_length=1, max_length=256)
    admin_fullname: Optional[str] = Field(None, max_length=255)

    @field_validator("admin_username")
    @classmethod
    def _uname(cls, v: str) -> str:
        if not _USERNAME_RE.match(v):
            raise ValueError("username may contain letters, digits and . _ @ - (3-100 characters)")
        return v


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=1, max_length=256)
    role: str
    email: Optional[str] = Field(None, max_length=255)
    full_name: Optional[str] = Field(None, max_length=255)

    @field_validator("role")
    @classmethod
    def _role_allowed(cls, v: str) -> str:
        if v not in ASSIGNABLE_ROLES:
            raise ValueError(f"role must be one of {', '.join(ASSIGNABLE_ROLES)}")
        return v

    @field_validator("username")
    @classmethod
    def _uname(cls, v: str) -> str:
        if not _USERNAME_RE.match(v):
            raise ValueError("username may contain letters, digits and . _ @ - (3-100 characters)")
        return v


class UserUpdateRequest(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None
    full_name: Optional[str] = Field(None, max_length=255)
    email: Optional[str] = Field(None, max_length=255)

    @field_validator("role")
    @classmethod
    def _role_allowed(cls, v):
        if v is not None and v not in ASSIGNABLE_ROLES:
            raise ValueError(f"role must be one of {', '.join(ASSIGNABLE_ROLES)}")
        return v


class PasswordResetRequest(BaseModel):
    new_password: str = Field(..., min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=1, max_length=256)


def _unauthorized(detail: str = "Authentication required.") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail,
                         headers={"WWW-Authenticate": "Bearer"})


def _strength_or_422(pw: str) -> None:
    try:
        validate_password_strength(pw)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


def seed_admin_user(db: Session):
    """Development/test only: ensures the demo organisation and its `admin`
    user exist (password IIP_ADMIN_PASSWORD or the documented demo password).
    Never runs in staging/production and never changes an existing password."""
    if settings.is_production:
        return
    from database.db import seed_demo_organization
    seed_demo_organization(db)
    if not db.query(User).filter(User.username == "admin").first():
        db.add(User(org_id=1, username="admin", email="admin@demo.local",
                    password_hash=hash_password(settings.admin_seed_password()),
                    role="owner", full_name="Demo Administrator", is_active=True,
                    email_verified_at=_utcnow(),     # demo address, nothing to confirm
                    is_platform_admin=False, failed_login_count=0))
        db.commit()


def create_session(db: Session, user: User) -> tuple[str, datetime]:
    token = new_session_token()
    now = _utcnow()
    # prune this user's sessions that ended more than a week ago (keeps the table bounded)
    db.query(UserSession).filter(UserSession.user_id == user.id,
                                 UserSession.expires_at < now - timedelta(days=7)
                                 ).delete(synchronize_session=False)
    expires = now + timedelta(minutes=settings.session_ttl_min)
    db.add(UserSession(token_hash=token_digest(token), user_id=user.id, org_id=user.org_id,
                       created_at=now, expires_at=expires, revoked=False))
    return token, expires


def revoke_user_sessions(db: Session, user_id: int, except_digest: Optional[str] = None) -> int:
    q = db.query(UserSession).filter(UserSession.user_id == user_id, UserSession.revoked == False)  # noqa: E712
    if except_digest:
        q = q.filter(UserSession.token_hash != except_digest)
    return q.update({"revoked": True}, synchronize_session=False)


# Writes still allowed while an organisation is read-only (subscription ended,
# payment overdue, deletion pending): signing out, own password, cancelling a
# deletion request, billing actions, and e-mail verification.
READ_ONLY_WRITE_ALLOWLIST = (
    "/api/auth/logout", "/api/auth/logout-all", "/api/auth/change-password",
    "/api/auth/verify-email", "/api/org/deletion-request", "/api/billing/",
)


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """Resolves the bearer token to an active user of an active organisation and
    enforces the organisation's access state (core/subscriptions.access_state):
    a read-only organisation gets HTTP 402 on every write outside the allowlist."""
    if not credentials or not credentials.credentials:
        raise _unauthorized()
    token = credentials.credentials
    if len(token) > 256:
        raise _unauthorized("Session invalid or expired.")
    sess = db.query(UserSession).filter(UserSession.token_hash == token_digest(token)).first()
    if not sess or sess.revoked or sess.expires_at <= _utcnow():
        raise _unauthorized("Session invalid or expired.")
    user = db.query(User).filter(User.id == sess.user_id).first()
    if not user or not user.is_active or user.org_id != sess.org_id:
        raise _unauthorized("Session invalid or expired.")
    org = user.organization
    if org is None or org.is_active is False:
        raise _unauthorized("Session invalid or expired.")
    if request.method not in ("GET", "HEAD", "OPTIONS") and \
            not request.url.path.startswith(READ_ONLY_WRITE_ALLOWLIST):
        from core.subscriptions import access_state
        st = access_state(org)
        if st["state"] == "read_only":
            raise HTTPException(status_code=402, detail={
                "code": "organization_read_only", "reason": st["reason"], "message": st["message"]})
    return user


def is_admin(user: User) -> bool:
    """Organisation owner or administrator."""
    return user.role in ADMIN_ROLES


def require_role(allowed_roles: List[str]):
    """Role-Based Access Control dependency factory (owner and administrator are always allowed).
    The full role model is written down in core/permissions.py."""
    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles and current_user.role not in ADMIN_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required roles: {', '.join(allowed_roles)} (User role: {current_user.role})"
            )
        return current_user
    return role_checker


def require_owner(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "owner":
        raise HTTPException(status_code=403, detail="Only the organisation owner can do this.")
    return current_user


def require_platform_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_platform_admin:
        raise HTTPException(status_code=403, detail="Platform administrator access required.")
    return current_user


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    limit_login(request)
    uname = req.username.strip()
    now = _utcnow()
    user = db.query(User).filter(User.username == uname).first()

    if user and user.locked_until and user.locked_until > now:
        mins = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
        raise HTTPException(status_code=429, headers={"Retry-After": str(mins * 60)},
                            detail=f"Too many failed attempts. Try again in about {mins} minute(s).")

    if not user or not verify_password(req.password, user.password_hash):
        # Same throttle for unknown usernames => no account enumeration.
        try:
            limiter.hit("login_fail", uname.lower(), settings.login_max_failures,
                        window_s=settings.login_lock_min * 60)
        except HTTPException:
            raise HTTPException(status_code=429,
                                headers={"Retry-After": str(settings.login_lock_min * 60)},
                                detail=f"Too many failed attempts. Try again in about "
                                       f"{settings.login_lock_min} minute(s).")
        if user:
            user.failed_login_count = (user.failed_login_count or 0) + 1
            if user.failed_login_count >= settings.login_max_failures:
                user.locked_until = now + timedelta(minutes=settings.login_lock_min)
                user.failed_login_count = 0
                audit(db, user.org_id, "ACCOUNT_LOCKED", "user", user.id,
                      details={"reason": "failed_logins", "minutes": settings.login_lock_min})
            db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_GENERIC_LOGIN_ERROR)

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Account has been deactivated. Contact your organization administrator.")
    org = user.organization
    if org is None or org.is_active is False:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="This organisation is not active. Contact support.")
    if (_require_verification() and user.email and not user.email_verified_at):
        # password was correct: tell the user and (re)send the confirmation link
        from api.account import send_verification
        send_verification(db, user)
        db.commit()
        raise HTTPException(status_code=403, detail={
            "code": "email_unverified",
            "message": "Please confirm your e-mail address first. If e-mail delivery is configured, "
                       "a confirmation link has been sent; otherwise ask your administrator."})
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(req.password)

    user.failed_login_count = 0
    user.locked_until = None
    if user.mfa_enabled_at and user.mfa_secret:
        from api.mfa import start_mfa_login
        step = start_mfa_login(db, user)
        db.commit()
        return LoginResponse(**step)
    token, expires = create_session(db, user)
    user.last_login = datetime.now(timezone.utc)
    db.commit()
    return LoginResponse(
        token=token, username=user.username, role=user.role, org_id=user.org_id,
        org_name=org.name, full_name=user.full_name,
        expires_at=expires.isoformat() + "Z",
        demo_mode=settings.demo_mode and user.username == "admin",
        is_platform_admin=bool(user.is_platform_admin),
    )


def _require_verification() -> bool:
    import os
    return (os.getenv("IIP_REQUIRE_EMAIL_VERIFICATION") or "").strip().lower() in ("1", "true", "yes")


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s or "org")[:80]


def unique_slug(db: Session, name: str) -> str:
    base = slugify(name)
    slug, n = base, 1
    while db.query(Organization).filter(Organization.slug == slug).first():
        n += 1
        slug = f"{base}-{n}"
    return slug


def create_organization(db: Session, *, org_name: str, industry: Optional[str], plan: str,
                        admin_username: str, admin_password: str, admin_email: Optional[str] = None,
                        admin_fullname: Optional[str] = None, platform_admin: bool = False,
                        email_verified: bool = False, source: str = "registration"):
    """Create an organisation, its default configuration and its first
    administrator in the caller's transaction (no commit)."""
    if db.query(Organization).filter(Organization.name == org_name).first():
        raise HTTPException(status_code=400, detail="Organization name already exists.")
    if db.query(User).filter(User.username == admin_username).first():
        raise HTTPException(status_code=400, detail="Username already in use.")
    org = Organization(name=org_name, slug=unique_slug(db, org_name), industry=industry,
                       subscription_tier=plan, subscription_status="unbilled",
                       currency="PKR", timezone="Asia/Karachi", fiscal_year_start_month=7,
                       is_active=True)
    db.add(org)
    db.flush()
    db.add(OrgConfig(org_id=org.id, service_level_target=0.95, annual_holding_rate=0.20,
                     order_setup_cost=50.0, shortage_cost_multiplier=1.5,
                     lead_time_cv_default=0.15, demand_cv_default=0.25,
                     annual_working_capital_budget=1000000.0, alert_expiry_threshold_days=90,
                     review_period_days=7))
    admin = User(org_id=org.id, username=admin_username, email=admin_email,
                 password_hash=hash_password(admin_password), role="owner",
                 full_name=admin_fullname or admin_username, is_active=True,
                 is_platform_admin=platform_admin, failed_login_count=0,
                 password_changed_at=_utcnow(),
                 email_verified_at=_utcnow() if (email_verified and admin_email) else None)
    db.add(admin)
    db.flush()
    from core.subscriptions import record_change
    record_change(db, org, source=source, actor=admin_username, from_plan=None, from_status=None,
                  note="organisation created")
    audit(db, org.id, "REGISTER_ORGANIZATION", "organization", org.id, user=admin,
          details={"org_name": org.name, "admin_user": admin.username, "plan": plan})
    return org, admin


@router.post("/register-org")
def register_organization(req: RegisterOrgRequest, request: Request, db: Session = Depends(get_db)):
    if not settings.allow_registration:
        raise HTTPException(status_code=403, detail="Self-service organization registration is disabled (IIP_ALLOW_REGISTRATION).")
    limit_login(request)
    _strength_or_422(req.admin_password)
    org, admin = create_organization(
        db, org_name=req.org_name.strip(), industry=req.industry, plan=DEFAULT_NEW_ORG_PLAN,
        admin_username=req.admin_username, admin_password=req.admin_password,
        admin_email=req.admin_email, admin_fullname=req.admin_fullname)
    token, _expires = create_session(db, admin)
    from api.account import send_verification
    email_status = send_verification(db, admin) if admin.email else None
    db.commit()
    return {
        "message": "Organization registered successfully",
        "email_verification": email_status,
        "token": token,
        "org_id": org.id,
        "org_name": org.name,
        "username": admin.username,
        "role": admin.role,
        "plan": org.subscription_tier,
    }


@router.get("/me")
def get_me(user: User = Depends(get_current_user)):
    org = user.organization
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "org_id": user.org_id,
        "org_name": org.name if org else "Organization",
        "currency": (org.currency if org and org.currency else "PKR"),
        "timezone": (org.timezone if org and org.timezone else "Asia/Karachi"),
        "plan": (org.subscription_tier if org else None),
        "is_platform_admin": bool(user.is_platform_admin),
        "demo_mode": settings.demo_mode and user.username == "admin",
        "email_verified": bool(user.email_verified_at),
        "mfa_enabled": bool(user.mfa_enabled_at and user.mfa_secret),
        "access": _access(org),
        "status": "authenticated"
    }


def _access(org) -> dict:
    from core.subscriptions import access_state
    return access_state(org) if org is not None else {"state": "full", "reason": None}


def _user_public(u: User) -> dict:
    return {"id": u.id, "username": u.username, "email": u.email, "full_name": u.full_name,
            "role": u.role, "is_active": u.is_active, "created_at": u.created_at,
            "mfa_enabled": bool(u.mfa_enabled_at and u.mfa_secret),
            "last_login": u.last_login}


@router.post("/users")
def create_tenant_user(
    req: UserCreateRequest,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db)
):
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(status_code=400, detail="Username already exists.")
    _strength_or_422(req.password)
    check_limit(db, current_user.organization, "users")

    new_user = User(
        org_id=current_user.org_id,
        username=req.username,
        email=req.email,
        password_hash=hash_password(req.password),
        role=req.role,
        full_name=req.full_name or req.username,
        is_active=True,
        is_platform_admin=False,
        failed_login_count=0,
        password_changed_at=_utcnow(),
    )
    db.add(new_user)
    db.flush()
    audit(db, current_user.org_id, "CREATE_USER", "user", new_user.id, user=current_user,
          details={"created_user": req.username, "role": req.role})
    db.commit()
    return {"message": f"User {new_user.username} created successfully with role {new_user.role}",
            "id": new_user.id}


@router.get("/users")
def list_tenant_users(
    current_user: User = Depends(require_role(["admin", "executive"])),
    db: Session = Depends(get_db)
):
    users = db.query(User).filter(User.org_id == current_user.org_id).order_by(User.id).all()
    return [_user_public(u) for u in users]


def _same_org_user(db: Session, user_id: int, admin: User) -> User:
    u = db.query(User).filter(User.id == user_id, User.org_id == admin.org_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found.")
    return u


def _not_owner_unless_self(target: User, actor: User) -> None:
    """Administrators cannot modify the organisation owner's account."""
    if target.role == "owner" and actor.id != target.id:
        raise HTTPException(status_code=403, detail="Only the owner can change the owner's account.")


def _active_admin_count(db: Session, org_id: int) -> int:
    return db.query(User).filter(User.org_id == org_id, User.role.in_(ADMIN_ROLES),
                                 User.is_active == True).count()  # noqa: E712


@router.patch("/users/{user_id}")
def update_tenant_user(
    user_id: int,
    req: UserUpdateRequest,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db)
):
    u = _same_org_user(db, user_id, current_user)
    _not_owner_unless_self(u, current_user)
    changes = req.model_dump(exclude_unset=True)
    if u.role == "owner" and ("role" in changes or changes.get("is_active") is False):
        raise HTTPException(status_code=409, detail="The owner's role and status change only by ownership transfer.")
    demoting = "role" in changes and changes["role"] != "admin" and u.role == "admin"
    deactivating = changes.get("is_active") is False and u.is_active
    if (demoting or deactivating) and u.role == "admin" and _active_admin_count(db, u.org_id) <= 1:
        raise HTTPException(status_code=409, detail="An organisation must keep at least one active administrator.")
    if changes.get("is_active") is True and not u.is_active:
        check_limit(db, current_user.organization, "users")
    for k, v in changes.items():
        setattr(u, k, v)
    revoked = 0
    if deactivating or demoting:
        revoked = revoke_user_sessions(db, u.id)
    audit(db, current_user.org_id, "UPDATE_USER", "user", u.id, user=current_user,
          details={"changes": changes, "sessions_revoked": revoked})
    db.commit()
    return {"message": "User updated", "user": _user_public(u), "sessions_revoked": revoked}


@router.post("/users/{user_id}/reset-password")
def admin_reset_password(
    user_id: int,
    req: PasswordResetRequest,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db)
):
    u = _same_org_user(db, user_id, current_user)
    _not_owner_unless_self(u, current_user)
    _strength_or_422(req.new_password)
    u.password_hash = hash_password(req.new_password)
    u.password_changed_at = _utcnow()
    u.failed_login_count = 0
    u.locked_until = None
    revoked = revoke_user_sessions(db, u.id)
    audit(db, current_user.org_id, "RESET_USER_PASSWORD", "user", u.id, user=current_user,
          details={"target": u.username, "sessions_revoked": revoked})
    db.commit()
    return {"message": f"Password reset for {u.username}; all of their sessions were signed out.",
            "sessions_revoked": revoked}


@router.post("/change-password")
def change_password(
    req: ChangePasswordRequest,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not verify_password(req.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    _strength_or_422(req.new_password)
    current_user.password_hash = hash_password(req.new_password)
    current_user.password_changed_at = _utcnow()
    revoked = revoke_user_sessions(db, current_user.id,
                                   except_digest=token_digest(credentials.credentials))
    audit(db, current_user.org_id, "CHANGE_PASSWORD", "user", current_user.id, user=current_user,
          details={"other_sessions_revoked": revoked})
    db.commit()
    return {"message": "Password changed. Your other sessions were signed out.",
            "sessions_revoked": revoked}


@router.post("/logout")
def logout(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
           db: Session = Depends(get_db)):
    if credentials and credentials.credentials and len(credentials.credentials) <= 256:
        sess = db.query(UserSession).filter(
            UserSession.token_hash == token_digest(credentials.credentials)).first()
        if sess:
            sess.revoked = True
            db.commit()
    return {"message": "Successfully logged out"}


@router.post("/logout-all")
def logout_all(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    n = revoke_user_sessions(db, current_user.id)
    audit(db, current_user.org_id, "LOGOUT_ALL_SESSIONS", "user", current_user.id, user=current_user,
          details={"sessions_revoked": n})
    db.commit()
    return {"message": "All sessions signed out.", "sessions_revoked": n}
