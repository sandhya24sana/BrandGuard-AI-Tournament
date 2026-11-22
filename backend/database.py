
import os
import sys
import sqlite3
from contextlib import contextmanager

from flask import current_app

backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from config import Config


# --------------------------------------------------
# PostgreSQL compatibility layer
# --------------------------------------------------

class PostgresCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self._lastrowid = None
        self._insert_result = None

    def execute(self, sql, params=None):
        self._lastrowid = None
        self._insert_result = None

        query = sql.strip()
        is_insert = query.upper().startswith("INSERT ")

        # Existing application queries use SQLite-style ? placeholders.
        query = query.replace("?", "%s")

        # PostgreSQL uses RETURNING to retrieve inserted IDs.
        if is_insert and "RETURNING" not in query.upper():
            query = query.rstrip().rstrip(";") + " RETURNING id"

        if params is None:
            self._cursor.execute(query)
        else:
            self._cursor.execute(query, params)

        if is_insert and "RETURNING" in query.upper():
            row = self._cursor.fetchone()
            if row:
                self._lastrowid = row["id"]
        return self

    def executemany(self, sql, params):
        query = sql.replace("?", "%s")
        self._cursor.executemany(query, params)
        return self

    def executescript(self, script):
        for statement in script.split(";"):
            statement = statement.strip()
            if statement:
                self.execute(statement)
        return self

    @property
    def lastrowid(self):
        return self._lastrowid

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class PostgresConnection:
    def __init__(self, connection):
        self._connection = connection

    def cursor(self):
        return PostgresCursor(self._connection.cursor())

    def execute(self, sql, params=None):
        cursor = self.cursor()
        cursor.execute(sql, params)
        return cursor

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        self._connection.close()


# --------------------------------------------------
# Database connection
# --------------------------------------------------

