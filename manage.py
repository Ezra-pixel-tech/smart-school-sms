from __future__ import annotations

import argparse

from migrations import run_migrations
from run import app, db, ensure_compatibility_migrations


def migrate() -> None:
    with app.app_context():
        applied = run_migrations(db, db.create_all, legacy_migrations=ensure_compatibility_migrations)
        if applied:
            print("Applied migrations: " + ", ".join(applied))
        else:
            print("Database schema is current.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Smart School SMS management commands")
    parser.add_argument("command", choices=["migrate"])
    arguments = parser.parse_args()
    if arguments.command == "migrate":
        migrate()
