
import os
import sqlite3
from pathlib import Path
from datetime import datetime

import psycopg
from psycopg import sql
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
SQLITE_PATH = BASE_DIR / "brandguard.db"

load_dotenv(BASE_DIR / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

TABLES = [
    "brands",
    "brand_facts",
    "brand_terminology",
    "brand_visual_rules",
    "brand_claim_rules",
    "content_items",
    "content_versions",
    "analysis_results",
    "reviews",
]


def convert_value(value, column_type):
    if value is None:
        return None

    if column_type in (
        "timestamp without time zone",
        "timestamp with time zone",
    ) and isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    return value


def migrate():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing from backend/.env")

    if not SQLITE_PATH.exists():
        raise FileNotFoundError(f"SQLite database not found: {SQLITE_PATH}")

    source = sqlite3.connect(SQLITE_PATH)
    source.row_factory = sqlite3.Row

    try:
        with psycopg.connect(DATABASE_URL) as destination:
            with destination.cursor() as cursor:

                print("\nChecking PostgreSQL destination...")

                # Never overwrite existing PostgreSQL records.
                for table in TABLES:
                    cursor.execute(
                        sql.SQL("SELECT COUNT(*) FROM {}").format(
                            sql.Identifier(table)
                        )
                    )
                    count = cursor.fetchone()[0]

                    if count != 0:
                        raise RuntimeError(
                            f"STOP: PostgreSQL table '{table}' "
                            f"already contains {count} records."
                        )

                # Verify the known reviews type mismatch is safe.
                non_null_reviews = source.execute(
                    "SELECT COUNT(*) FROM reviews "
                    "WHERE content_version IS NOT NULL"
                ).fetchone()[0]

                if non_null_reviews:
                    raise RuntimeError(
                        "STOP: reviews.content_version contains "
                        "non-NULL values. Manual type review required."
                    )

                print("Destination is empty. Starting migration...")

                expected_counts = {}

                for table in TABLES:
                    source_rows = source.execute(
                        f'SELECT * FROM "{table}"'
                    ).fetchall()

                    expected_counts[table] = len(source_rows)

                    source_columns = [
                        item[1]
                        for item in source.execute(
                            f'PRAGMA table_info("{table}")'
                        ).fetchall()
                    ]

                    cursor.execute(
                        """
                        SELECT column_name, data_type
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = %s
                        """,
                        (table,),
                    )

                    destination_types = dict(cursor.fetchall())

                    missing = set(source_columns) - set(destination_types)

                    if missing:
                        raise RuntimeError(
                            f"Missing columns in {table}: {missing}"
                        )

                    placeholders = sql.SQL(", ").join(
                        sql.Placeholder() for _ in source_columns
                    )

                    column_identifiers = sql.SQL(", ").join(
                        sql.Identifier(column)
                        for column in source_columns
                    )

                    insert_query = sql.SQL(
                        "INSERT INTO {} ({}) VALUES ({})"
                    ).format(
                        sql.Identifier(table),
                        column_identifiers,
                        placeholders,
                    )

                    values = []

                    for row in source_rows:
                        converted = [
                            convert_value(
                                row[column],
                                destination_types[column],
                            )
                            for column in source_columns
                        ]
                        values.append(converted)

                    if values:
                        cursor.executemany(insert_query, values)

                    print(f"{table}: copied {len(values)} records")

                print("\nVerifying migrated records...")

                for table in TABLES:
                    cursor.execute(
                        sql.SQL("SELECT COUNT(*) FROM {}").format(
                            sql.Identifier(table)
                        )
                    )

                    actual = cursor.fetchone()[0]
                    expected = expected_counts[table]

                    if actual != expected:
                        raise RuntimeError(
                            f"COUNT MISMATCH in {table}: "
                            f"expected {expected}, got {actual}"
                        )

                    print(f"{table}: {actual}/{expected} verified")

                # Reset auto-increment sequences to preserve future IDs.
                for table in TABLES:
                    cursor.execute(
                        "SELECT pg_get_serial_sequence(%s, 'id')",
                        (table,),
                    )
                    sequence = cursor.fetchone()[0]

                    if sequence:
                        cursor.execute(
                            sql.SQL("SELECT MAX(id) FROM {}").format(
                                sql.Identifier(table)
                            )
                        )
                        maximum_id = cursor.fetchone()[0]

                        cursor.execute(
                            "SELECT setval(%s::regclass, %s, %s)",
                            (
                                sequence,
                                maximum_id if maximum_id is not None else 1,
                                maximum_id is not None,
                            ),
                        )

            # Commit happens only if all operations succeed.
        print("\nSUCCESS: All records migrated and verified!")

    finally:
        source.close()


if __name__ == "__main__":
    print("BrandGuard AI - SQLite to PostgreSQL Migration")
    print("Source:", SQLITE_PATH)
    print("Destination: PostgreSQL")

    confirmation = input(
        "\nType MIGRATE to start the data transfer: "
    ).strip()

    if confirmation != "MIGRATE":
        print("Migration cancelled. No data was changed.")
    else:
        migrate()
