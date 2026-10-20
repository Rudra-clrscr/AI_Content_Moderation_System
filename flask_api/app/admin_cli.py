"""Create or change an administrator from the command line (the only way to make one).

    python -m app.admin_cli create-admin --email you@example.com
    python -m app.admin_cli set-password --email you@example.com
    python -m app.admin_cli list

The password is typed at a hidden prompt, or read from ADMIN_INITIAL_PASSWORD for a one-off
scripted setup (remove it from .env afterwards). Nothing here runs when the server starts, so
a restart never changes anyone's password.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from werkzeug.security import generate_password_hash

import app.config  # noqa: F401  (loads flask_api/.env)
from app.admin_auth import default_db_path, password_problem
from app.admin_store import AdminStore


def _password(email: str) -> str:
    pw = os.environ.get("ADMIN_INITIAL_PASSWORD") or os.environ.get("ADMIN_PASSWORD") or ""
    if not pw:
        pw = getpass.getpass("New password: ")
        if pw != getpass.getpass("Type it again: "):
            sys.exit("The two passwords do not match.")
    problem = password_problem(pw, email)
    if problem:
        sys.exit(problem)
    return pw


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="app.admin_cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("create-admin", "set-password"):
        s = sub.add_parser(name)
        s.add_argument("--email", default=os.environ.get("ADMIN_EMAIL") or os.environ.get("Admin_Email", ""))
    sub.add_parser("list")
    args = p.parse_args(argv)
    store = AdminStore(default_db_path())

    if args.cmd == "list":
        print(f"database: {store.path}\nadmins: {store.admin_count()}")
        return
    email = args.email.strip().lower()
    if "@" not in email:
        sys.exit("Give --email you@example.com (or set ADMIN_EMAIL in .env).")
    if args.cmd == "create-admin":
        if store.get_admin(email):
            sys.exit(f"{email} already exists. Use set-password to change the password.")
        store.create_admin(email, generate_password_hash(_password(email)))
        print(f"Created administrator {email}.")
    else:
        if not store.get_admin(email):
            sys.exit(f"No administrator {email}. Use create-admin first.")
        store.set_password(email, generate_password_hash(_password(email)))
        print(f"Password changed for {email}. Existing logins were ended.")


if __name__ == "__main__":
    main()
