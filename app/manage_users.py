"""Manage CloudTasks accounts (invite-only: there is no public sign-up).

Run inside the app container on the VM:
  sudo docker compose exec -it app python -m app.manage_users create alice           (role: user)
  sudo docker compose exec -it app python -m app.manage_users create devansh --role admin
  sudo docker compose exec -it app python -m app.manage_users create demo --role demo   (gets 10 sample tasks)
  sudo docker compose exec -it app python -m app.manage_users list
  sudo docker compose exec -it app python -m app.manage_users reset alice
  sudo docker compose exec -it app python -m app.manage_users delete alice

Passwords are typed twice with getpass: never a command-line argument (so never in shell history
or `ps`), never printed, never logged. Resetting a password signs that user out everywhere.
"""
import argparse
import getpass
import sqlite3
import sys

from . import auth, db
from .samples import DEMO_DONE, sample_tasks
from .schemas import TaskCreate


def ask_password(username: str) -> str:
    if not sys.stdin.isatty():
        raise SystemExit("Refusing to read a password without an interactive terminal (use: docker compose exec -it ...).")
    for _ in range(3):
        first = getpass.getpass(f"New password for {username} (min {auth.PASSWORD_MIN} characters): ")
        try:
            auth.validate_password(first, username)
        except ValueError as e:
            print(e)
            continue
        if getpass.getpass("Repeat the password: ") != first:
            print("The passwords don't match. Try again.")
            continue
        return first
    raise SystemExit("No password set.")


def seed_demo(user_id: int) -> int:
    for t in reversed(sample_tasks()):
        data = TaskCreate(**t).model_dump(mode="json")
        data["done"] = t["title"] in DEMO_DONE
        db.create_task(user_id, data)
    return db.count_tasks(user_id)


def cmd_create(args) -> int:
    username = auth.validate_username(args.username)
    if db.get_user_by_name(username):
        raise SystemExit(f"User '{username}' already exists. Use 'reset' to change the password.")
    password = ask_password(username)
    try:
        user_id = db.create_user(username, args.role, auth.hash_password(password))
    except sqlite3.IntegrityError:
        raise SystemExit(f"User '{username}' already exists.")
    print(f"Created {args.role} account '{username}'.")
    if args.role == "demo":
        print(f"Added {seed_demo(user_id)} sample tasks to the read-only demo account.")
    return 0


def cmd_list(args) -> int:
    users = db.list_users()  # public fields only, no password hashes
    if not users:
        print("No accounts yet.")
        return 0
    print(f"{'USERNAME':<20} {'ROLE':<7} {'TASKS':>5}  CREATED (UTC)")
    for u in users:
        print(f"{u['username']:<20} {u['role']:<7} {u['tasks']:>5}  {u['created_at']}")
    return 0


def cmd_reset(args) -> int:
    username = auth.validate_username(args.username)
    user = db.get_user_by_name(username)
    if not user:
        raise SystemExit(f"No user '{username}'.")
    db.set_password_hash(user["id"], auth.hash_password(ask_password(username)))
    print(f"Password changed for '{username}'. All of their sessions were signed out.")
    return 0


def cmd_delete(args) -> int:
    username = auth.validate_username(args.username)
    user = db.get_user_by_name(username)
    if not user:
        raise SystemExit(f"No user '{username}'.")
    if not args.yes:
        if input(f"Delete '{username}' and all of their tasks? Type the username to confirm: ").strip() != username:
            print("Cancelled.")
            return 1
    db.delete_user(user["id"])
    print(f"Deleted '{username}' and their tasks and sessions.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.manage_users", description="Manage CloudTasks accounts.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="create an account (asks for the password)")
    c.add_argument("username")
    c.add_argument("--role", choices=auth.ROLES, default="user")
    sub.add_parser("list", help="list accounts (no password hashes)")
    r = sub.add_parser("reset", help="set a new password (asks for it) and sign the user out everywhere")
    r.add_argument("username")
    d = sub.add_parser("delete", help="delete an account and its tasks")
    d.add_argument("username")
    d.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = ap.parse_args(argv)

    db.init_db()
    try:
        return {"create": cmd_create, "list": cmd_list, "reset": cmd_reset, "delete": cmd_delete}[args.cmd](args)
    except ValueError as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    sys.exit(main())
