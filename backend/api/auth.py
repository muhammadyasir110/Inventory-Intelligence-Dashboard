"""
api/auth.py
------------
Authentication and Role-Based Access Control (RBAC).

* Passwords: PBKDF2-HMAC-SHA256, per-user salt (core/security.py); legacy
  v2.1 hashes are upgraded on the next successful login.
* Sessions: random bearer tokens, stored server-side as SHA-256 digests in
  `user_sessions` with an expiry (IIP_SESSION_TTL_MIN). A missing,
  malformed, unknown, revoked or expired token is ALWAYS rejected with 401;
  there is no fallback user.
* Tenancy: every authenticated request carries the user's org_id; routers
  must scope their queries by it.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from core.settings import settings, ALLOWED_ROLES
from core.security import (
    hash_password, verify_password, needs_rehash, new_session_token, token_digest,
    validate_password_strength,
)
from database.db import get_db
from models.models import User, Organization, OrgConfig, AuditLog, UserSession

router = APIRouter(prefix="/api/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    username: str
    role: str
    org_id: int
    org_name: str
    full_name: Optional[str] = None
    expires_at: Optional[str] = None
    demo_mode: bool = False


class RegisterOrgRequest(BaseModel):
    org_name: str
    industry: Optional[str] = "FMCG / Manufacturing"
    admin_username: str
    admin_email: Optional[str] = None
    admin_password: str
    admin_fullname: Optional[str] = None


class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: str
    email: Optional[str] = None
    full_name: Optional[str] = None

    @field_validator("role")
    @classmethod
    def _role_allowed(cls, v: str) -> str:
        if v not in ALLOWED_ROLES:
            raise ValueError(f"role must be one of {', '.join(ALLOWED_ROLES)}")
        return v


def _unauthorized(detail: str = "Authentication required.") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail,
                         headers={"WWW-Authenticate": "Bearer"})


def seed_admin_user(db: Session):
    """Ensures the default organization and its admin user exist.
    The admin password comes from IIP_ADMIN_PASSWORD (required in production);
    an existing admin's password is never changed here."""
    org = db.query(Organization).filter(Organization.id == 1).first()
    if not org:
        org = Organization(id=1, name="Demo Enterprise Global", slug="demo-enterprise",
                           industry="FMCG / Manufacturing", subscription_tier="enterprise",
                           is_active=True)
        db.add(org)
        db.commit()

    admin = db.query(User).filter(User.username == "admin").first()
    if not admin:
        db.add(User(org_id=1, username="admin", email="admin@enterprise.org",
                    password_hash=hash_password(settings.admin_seed_password()),
                    role="admin", full_name="System Administrator", is_active=True))
        db.commit()


def create_session(db: Session, user: User) -> tuple[str, datetime]:
    token = new_session_token()
    now = _utcnow()
    expires = now + timedelta(minutes=settings.session_ttl_min)
    db.add(UserSession(token_hash=token_digest(token), user_id=user.id, org_id=user.org_id,
                       created_at=now, expires_at=expires, revoked=False))
    return token, expires


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """Resolves the bearer token to an active user. Never falls back."""
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
    return user


def require_role(allowed_roles: List[str]):
    """Role-Based Access Control dependency factory (admin is always allowed)."""
    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles and current_user.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required roles: {', '.join(allowed_roles)} (User role: {current_user.role})"
            )
        return current_user
    return role_checker


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == req.username).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid username or password.")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Account has been deactivated. Contact your organization administrator.")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(req.password)

    token, expires = create_session(db, user)
    user.last_login = datetime.now(timezone.utc)
    db.commit()
    org = user.organization
    return LoginResponse(
        token=token, username=user.username, role=user.role, org_id=user.org_id,
        org_name=org.name if org else "Organization", full_name=user.full_name,
        expires_at=expires.isoformat() + "Z",
        demo_mode=settings.demo_mode and user.username == "admin",
    )


@router.post("/register-org")
def register_organization(req: RegisterOrgRequest, db: Session = Depends(get_db)):
    if not settings.allow_registration:
        raise HTTPException(status_code=403, detail="Self-service organization registration is disabled (IIP_ALLOW_REGISTRATION).")
    try:
        validate_password_strength(req.admin_password)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    # Check if org name or admin username already exists
    if db.query(Organization).filter(Organization.name == req.org_name).first():
        raise HTTPException(status_code=400, detail="Organization name already exists.")
    if db.query(User).filter(User.username == req.admin_username).first():
        raise HTTPException(status_code=400, detail="Username already in use.")

    slug = req.org_name.lower().replace(" ", "-").replace("_", "-")
    org = Organization(
        name=req.org_name,
        slug=slug,
        industry=req.industry,
        subscription_tier="enterprise",
        is_active=True
    )
    db.add(org)
    db.flush()

    # Create default OrgConfig
    config = OrgConfig(
        org_id=org.id,
        service_level_target=0.95,
        annual_holding_rate=0.20,
        order_setup_cost=50.0,
        shortage_cost_multiplier=1.5,
        annual_working_capital_budget=1000000.0,
        alert_expiry_threshold_days=90
    )
    db.add(config)

    # Create Admin User
    admin = User(
        org_id=org.id,
        username=req.admin_username,
        email=req.admin_email,
        password_hash=hash_password(req.admin_password),
        role="admin",
        full_name=req.admin_fullname or req.admin_username,
        is_active=True
    )
    db.add(admin)

    # Audit log
    audit = AuditLog(
        org_id=org.id,
        username=req.admin_username,
        action="REGISTER_ORGANIZATION",
        entity_type="organization",
        entity_id=str(org.id),
        details={"org_name": org.name, "admin_user": admin.username}
    )
    db.add(audit)

    db.flush()
    token, _expires = create_session(db, admin)
    db.commit()

    return {
        "message": "Organization registered successfully",
        "token": token,
        "org_id": org.id,
        "org_name": org.name,
        "username": admin.username,
        "role": admin.role
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
        "demo_mode": settings.demo_mode and user.username == "admin",
        "status": "authenticated"
    }


@router.post("/users")
def create_tenant_user(
    req: UserCreateRequest,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db)
):
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(status_code=400, detail="Username already exists.")
    try:
        validate_password_strength(req.password)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    new_user = User(
        org_id=current_user.org_id,
        username=req.username,
        email=req.email,
        password_hash=hash_password(req.password),
        role=req.role,
        full_name=req.full_name or req.username,
        is_active=True
    )
    db.add(new_user)
    
    audit = AuditLog(
        org_id=current_user.org_id,
        user_id=current_user.id,
        username=current_user.username,
        action="CREATE_USER",
        entity_type="user",
        details={"created_user": req.username, "role": req.role}
    )
    db.add(audit)
    db.commit()

    return {"message": f"User {new_user.username} created successfully with role {new_user.role}"}


@router.get("/users")
def list_tenant_users(
    current_user: User = Depends(require_role(["admin", "executive"])),
    db: Session = Depends(get_db)
):
    users = db.query(User).filter(User.org_id == current_user.org_id).all()
    return [{
        "id": u.id,
        "username": u.username,
        "email": u.email,
        "full_name": u.full_name,
        "role": u.role,
        "is_active": u.is_active,
        "created_at": u.created_at
    } for u in users]


@router.post("/logout")
def logout(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
           db: Session = Depends(get_db)):
    if credentials and credentials.credentials:
        sess = db.query(UserSession).filter(
            UserSession.token_hash == token_digest(credentials.credentials)).first()
        if sess:
            sess.revoked = True
            db.commit()
    return {"message": "Successfully logged out"}