from __future__ import annotations

from unittest.mock import MagicMock, patch

import migrations


def test_legacy_schema_changes_commit_before_foundation_inspection() -> None:
    events: list[str] = []
    db = MagicMock()
    select_result = MagicMock()
    select_result.fetchall.return_value = []
    db.session.execute.side_effect = [MagicMock(), select_result, MagicMock()]
    db.session.commit.side_effect = lambda: events.append("commit")

    def legacy() -> None:
        events.append("legacy")

    def foundation(_db: object) -> None:
        events.append("foundation")

    with patch.object(migrations, "_foundation", side_effect=foundation):
        migrations.run_migrations(db, MagicMock(), legacy_migrations=legacy)

    legacy_index = events.index("legacy")
    foundation_index = events.index("foundation")
    assert "commit" in events[legacy_index + 1 : foundation_index]
