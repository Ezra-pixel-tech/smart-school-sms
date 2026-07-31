from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import inspect, text

MIGRATIONS = (
    (
        "20260730_01_foundation",
        "Add tenant status, branding, session security, and archival fields.",
    ),
)


def _columns(db, table: str) -> set[str]:
    return {column["name"] for column in inspect(db.engine).get_columns(table)}


def _add_column(db, table: str, name: str, ddl: str) -> None:
    if name not in _columns(db, table):
        db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def _foundation(db) -> None:
    for name, ddl in [
        ("slug", "VARCHAR(100)"),
        ("short_name", "VARCHAR(80) DEFAULT ''"),
        ("status", "VARCHAR(30) DEFAULT 'active' NOT NULL"),
        ("archived_at", "TIMESTAMP"),
        ("trial_ends_at", "TIMESTAMP"),
        ("favicon", "VARCHAR(260) DEFAULT ''"),
        ("primary_color", "VARCHAR(20) DEFAULT '#0b2b5c'"),
        ("secondary_color", "VARCHAR(20) DEFAULT '#125edb'"),
        ("accent_color", "VARCHAR(20) DEFAULT '#079455'"),
        ("region", "VARCHAR(100) DEFAULT ''"),
        ("town", "VARCHAR(100) DEFAULT ''"),
        ("website", "VARCHAR(200) DEFAULT ''"),
        ("stamp", "VARCHAR(260) DEFAULT ''"),
        ("report_card_design", "VARCHAR(40) DEFAULT 'classic'"),
        ("receipt_design", "VARCHAR(40) DEFAULT 'classic'"),
        ("login_background", "VARCHAR(260) DEFAULT ''"),
        ("welcome_message", "VARCHAR(300) DEFAULT ''"),
        ("timezone", "VARCHAR(80) DEFAULT 'Africa/Accra'"),
        ("currency", "VARCHAR(10) DEFAULT 'GHS'"),
        ("date_format", "VARCHAR(30) DEFAULT 'DD MMM YYYY'"),
        ("onboarding_step", "INTEGER DEFAULT 1 NOT NULL"),
        ("onboarding_data", "TEXT DEFAULT '{}'"),
    ]:
        _add_column(db, "schools", name, ddl)

    for name, ddl in [
        ("session_version", "INTEGER DEFAULT 1 NOT NULL"),
        ("last_login_at", "TIMESTAMP"),
        ("disabled_reason", "VARCHAR(260) DEFAULT ''"),
        ("staff_id", "VARCHAR(80) DEFAULT ''"),
        ("employment_status", "VARCHAR(30) DEFAULT 'active'"),
        ("archived_at", "TIMESTAMP"),
    ]:
        _add_column(db, "users", name, ddl)

    for name, ddl in [
        ("status", "VARCHAR(30) DEFAULT 'active'"),
        ("programme_id", "INTEGER"),
        ("photo", "VARCHAR(260) DEFAULT ''"),
    ]:
        _add_column(db, "students", name, ddl)

    for name, ddl in [
        ("workflow_status", "VARCHAR(30) DEFAULT 'draft' NOT NULL"),
        ("submitted_at", "TIMESTAMP"),
        ("approved_at", "TIMESTAMP"),
        ("published_at", "TIMESTAMP"),
        ("approved_by", "INTEGER"),
        ("locked_at", "TIMESTAMP"),
        ("revision", "INTEGER DEFAULT 1 NOT NULL"),
    ]:
        _add_column(db, "scores", name, ddl)

    for name, ddl in [
        ("status", "VARCHAR(20) DEFAULT 'present'"),
        ("attendance_date", "DATE"),
        ("remarks", "VARCHAR(260) DEFAULT ''"),
        ("recorded_by", "INTEGER"),
    ]:
        _add_column(db, "attendance", name, ddl)

    for name, ddl in [
        ("scheduled_for", "TIMESTAMP"),
        ("expires_at", "TIMESTAMP"),
        ("class_id", "INTEGER"),
    ]:
        _add_column(db, "announcements", name, ddl)

    db.session.execute(text("UPDATE schools SET slug = 'school-' || id WHERE slug IS NULL OR slug = ''"))
    db.session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_schools_slug ON schools (slug)"))


def run_migrations(db, create_all, legacy_migrations=None) -> list[str]:
    """Run additive, idempotent migrations and record each applied version."""
    create_all()
    db.session.execute(
        text("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version VARCHAR(80) PRIMARY KEY,
            description VARCHAR(260) NOT NULL,
            applied_at TIMESTAMP NOT NULL
        )
    """)
    )
    db.session.commit()

    applied = {row[0] for row in db.session.execute(text("SELECT version FROM schema_migrations")).fetchall()}
    completed: list[str] = []

    if legacy_migrations:
        legacy_migrations()
        # Legacy PostgreSQL ALTER statements hold ACCESS EXCLUSIVE locks until
        # committed.  The foundation migration inspects tables through a new
        # connection, so leaving this transaction open makes that inspection
        # wait forever on locks owned by this same migration process.
        db.session.commit()

    for version, description in MIGRATIONS:
        if version in applied:
            continue
        try:
            if version == "20260730_01_foundation":
                _foundation(db)
            db.session.execute(
                text("""
                    INSERT INTO schema_migrations(version, description, applied_at)
                    VALUES (:version, :description, :applied_at)
                """),
                {
                    "version": version,
                    "description": description,
                    "applied_at": datetime.now(timezone.utc).replace(tzinfo=None),
                },
            )
            db.session.commit()
            completed.append(version)
        except Exception:
            db.session.rollback()
            raise
    return completed