def get_db_connection():
    try:
        database_url = current_app.config.get(
            "DATABASE_URL", Config.DATABASE_URL
        )
        db_path = current_app.config.get(
            "DATABASE_PATH", Config.DATABASE_PATH
        )
    except RuntimeError:
        database_url = Config.DATABASE_URL
        db_path = Config.DATABASE_PATH

    if database_url:
        try:
            import psycopg
            from psycopg.rows import dict_row

            # Support older Render PostgreSQL URL format.
            if database_url.startswith("postgres://"):
                database_url = database_url.replace(
                    "postgres://", "postgresql://", 1
                )

            connection = psycopg.connect(
                database_url,
                row_factory=dict_row,
                connect_timeout=15,
            )
            return PostgresConnection(connection)

        except Exception:
            # Do not silently fall back to SQLite on production.
            raise

    # Local SQLite fallback.
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

    conn = sqlite3.connect(
        db_path,
        timeout=30.0,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("PRAGMA foreign_keys = ON;")

    return conn


@contextmanager
def db_session():
    conn = get_db_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --------------------------------------------------
# PostgreSQL schema
# --------------------------------------------------

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS brands (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    tagline TEXT,
    description TEXT,
    industry TEXT,
    primary_tone TEXT DEFAULT '',
    voice_traits TEXT,
    target_audience TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS brand_facts (
    id BIGSERIAL PRIMARY KEY,
    brand_id BIGINT NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    statement TEXT NOT NULL,
    key_metric TEXT,
    keywords TEXT,
    active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS brand_terminology (
    id BIGSERIAL PRIMARY KEY,
    brand_id BIGINT NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    term TEXT NOT NULL,
    replacement TEXT NOT NULL,
    replacement_text TEXT,
    reason TEXT,
    category TEXT DEFAULT 'Terminology',
    severity TEXT DEFAULT 'warning',
    active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS brand_visual_rules (
    id BIGSERIAL PRIMARY KEY,
    brand_id BIGINT NOT NULL UNIQUE REFERENCES brands(id) ON DELETE CASCADE,
    primary_colors TEXT,
    secondary_colors TEXT,
    forbidden_colors TEXT,
    logo_rules TEXT,
    font_family TEXT,
    contrast_minimum TEXT DEFAULT '',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS brand_claim_rules (
    id BIGSERIAL PRIMARY KEY,
    brand_id BIGINT NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    rule_name TEXT NOT NULL,
    pattern_or_trigger TEXT NOT NULL,
    severity TEXT DEFAULT 'critical',
    required_disclaimer TEXT,
    replacement_text TEXT,
    explanation TEXT,
    active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS content_items (
    id BIGSERIAL PRIMARY KEY,
    brand_id BIGINT NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    content_type TEXT DEFAULT 'Ad Copy',
    channel TEXT DEFAULT 'Digital Ads',
    original_text TEXT NOT NULL,
    corrected_text TEXT,
    current_version INTEGER DEFAULT 1,
    status TEXT DEFAULT 'draft',
    risk_score INTEGER DEFAULT 0,
    risk_level TEXT DEFAULT 'LOW',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS content_versions (
    id BIGSERIAL PRIMARY KEY,
    content_id BIGINT NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    text TEXT NOT NULL,
    corrected_text TEXT,
    risk_score INTEGER,
    risk_level TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS analysis_results (
    id BIGSERIAL PRIMARY KEY,
    content_id BIGINT NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    score INTEGER NOT NULL,
    risk_level TEXT NOT NULL,
    sub_scores TEXT NOT NULL,
    engine_used TEXT NOT NULL,
    issues TEXT NOT NULL,
    summary TEXT,
    corrected_text TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reviews (
    id BIGSERIAL PRIMARY KEY,
    content_id BIGINT NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
    content_version INTEGER,
    reviewer_name TEXT NOT NULL,
    reviewer_role TEXT,
    decision TEXT NOT NULL,
    comments TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS manual_correction_drafts (
    id BIGSERIAL PRIMARY KEY,
    content_id BIGINT NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
    content_version_id BIGINT NOT NULL REFERENCES content_versions(id) ON DELETE CASCADE,
    issue_id TEXT NOT NULL,
    original_text TEXT NOT NULL,
    replacement_text TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (content_id, content_version_id, issue_id)
);
"""


# --------------------------------------------------
# SQLite schema
# --------------------------------------------------

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS brands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    tagline TEXT,
    description TEXT,
    industry TEXT,
    primary_tone TEXT DEFAULT '',
    voice_traits TEXT,
    target_audience TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS brand_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    statement TEXT NOT NULL,
    key_metric TEXT,
    keywords TEXT,
    active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (brand_id) REFERENCES brands(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS brand_terminology (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id INTEGER NOT NULL,
    term TEXT NOT NULL,
    replacement TEXT NOT NULL,
    replacement_text TEXT,
    reason TEXT,
    category TEXT DEFAULT 'Terminology',
    severity TEXT DEFAULT 'warning',
    active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (brand_id) REFERENCES brands(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS brand_visual_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id INTEGER NOT NULL UNIQUE,
    primary_colors TEXT,
    secondary_colors TEXT,
    forbidden_colors TEXT,
    logo_rules TEXT,
    font_family TEXT,
    contrast_minimum TEXT DEFAULT '',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (brand_id) REFERENCES brands(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS brand_claim_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id INTEGER NOT NULL,
    rule_name TEXT NOT NULL,
    pattern_or_trigger TEXT NOT NULL,
    severity TEXT DEFAULT 'critical',
    required_disclaimer TEXT,
    replacement_text TEXT,
    explanation TEXT,
    active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (brand_id) REFERENCES brands(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS content_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    content_type TEXT DEFAULT 'Ad Copy',
    channel TEXT DEFAULT 'Digital Ads',
    original_text TEXT NOT NULL,
    corrected_text TEXT,
    current_version INTEGER DEFAULT 1,
    status TEXT DEFAULT 'draft',
    risk_score INTEGER DEFAULT 0,
    risk_level TEXT DEFAULT 'LOW',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (brand_id) REFERENCES brands(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS content_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id INTEGER NOT NULL,
    version_number INTEGER NOT NULL,
    text TEXT NOT NULL,
    corrected_text TEXT,
    risk_score INTEGER,
    risk_level TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (content_id) REFERENCES content_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS analysis_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id INTEGER NOT NULL,
    version_number INTEGER NOT NULL,
    score INTEGER NOT NULL,
    risk_level TEXT NOT NULL,
    sub_scores TEXT NOT NULL,
    engine_used TEXT NOT NULL,
    issues TEXT NOT NULL,
    summary TEXT,
    corrected_text TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (content_id) REFERENCES content_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id INTEGER NOT NULL,
    content_version INTEGER,
    reviewer_name TEXT NOT NULL,
    reviewer_role TEXT,
    decision TEXT NOT NULL,
    comments TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (content_id) REFERENCES content_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS manual_correction_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id INTEGER NOT NULL,
    content_version_id INTEGER NOT NULL,
    issue_id TEXT NOT NULL,
    original_text TEXT NOT NULL,
    replacement_text TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (content_id, content_version_id, issue_id),
    FOREIGN KEY (content_id) REFERENCES content_items(id) ON DELETE CASCADE,
    FOREIGN KEY (content_version_id) REFERENCES content_versions(id) ON DELETE CASCADE
);
"""


# --------------------------------------------------
# Database initialization
# --------------------------------------------------

def init_db():
    conn = get_db_connection()

    try:
        cursor = conn.cursor()

        is_postgres = isinstance(conn, PostgresConnection)

        if is_postgres:
            cursor.executescript(POSTGRES_SCHEMA)

            # Keep these migrations safe for existing databases.
            cursor.execute("""
                ALTER TABLE brand_terminology
                ADD COLUMN IF NOT EXISTS replacement_text TEXT
            """)
            cursor.execute("""
                ALTER TABLE brand_claim_rules
                ADD COLUMN IF NOT EXISTS replacement_text TEXT
            """)
            cursor.execute("""
                ALTER TABLE reviews
                ADD COLUMN IF NOT EXISTS content_version INTEGER
            """)
            cursor.execute("""
                UPDATE brand_terminology
                SET replacement_text = TRIM(replacement)
                WHERE replacement_text IS NULL
                  AND POSITION('/' IN replacement) = 0
                  AND TRIM(replacement) <> ''
            """)

        else:
            cursor.executescript(SQLITE_SCHEMA)

            for table_name, column_name in (
                ("brand_terminology", "replacement_text"),
                ("brand_claim_rules", "replacement_text"),
                ("reviews", "content_version"),
            ):
                columns = {
                    row["name"]
                    for row in cursor.execute(
                        f"PRAGMA table_info({table_name})"
                    ).fetchall()
                }

                if column_name not in columns:
                    cursor.execute(
                        f"ALTER TABLE {table_name} ADD COLUMN {column_name} TEXT"
                    )

            cursor.execute("""
                UPDATE brand_terminology
                SET replacement_text = trim(replacement)
                WHERE replacement_text IS NULL
                  AND instr(replacement, '/') = 0
                  AND trim(replacement) != ''
            """)

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def dict_from_row(row):
    if row is None:
        return None
    return dict(row)
