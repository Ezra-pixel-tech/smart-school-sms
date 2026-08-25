import os
import secrets
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


BASE_DIR = Path(__file__).resolve().parent
_DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"
_SECRET_KEY = (os.getenv("SECRET_KEY") or "").strip()

# Production sessions must never be signed with a public/default secret. Local
# debug remains convenient, but gets an unpredictable process-local key.
if not _SECRET_KEY:
    if _DEBUG:
        _SECRET_KEY = secrets.token_urlsafe(48)
    else:
        raise RuntimeError("SECRET_KEY must be configured when FLASK_DEBUG is disabled")


class Config:
    SECRET_KEY = _SECRET_KEY
    DEBUG = _DEBUG
    DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'smart_schools_sms.db'}")
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": int(os.getenv("DB_POOL_RECYCLE_SECONDS", "280")),
    }
    SCHOOL_UPLOAD_LIMIT_MB = int(os.getenv("SCHOOL_UPLOAD_LIMIT_MB", "5"))
    MAX_CONTENT_LENGTH = SCHOOL_UPLOAD_LIMIT_MB * 1024 * 1024
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "0" if DEBUG else "1") == "1"
    PERMANENT_SESSION_LIFETIME_MINUTES = int(os.getenv("SESSION_LIFETIME_MINUTES", "480"))
    TRUSTED_PROXY_COUNT = int(os.getenv("TRUSTED_PROXY_COUNT", "1"))
    SCHOOL_PORTAL_BASE_DOMAIN = os.getenv("SCHOOL_PORTAL_BASE_DOMAIN", "").strip().lower().lstrip(".")
    LOGIN_RATE_LIMIT_ATTEMPTS = int(os.getenv("LOGIN_RATE_LIMIT_ATTEMPTS", "5"))
    LOGIN_RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("LOGIN_RATE_LIMIT_WINDOW_SECONDS", "900"))
    PASSWORD_MIN_LENGTH = int(os.getenv("PASSWORD_MIN_LENGTH", "10"))
    PASSWORD_RESET_RATE_LIMIT_ATTEMPTS = int(os.getenv("PASSWORD_RESET_RATE_LIMIT_ATTEMPTS", "5"))
    PASSWORD_RESET_RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("PASSWORD_RESET_RATE_LIMIT_WINDOW_SECONDS", "3600"))
    PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "")
    PAYSTACK_PUBLIC_KEY = os.getenv("PAYSTACK_PUBLIC_KEY", "")
    PARENT_REPORT_FEE = os.getenv("PARENT_REPORT_FEE", "0.00")
    PAYSTACK_CURRENCY = os.getenv("PAYSTACK_CURRENCY", "GHS").upper()
