from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone

from flask import current_app, jsonify, request
from sqlalchemy import text


class RedactingFilter(logging.Filter):
    _secret = re.compile(
        r"(?i)(authorization|api[_-]?key|secret|password|token)"
        r"(\s*[=:]\s*)([^\s,;]+)"
    )

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = self._secret.sub(r"\1\2[REDACTED]", message)
        record.msg = redacted
        record.args = ()
        return True


def configure_logging(app) -> None:
    level = logging.DEBUG if app.debug else logging.INFO
    app.logger.setLevel(level)
    for handler in app.logger.handlers:
        handler.addFilter(RedactingFilter())


def validate_environment(app) -> list[str]:
    errors: list[str] = []
    warnings: list[str] = []
    production = not app.debug
    secret = app.config.get("SECRET_KEY") or ""
    database_url = app.config.get("SQLALCHEMY_DATABASE_URI") or ""

    if production and len(secret) < 32:
        errors.append("SECRET_KEY must contain at least 32 characters in production.")
    if production and database_url.startswith("sqlite:"):
        warnings.append(
            "Production is using SQLite. Configure DATABASE_URL with PostgreSQL before accepting live school data."
        )
    if os.getenv("PAYSTACK_SECRET_KEY") and not os.getenv("PAYSTACK_SECRET_KEY", "").startswith("sk_"):
        errors.append("PAYSTACK_SECRET_KEY has an invalid format.")
    if os.getenv("RESEND_API_KEY") and not os.getenv("EMAIL_FROM"):
        errors.append("EMAIL_FROM is required when RESEND_API_KEY is configured.")
    if os.getenv("APP_URL") and not os.getenv("APP_URL", "").startswith("https://"):
        warnings.append("APP_URL should use HTTPS in production.")

    app.config["ENVIRONMENT_WARNINGS"] = tuple(warnings)
    if errors:
        raise RuntimeError("Environment validation failed: " + " ".join(errors))
    for warning in warnings:
        app.logger.warning(warning)
    return warnings


def wants_json() -> bool:
    return request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json"


def register_error_handlers(app) -> None:
    labels = {
        400: "The request could not be processed.",
        403: "You do not have permission to access this resource.",
        404: "The requested resource was not found.",
        429: "Too many requests. Please try again later.",
    }

    for status, message in labels.items():

        def handler(error, status=status, message=message):
            if wants_json():
                return jsonify(error=message, status=status), status
            return (
                f"""<!doctype html><html><head><title>{status}</title></head>
                <body><main style="max-width:640px;margin:10vh auto;font-family:sans-serif">
                <h1>{status}</h1><p>{message}</p>
                <a href="/">Return home</a></main></body></html>""",
                status,
            )

        app.register_error_handler(status, handler)

    @app.errorhandler(500)
    def internal_error(error):
        current_app.logger.exception("Unhandled application error")
        message = "An unexpected error occurred. Please try again."
        if wants_json():
            return jsonify(error=message, status=500), 500
        return (
            """<!doctype html><html><head><title>Server error</title></head>
            <body><main style="max-width:640px;margin:10vh auto;font-family:sans-serif">
            <h1>Something went wrong</h1>
            <p>An unexpected error occurred. Please try again.</p>
            <a href="/">Return home</a></main></body></html>""",
            500,
        )


def register_health_routes(app, db) -> None:
    @app.get("/health")
    def health():
        database = "ok"
        status = 200
        try:
            db.session.execute(text("SELECT 1"))
        except Exception:
            database = "unavailable"
            status = 503
            app.logger.exception("Database health check failed")
        payload = {
            "status": "ok" if status == 200 else "degraded",
            "database": database,
            "time": datetime.now(timezone.utc).isoformat(),
            "version": os.getenv("RENDER_GIT_COMMIT", "development")[:12],
        }
        return jsonify(payload), status

    @app.get("/ready")
    def readiness():
        if current_app.config.get("ENVIRONMENT_WARNINGS"):
            return jsonify(status="ready", warnings=len(current_app.config["ENVIRONMENT_WARNINGS"])), 200
        return jsonify(status="ready"), 200


def clean_text(value, *, maximum=255, required=False, field="value") -> str:
    cleaned = str(value or "").strip()
    if required and not cleaned:
        raise ValueError(f"{field} is required.")
    if len(cleaned) > maximum:
        raise ValueError(f"{field} must contain at most {maximum} characters.")
    return cleaned


def clean_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", clean_text(value, maximum=100, required=True, field="School slug").lower()).strip(
        "-"
    )
    if len(slug) < 3:
        raise ValueError("School slug must contain at least 3 letters or numbers.")
    return slug


def clean_email(value: str, *, required=False) -> str:
    email = clean_text(value, maximum=160, required=required, field="Email").lower()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("Enter a valid email address.")
    return email


def validate_password_strength(password: str) -> None:
    minimum = max(10, int(os.getenv("PASSWORD_MIN_LENGTH", "10")))
    if len(password or "") < minimum:
        raise ValueError(f"Password must contain at least {minimum} characters.")
    if not re.search(r"[A-Z]", password):
        raise ValueError("Password must contain an uppercase letter.")
    if not re.search(r"[a-z]", password):
        raise ValueError("Password must contain a lowercase letter.")
    if not re.search(r"\d", password):
        raise ValueError("Password must contain a number.")
