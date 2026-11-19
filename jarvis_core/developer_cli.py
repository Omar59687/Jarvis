"""Trusted-terminal commands for registering and indexing local repositories."""
from __future__ import annotations

import argparse
from pathlib import Path
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
    database = Database(args.database)
    database.initialize()
    service = DeveloperRepositoryService(database)
    try:
        if args.command == "register":
            print(f"Repository path supplied for registration: {args.path.expanduser().absolute()}")
            print(f"Workspace: {args.workspace_id}; approved root: {args.approved_root_id}")
            print("Jarvis will read bounded tracked Git objects only; it will not execute repository code.")
            if input("Type REGISTER to approve: ").strip() != "REGISTER":
                print("Registration cancelled.")
                return 1
            repo = service.register(args.actor_id, args.workspace_id, args.approved_root_id,
                                    args.path, confirmed=True)
            print(f"Registered repository {repo.id} ({repo.name}).")
            return 0
        print(f"Index repository {args.repository_id} in workspace {args.workspace_id}?")
        if input("Type INDEX to read the current Git commit: ").strip() != "INDEX":
            print("Indexing cancelled.")
            return 1
        repo = service.index(args.actor_id, args.workspace_id, args.repository_id)
        print(f"Indexed {repo.name} at commit {repo.head_commit}; status={repo.index_status}.")
        return 0
    except (AccessDenied, RepositoryError, ValueError):
        print("Repository operation was denied or unavailable.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
