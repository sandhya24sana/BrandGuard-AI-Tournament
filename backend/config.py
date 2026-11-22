
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def configure_rate_limit_storage(config):
    environment = str(config.get("APP_ENV") or "development").strip().lower()
    configured_uris = (
        config.get("RATELIMIT_STORAGE_URI"),
        config.get("REDIS_URL"),
    )

    if environment in {"production", "prod"}:
        storage_uri = next(
            (
                uri.strip()
                for uri in configured_uris
                if isinstance(uri, str) and _is_redis_uri(uri)
            ),
            None,
        )
        if storage_uri is None:
            raise RuntimeError(
                "Production rate limiting requires RATELIMIT_STORAGE_URI or REDIS_URL "
                "to contain a valid redis:// or rediss:// URL."
            )
    else:
        storage_uri = next(
            (uri.strip() for uri in configured_uris if isinstance(uri, str) and uri.strip()),
            "memory://",
        )

    config["RATELIMIT_STORAGE_URI"] = storage_uri


def _is_redis_uri(uri):
    if not isinstance(uri, str) or not uri.strip():
        return False
    try:
        parsed_uri = urlsplit(uri.strip())
    except ValueError:
        return False
    return parsed_uri.scheme.lower() in {"redis", "rediss"} and bool(parsed_uri.hostname)


class Config:
    APP_ENV = (
        "production"
        if os.environ.get("RENDER", "").strip().lower() == "true"
        else (
            os.environ.get("APP_ENV")
            or os.environ.get("FLASK_ENV")
            or "development"
        ).strip().lower()
    )

    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

    # PostgreSQL connection URL (Render)
    DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

    # SQLite fallback for local development
    DATABASE_PATH = os.environ.get(
        "DATABASE_PATH",
        str(BASE_DIR / "brandguard.db")
    )

    UPLOAD_FOLDER = os.environ.get(
        "UPLOAD_FOLDER",
        str(BASE_DIR / "uploads")
    )

    MAX_CONTENT_LENGTH = 16 * 1024 * 1024

    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    GEMINI_ANALYSIS_RATE_LIMIT = os.environ.get(
        "GEMINI_ANALYSIS_RATE_LIMIT",
        "10 per minute",
    )
    GEMINI_REWRITE_RATE_LIMIT = os.environ.get(
        "GEMINI_REWRITE_RATE_LIMIT",
        "5 per minute",
    )
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI")
    REDIS_URL = os.environ.get("REDIS_URL")
    RATELIMIT_HEADERS_ENABLED = True

    CORS_ORIGINS = [
        origin.strip()
        for origin in os.environ.get(
            "CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173,https://brand-guard-ai-three.vercel.app,https://brand-guard-ai-tournament.vercel.app"
        ).split(",")
        if origin.strip()
    ]

    PORT = int(os.environ.get("PORT", 5000))

    DEBUG = os.environ.get(
        "FLASK_DEBUG", "False"
    ).lower() in ("true", "1")
