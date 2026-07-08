import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


BASE_DIR = Path(__file__).resolve().parent


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret-before-hosting")
    DEBUG = os.getenv("FLASK_DEBUG", "1") == "1"
    DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'smart_schools_sms.db'}")
    SCHOOL_UPLOAD_LIMIT_MB = int(os.getenv("SCHOOL_UPLOAD_LIMIT_MB", "5"))
    MAX_CONTENT_LENGTH = SCHOOL_UPLOAD_LIMIT_MB * 1024 * 1024
