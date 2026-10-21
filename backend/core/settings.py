"""
core/settings.py
-----------------
Single source of runtime configuration, read from environment variables
(optionally from `backend/.env`). Development/demo behaviour is explicit and
cannot leak into production:

  IIP_ENV                 development (default) | production
  IIP_ADMIN_PASSWORD      password for the seeded `admin` user. Optional in
                          development (falls back to the documented demo
                          password `admin123`); REQUIRED in production.
  IIP_CORS_ORIGINS        comma-separated allowed browser origins.
                          development default: http://127.0.0.1:8000,http://localhost:8000
                          production default : none (same-origin only)
  IIP_ALLOW_REGISTRATION  true/false - public POST /api/auth/register-org (default false)
  IIP_SESSION_TTL_MIN     session lifetime in minutes (default 480)
  IIP_MAX_UPLOAD_MB       maximum upload size in MB (default 10)
  IIP_MAX_UPLOAD_ROWS     maximum data rows per upload (default 100000)
  IIP_PBKDF2_ITERATIONS   PBKDF2-HMAC-SHA256 iterations for new hashes (default 600000)
  DATABASE_URL            SQLAlchemy URL (see database/db.py)
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
ALLOWED_ROLES = ("admin", "inventory_manager", "procurement_manager", "warehouse_manager", "executive")


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

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def demo_mode(self) -> bool:
        """True when the documented demo admin password is in use."""
        return not self.is_production and not os.getenv("IIP_ADMIN_PASSWORD")

    def admin_seed_password(self) -> str:
        if self.admin_password:
            return self.admin_password
        if self.is_production:
            raise RuntimeError(
                "IIP_ENV=production requires IIP_ADMIN_PASSWORD to be set "
                "(the demo password is refused in production).")
        log.warning("DEVELOPMENT/DEMO MODE: seeding admin with the documented demo "
                    "password. Set IIP_ADMIN_PASSWORD to change it.")
        return DEMO_ADMIN_PASSWORD


def load_settings() -> Settings:
    env = os.getenv("IIP_ENV", "development").strip().lower()
    if env not in ("development", "production"):
        raise RuntimeError(f"IIP_ENV must be 'development' or 'production', got {env!r}")
    origins_raw = os.getenv("IIP_CORS_ORIGINS")
    if origins_raw is None:
        origins = [] if env == "production" else ["http://127.0.0.1:8000", "http://localhost:8000"]
    else:
        origins = [o.strip() for o in origins_raw.split(",") if o.strip()]
    if "*" in origins and env == "production":
        raise RuntimeError("IIP_CORS_ORIGINS='*' is not allowed in production.")
    pw = os.getenv("IIP_ADMIN_PASSWORD") or None
    if env == "production" and pw and len(pw) < 12:
        raise RuntimeError("IIP_ADMIN_PASSWORD must be at least 12 characters in production.")
    return Settings(
        env=env,
        admin_password=pw,
        cors_origins=origins,
        allow_registration=_bool("IIP_ALLOW_REGISTRATION", False),
        session_ttl_min=max(5, _int("IIP_SESSION_TTL_MIN", 480)),
        max_upload_mb=max(1, _int("IIP_MAX_UPLOAD_MB", 10)),
        max_upload_rows=max(10, _int("IIP_MAX_UPLOAD_ROWS", 100_000)),
        pbkdf2_iterations=max(1_000, _int("IIP_PBKDF2_ITERATIONS", 600_000)),
    )


settings = load_settings()
