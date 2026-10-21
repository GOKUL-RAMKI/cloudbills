"""Create the clouDBills database objects and load demonstration data.

Usage:
    python backend/scripts/init_db.py --reset
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

BACKEND_DIR = Path(__file__).resolve().parents[1]
SQL_DIR = BACKEND_DIR / "sql"
sys.path.insert(0, str(BACKEND_DIR))

from db import DATABASE_URL  # noqa: E402
from scripts.seed import run_seed  # noqa: E402

SQL_FILES = [
    "01_schema.sql",
    "02_indexes.sql",
    "03_views.sql",
    "04_functions.sql",
    "05_procedures.sql",
    "06_triggers.sql",
]


def init_database(reset: bool = False, seed: bool = True) -> None:
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        if reset:
            print("Dropping schema public ...")
            conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
            conn.execute("CREATE SCHEMA public")
            conn.execute("GRANT ALL ON SCHEMA public TO public")

        for filename in SQL_FILES:
            path = SQL_DIR / filename
            print(f"Applying {filename} ...")
            conn.execute(path.read_text(encoding="utf-8-sig"))

    print("Schema, functions, procedures, triggers and views are in place.")

    if seed:
        run_seed(force=reset)


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialise the clouDBills database")
    parser.add_argument("--reset", action="store_true", help="drop and recreate schema public")
    parser.add_argument("--no-seed", action="store_true", help="skip demonstration data")
    args = parser.parse_args()
    init_database(reset=args.reset, seed=not args.no_seed)


if __name__ == "__main__":
    main()
