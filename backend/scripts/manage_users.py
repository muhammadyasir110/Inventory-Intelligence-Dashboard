"""
scripts/manage_users.py
------------------------
Command-line user administration (works on the database named by DATABASE_URL,
default backend/database/iid_local.db). Run from the backend folder:

  python scripts/manage_users.py bootstrap --org "Al-Noor Distributors" --username owner --platform-admin
  python scripts/manage_users.py set-password admin
  python scripts/manage_users.py create participant01 --role executive --org-id 2
  python scripts/manage_users.py set-platform-admin owner --on
  python scripts/manage_users.py list
  python scripts/manage_users.py revoke-sessions participant01

`bootstrap` is the secure way to create the FIRST organisation and its
administrator in staging/production (no default credentials exist there).
It refuses to run if any organisation already has an active administrator,
unless --allow-additional is given.

Passwords are prompted (not echoed) and stored as PBKDF2 hashes. Changing a
password or deactivating a user revokes that user's existing sessions.
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.settings import ASSIGNABLE_ROLES                   # noqa: E402
from core.security import hash_password, validate_password_strength   # noqa: E402
from database.db import SessionLocal, init_db               # noqa: E402
from models.models import User, UserSession                 # noqa: E402


def _prompt_password() -> str:
    pw = os.environ.get("IIP_NEW_PASSWORD") or getpass.getpass("New password: ")
    if not os.environ.get("IIP_NEW_PASSWORD") and pw != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match.")
    try:
        validate_password_strength(pw)
    except ValueError as e:
        sys.exit(str(e))
    return pw


def _revoke(db, user):
    n = db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.revoked == False).update(  # noqa: E712
        {"revoked": True})
    return n


def _bootstrap(db, a):
    from api.auth import create_organization
    from core.plans import PLANS
    from fastapi import HTTPException
    if not a.allow_additional and db.query(User).filter(User.role.in_(("admin", "owner")),
                                                         User.is_active == True).first():  # noqa: E712
        sys.exit("An administrator already exists. Use --allow-additional to create another organisation.")
    if a.plan not in PLANS:
        sys.exit(f"--plan must be one of {', '.join(PLANS)}")
    pw = _prompt_password()
    try:
        org, admin = create_organization(db, org_name=a.org, industry=a.industry, plan=a.plan,
                                         admin_username=a.username, admin_password=pw,
                                         admin_email=a.email, admin_fullname=a.full_name,
                                         platform_admin=a.platform_admin,
                                         email_verified=True,   # created by the operator
                                         source="platform_admin")
    except HTTPException as e:
        sys.exit(str(e.detail))
    db.commit()
    print(f"Created organisation '{org.name}' (id {org.id}, plan {org.subscription_tier}) with administrator "
          f"'{admin.username}'{' (platform administrator)' if a.platform_admin else ''}.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("bootstrap", help="create the first organisation and its administrator")
    p.add_argument("--org", required=True); p.add_argument("--username", required=True)
    p.add_argument("--email"); p.add_argument("--full-name"); p.add_argument("--industry", default="FMCG / Distribution")
    p.add_argument("--plan", default="enterprise"); p.add_argument("--platform-admin", action="store_true")
    p.add_argument("--allow-additional", action="store_true")
    p = sub.add_parser("set-platform-admin"); p.add_argument("username")
    g = p.add_mutually_exclusive_group(required=True); g.add_argument("--on", action="store_true"); g.add_argument("--off", action="store_true")
    p = sub.add_parser("set-password"); p.add_argument("username")
    p = sub.add_parser("create"); p.add_argument("username")
    p.add_argument("--role", default="executive", choices=ASSIGNABLE_ROLES)
    p.add_argument("--org-id", type=int, required=True); p.add_argument("--full-name")
    p = sub.add_parser("deactivate"); p.add_argument("username")
    p = sub.add_parser("revoke-sessions"); p.add_argument("username")
    p = sub.add_parser("reset-mfa", help="switch off two-factor login (lost device), revoke sessions")
    p.add_argument("username")
    sub.add_parser("list")
    a = ap.parse_args()

    init_db()
    db = SessionLocal()
    try:
        if a.cmd == "bootstrap":
            _bootstrap(db, a)
            return
        if a.cmd == "list":
            for u in db.query(User).order_by(User.org_id, User.username):
                print(f"{u.org_id:>3}  {u.username:<24} {u.role:<20} active={u.is_active} "
                      f"platform_admin={bool(u.is_platform_admin)}")
            return
        user = db.query(User).filter(User.username == a.username).first()
        if a.cmd == "create":
            if user:
                sys.exit(f"User '{a.username}' already exists.")
            from models.models import Organization
            if not db.query(Organization).filter(Organization.id == a.org_id).first():
                sys.exit(f"No organisation with id {a.org_id}.")
            db.add(User(org_id=a.org_id, username=a.username, role=a.role, is_active=True,
                        is_platform_admin=False, failed_login_count=0,
                        full_name=a.full_name or a.username, password_hash=hash_password(_prompt_password())))
            db.commit(); print(f"Created {a.username} ({a.role}) in org {a.org_id}.")
            return
        if not user:
            sys.exit(f"No user '{a.username}'.")
        if a.cmd == "set-platform-admin":
            user.is_platform_admin = bool(a.on); db.commit()
            print(f"{user.username}: platform administrator = {bool(a.on)}")
            return
        if a.cmd == "set-password":
            user.password_hash = hash_password(_prompt_password())
            n = _revoke(db, user); db.commit()
            print(f"Password changed for {user.username}; {n} active session(s) revoked.")
        elif a.cmd == "deactivate":
            user.is_active = False; n = _revoke(db, user); db.commit()
            print(f"Deactivated {user.username}; {n} session(s) revoked.")
        elif a.cmd == "reset-mfa":
            user.mfa_secret = user.mfa_pending_secret = None
            user.mfa_enabled_at = user.mfa_last_step = None
            user.mfa_recovery_hashes = None
            n = _revoke(db, user); db.commit()
            print(f"Two-factor login switched off for {user.username}; {n} session(s) revoked. "
                  "Verify the person's identity before doing this.")
        elif a.cmd == "revoke-sessions":
            n = _revoke(db, user); db.commit(); print(f"Revoked {n} session(s) for {user.username}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
