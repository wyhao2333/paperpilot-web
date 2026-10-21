"""Offline account maintenance. Passwords are entered locally with getpass."""
import argparse
import getpass
import re
import sqlite3

from .config import Config
from .db import Database, now, uid
from .security import hash_password


def main():
    parser = argparse.ArgumentParser(description="Local PaperPilot account maintenance")
    parser.add_argument("action", choices=["create", "reset-password"])
    parser.add_argument("username")
    parser.add_argument("--admin", action="store_true", help="Create an administrator account")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{2,40}", args.username):
        raise SystemExit("Username: 2-40 letters, numbers, dot, hyphen or underscore.")
    password = getpass.getpass("Password (12+ characters, hidden): ")
    confirmation = getpass.getpass("Repeat password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")
    try:
        hashed = hash_password(password)
    except ValueError:
        raise SystemExit("Password must contain 12-256 characters.")
    db = Database(Config.from_env().data_dir)
    username = args.username.lower()
    if args.action == "create":
        first = db.one("SELECT COUNT(*) AS n FROM users")["n"] == 0
        try:
            db.execute("INSERT INTO users(id,username,display_name,password_hash,role,created) VALUES (?,?,?,?,?,?)", (uid(), username, username, hashed, "admin" if first or args.admin else "user", now()))
        except sqlite3.IntegrityError:
            raise SystemExit("Username already exists.")
    else:
        account = db.one("SELECT id FROM users WHERE username=?", (username,))
        if not account:
            raise SystemExit("Account not found.")
        with db.connect() as con:
            con.execute("UPDATE users SET password_hash=? WHERE id=?", (hashed, account["id"]))
            con.execute("DELETE FROM sessions WHERE user_id=?", (account["id"],))
    print("Account updated. No password was written to output.")


if __name__ == "__main__":
    main()
