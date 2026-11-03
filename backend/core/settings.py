"""
core/settings.py
-----------------
Single source of runtime configuration, read from environment variables
(optionally from `backend/.env`). Development/demo behaviour is explicit and
cannot leak into production:

  IIP_ENV                 development (default) | test | staging | production
                          staging behaves exactly like production (security
                          controls, no demo seeding, no startup migrations).
  IIP_ADMIN_PASSWORD      development/test only: password for the seeded demo
                          `admin` user (falls back to the documented demo
                          password `admin123`). IGNORED in staging/production,
                          where no default administrator is ever created; use
                          `python scripts/manage_users.py bootstrap`.
  IIP_CORS_ORIGINS        comma-separated allowed browser origins.
                          development default: http://127.0.0.1:8000,http://localhost:8000
                          production default : none (same-origin only)
  IIP_ALLOW_REGISTRATION  true/false - public POST /api/auth/register-org (default false)
  IIP_SESSION_TTL_MIN     session lifetime in minutes (default 480)
  IIP_MAX_UPLOAD_MB       maximum upload size in MB (default 10)
  IIP_MAX_UPLOAD_ROWS     maximum data rows per upload (default 100000)
  IIP_PBKDF2_ITERATIONS   PBKDF2-HMAC-SHA256 iterations for new hashes (default 600000)
  IIP_LOGIN_MAX_FAILURES  failed logins per account before a temporary lock (default 5)
  IIP_LOGIN_LOCK_MIN      lock duration in minutes (default 15)
  IIP_RATE_LOGIN_PER_MIN  login/registration requests per client IP per minute (default 20)
  IIP_RATE_HEAVY_PER_MIN  optimisation/simulation/import requests per organisation per minute (default 30)
  IIP_MAX_CONCURRENT_HEAVY  simultaneous heavy computations per process (default 2)
  IIP_MAX_OPT_ITEMS       maximum SKUs in one optimisation run (default 5000)
  IIP_TRUST_PROXY         true when behind a reverse proxy that sets X-Forwarded-For (default false)
  IIP_LOG_FORMAT          json (default in staging/production) | text
  DATABASE_URL            SQLAlchemy URL (see database/db.py)

2.6 account lifecycle, e-mail, billing, operations (read where used):
  IIP_PUBLIC_BASE_URL     https://app.example.com - base of links in e-mails
  IIP_EMAIL_BACKEND       smtp | console (development only) | disabled; see core/email.py
  IIP_SMTP_*              SMTP provider settings; see core/email.py
  IIP_REQUIRE_EMAIL_VERIFICATION  true: users with an unverified e-mail cannot log in (default false)
  IIP_BILLING_PROVIDER    empty (no payments) | test_hmac (development/test only); see core/billing.py
  IIP_BILLING_WEBHOOK_SECRET      webhook signing secret of the provider
  IIP_BILLING_GRACE_DAYS  days of full access after a payment fails or a period ends (default 7)
  IIP_DELETION_GRACE_DAYS days between an owner's deletion request and the earliest purge (default 30)
  IIP_METRICS_TOKEN       enables GET /api/metrics (bearer token); unset = endpoint disabled
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import List

log = logging.getLogger("iip.settings")

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:  # optional .env support (python-dotenv is already a dependency)
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_BACKEND_DIR, ".env"), override=False)
except Exception:  # pragma: no cover
    pass

DEMO_ADMIN_PASSWORD = "admin123"
# "owner" (2.6): exactly one per organisation, created with the organisation or
# by ownership transfer - never assignable through user management.
ALLOWED_ROLES = ("owner", "admin", "inventory_manager", "procurement_manager", "warehouse_manager", "executive")
ASSIGNABLE_ROLES = ("admin", "inventory_manager", "procurement_manager", "warehouse_manager", "executive")
ADMIN_ROLES = ("owner", "admin")


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    env: str = "development"
    admin_password: str | None = None
    cors_origins: List[str] = field(default_factory=list)
    allow_registration: bool = False
    session_ttl_min: int = 480
    max_upload_mb: int = 10
    max_upload_rows: int = 100_000
    pbkdf2_iterations: int = 600_000
    login_max_failures: int = 5
    login_lock_min: int = 15
    rate_login_per_min: int = 20
    rate_heavy_per_min: int = 30
    max_concurrent_heavy: int = 2
    max_opt_items: int = 5000
    trust_proxy: bool = False
    log_format: str = "text"

    @property
    def is_production(self) -> bool:
        """Production-like environments (production and staging)."""
        return self.env in ("production", "staging")

    @property
    def demo_mode(self) -> bool:
        """True when the documented demo admin password is in use."""
        return not self.is_production and not os.getenv("IIP_ADMIN_PASSWORD")

    def admin_seed_password(self) -> str:
        if self.is_production:
            raise RuntimeError(
                "No default administrator is created in staging/production. "
                "Run `python scripts/manage_users.py bootstrap` instead.")
        if self.admin_password:
            return self.admin_password
        log.warning("DEVELOPMENT/DEMO MODE: seeding admin with the documented demo "
                    "password. Set IIP_ADMIN_PASSWORD to change it.")
        return DEMO_ADMIN_PASSWORD


def load_settings() -> Settings:
    env = os.getenv("IIP_ENV", "development").strip().lower()
    if env not in ("development", "test", "staging", "production"):
        raise RuntimeError(
            f"IIP_ENV must be 'development', 'test', 'staging' or 'production', got {env!r}")
    prod_like = env in ("production", "staging")
    origins_raw = os.getenv("IIP_CORS_ORIGINS")
    if origins_raw is None:
        origins = [] if prod_like else ["http://127.0.0.1:8000", "http://localhost:8000"]
    else:
        origins = [o.strip() for o in origins_raw.split(",") if o.strip()]
    if "*" in origins and prod_like:
        raise RuntimeError("IIP_CORS_ORIGINS='*' is not allowed in production.")
    if prod_like and (os.getenv("IIP_EMAIL_BACKEND") or "").strip().lower() == "console":
        raise RuntimeError("IIP_EMAIL_BACKEND=console is for development only (nothing is sent).")
    if prod_like and (os.getenv("IIP_BILLING_PROVIDER") or "").strip().lower() == "test_hmac":
        raise RuntimeError("IIP_BILLING_PROVIDER=test_hmac is a test adapter and is refused in staging/production.")
    pw = os.getenv("IIP_ADMIN_PASSWORD") or None
    if prod_like and pw:
        log.warning("IIP_ADMIN_PASSWORD is ignored in %s: no default administrator is created. "
                    "Use `python scripts/manage_users.py bootstrap`.", env)
        pw = None
    return Settings(
        env=env,
        admin_password=pw,
        cors_origins=origins,
        allow_registration=_bool("IIP_ALLOW_REGISTRATION", False),
        session_ttl_min=max(5, _int("IIP_SESSION_TTL_MIN", 480)),
        max_upload_mb=max(1, _int("IIP_MAX_UPLOAD_MB", 10)),
        max_upload_rows=max(10, _int("IIP_MAX_UPLOAD_ROWS", 100_000)),
        pbkdf2_iterations=max(1_000, _int("IIP_PBKDF2_ITERATIONS", 600_000)),
        login_max_failures=max(1, _int("IIP_LOGIN_MAX_FAILURES", 5)),
        login_lock_min=max(1, _int("IIP_LOGIN_LOCK_MIN", 15)),
        rate_login_per_min=max(1, _int("IIP_RATE_LOGIN_PER_MIN", 20)),
        rate_heavy_per_min=max(1, _int("IIP_RATE_HEAVY_PER_MIN", 30)),
        max_concurrent_heavy=max(1, _int("IIP_MAX_CONCURRENT_HEAVY", 2)),
        max_opt_items=max(10, _int("IIP_MAX_OPT_ITEMS", 5000)),
        trust_proxy=_bool("IIP_TRUST_PROXY", False),
        log_format=(os.getenv("IIP_LOG_FORMAT") or ("json" if prod_like else "text")).strip().lower(),
    )


settings = load_settings()
