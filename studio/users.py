"""Manage GPR Studio accounts and read the sign-in log.

    python -m studio.users add <name>        create an account (asks for the password)
    python -m studio.users passwd <name>     set a new password (signs the account out everywhere)
    python -m studio.users disable <name>    block an account and end its sessions
    python -m studio.users enable <name>
    python -m studio.users list              accounts and their last sign-in
    python -m studio.users log [--user NAME] [--limit N]   recent sign-in attempts

Reads DATABASE_URL from .env. Passwords are typed at a hidden prompt, never passed as arguments
(arguments land in shell history and the process list).
"""

from __future__ import annotations

import argparse
import getpass
import sys

from dotenv import load_dotenv

from store.auth_store import AuthStore
from store.database import DatabaseNotConfiguredError, database_url
from studio.auth import Auth, check_password_rules


def _ask_password() -> str:
    while True:
        first = getpass.getpass("Password: ")
        try:
            check_password_rules(first)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            continue
        if getpass.getpass("Again: ") == first:
            return first
        print("The two did not match.", file=sys.stderr)


def _when(value: object) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S %Z") if value else "never"  # type: ignore[attr-defined]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m studio.users", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("add", "passwd", "disable", "enable"):
        commands.add_parser(name).add_argument("username")
    commands.add_parser("list")
    log = commands.add_parser("log")
    log.add_argument("--user")
    log.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)

    load_dotenv()
    try:
        auth = Auth(AuthStore(database_url()))
    except DatabaseNotConfiguredError as exc:
        print(exc, file=sys.stderr)
        return 1
    try:
        if args.command == "add":
            auth.add_user(args.username, _ask_password())
            print(f"created {args.username}")
        elif args.command == "passwd":
            auth.set_password(args.username, _ask_password())
            print(f"password changed for {args.username}; their sessions were ended")
        elif args.command in ("disable", "enable"):
            auth.set_disabled(args.username, args.command == "disable")
            print(f"{args.username} {args.command}d")
        elif args.command == "list":
            for row in auth.store.accounts():
                state = "disabled" if row["disabled_at"] else "active"
                print(f"{row['username']:<24} {state:<9} last sign-in {_when(row['last_login'])}")
        else:
            for event in auth.store.history(args.limit, args.user):
                print(f"{_when(event.at)}  {event.event:<12} {event.username:<24} {event.ip or '-'}")
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        auth.store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
