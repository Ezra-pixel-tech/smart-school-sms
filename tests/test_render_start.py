from __future__ import annotations

from unittest.mock import MagicMock, patch

import render_start


def test_migration_success_keeps_gunicorn_running() -> None:
    gunicorn = MagicMock()
    gunicorn.poll.return_value = None
    gunicorn.wait.return_value = 0
    migration = MagicMock(returncode=0)

    with (
        patch.object(render_start.subprocess, "Popen", return_value=gunicorn),
        patch.object(render_start.subprocess, "run", return_value=migration) as run_migration,
        patch.object(render_start.time, "sleep"),
        patch.object(render_start.signal, "signal"),
    ):
        assert render_start.main() == 0

    run_migration.assert_called_once()
    gunicorn.terminate.assert_not_called()


def test_migration_failure_stops_gunicorn_and_fails() -> None:
    gunicorn = MagicMock()
    gunicorn.poll.return_value = None
    gunicorn.wait.return_value = 0
    migration = MagicMock(returncode=7)

    with (
        patch.object(render_start.subprocess, "Popen", return_value=gunicorn),
        patch.object(render_start.subprocess, "run", return_value=migration),
        patch.object(render_start.time, "sleep"),
        patch.object(render_start.signal, "signal"),
    ):
        assert render_start.main() == 7

    gunicorn.terminate.assert_called_once()
