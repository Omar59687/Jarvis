"""Trusted-terminal commands for registering and indexing local repositories."""
from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys

from .developer import DeveloperRepositoryService, RepositoryError
from .storage import AccessDenied, Database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register")
    register.add_argument("--actor-id", required=True)
    register.add_argument("--workspace-id", required=True)
    register.add_argument("--approved-root-id", required=True)
    register.add_argument("--path", type=Path, required=True)
    index = commands.add_parser("index")
    index.add_argument("--actor-id", required=True)
    index.add_argument("--workspace-id", required=True)
    index.add_argument("--repository-id", required=True)
    args = parser.parse_args(argv)
    if not sys.stdin.isatty():
        parser.error("repository approval and indexing require an interactive trusted terminal")
    try:
        database = Database(args.database)
        database.initialize()
        service = DeveloperRepositoryService(database)
        if args.command == "register":
            print(f"Repository path supplied for registration: {args.path.expanduser().absolute()}")
            print(f"Workspace: {args.workspace_id}; approved root: {args.approved_root_id}")
            print("Jarvis will read bounded tracked Git objects only; it will not execute repository code.")
            if input("Type REGISTER to approve: ").strip() != "REGISTER":
                print("Registration cancelled.")
                return 1
            repo = service.register(args.actor_id, args.workspace_id, args.approved_root_id,
                                    args.path, confirmed=True)
            print(f"Registered repository {repo.id} ({repo.name}) at commit {repo.head_commit} "
                  f"on {repo.branch_name or 'detached HEAD'}.")
            return 0
        preview = service.preview_index(args.actor_id, args.workspace_id, args.repository_id)
        branch = preview["branch_name"] or "detached HEAD"
        print(f"Repository: {preview['name']} ({preview['repository_id']})")
        print(f"Workspace: {preview['workspace_id']}; path under approved root: {preview['relative_path']}")
        print(f"Current commit: {preview['current_commit']} ({branch})")
        print("Only committed Git objects will be indexed; dirty and untracked files are excluded.")
        confirmation = f"INDEX {preview['current_commit']}"
        if input(f"Type {confirmation} to approve this exact commit: ").strip() != confirmation:
            print("Indexing cancelled.")
            return 1
        repo = service.index_confirmed(args.actor_id, args.workspace_id, args.repository_id,
                                       preview["current_commit"] or "")
        print(f"Indexed {repo.name} at commit {repo.head_commit}; status={repo.index_status}.")
        return 0
    except (AccessDenied, RepositoryError, ValueError):
        print("Repository operation was denied or unavailable.", file=sys.stderr)
        return 2
    except sqlite3.Error:
        # SQLite lock/IO details can include local database paths. Keep operator
        # failures actionable without printing internals into shared terminals.
        print("Repository database is unavailable or locked; close Jarvis users and retry.",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
