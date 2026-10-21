"""
scripts/manage_users.py
------------------------
Command-line user administration (works on the database named by DATABASE_URL,
default backend/database/iid_local.db). Run from the backend folder:

  python scripts/manage_users.py set-password admin
  python scripts/manage_users.py create participant01 --role executive
  python scripts/manage_users.py list
  python scripts/manage_users.py revoke-sessions participant01

Passwords are prompted (not echoed) and stored as PBKDF2 hashes. Changing a
password or deactivating a user revokes that user's existing sessions.
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.settings import ALLOWED_ROLES                      # noqa: E402
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("set-password"); p.add_argument("username")
    p = sub.add_parser("create"); p.add_argument("username")
    p.add_argument("--role", default="executive", choices=ALLOWED_ROLES)
    p.add_argument("--org-id", type=int, default=1); p.add_argument("--full-name")
    p = sub.add_parser("deactivate"); p.add_argument("username")
    p = sub.add_parser("revoke-sessions"); p.add_argument("username")
    sub.add_parser("list")
    a = ap.parse_args()

    init_db()
    db = SessionLocal()
    try:
        if a.cmd == "list":
            for u in db.query(User).order_by(User.org_id, User.username):
                print(f"{u.org_id:>3}  {u.username:<24} {u.role:<20} active={u.is_active}")
            return
        user = db.query(User).filter(User.username == a.username).first()
        if a.cmd == "create":
            if user:
                sys.exit(f"User '{a.username}' already exists.")
            db.add(User(org_id=a.org_id, username=a.username, role=a.role, is_active=True,
                        full_name=a.full_name or a.username, password_hash=hash_password(_prompt_password())))
            db.commit(); print(f"Created {a.username} ({a.role}) in org {a.org_id}.")
            return
        if not user:
            sys.exit(f"No user '{a.username}'.")
        if a.cmd == "set-password":
            user.password_hash = hash_password(_prompt_password())
            n = _revoke(db, user); db.commit()
            print(f"Password changed for {user.username}; {n} active session(s) revoked.")
        elif a.cmd == "deactivate":
            user.is_active = False; n = _revoke(db, user); db.commit()
            print(f"Deactivated {user.username}; {n} session(s) revoked.")
        elif a.cmd == "revoke-sessions":
            n = _revoke(db, user); db.commit(); print(f"Revoked {n} session(s) for {user.username}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
