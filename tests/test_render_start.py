from __future__ import annotations

from unittest.mock import MagicMock, patch

import render_start


def test_migration_completes_before_gunicorn_starts() -> None:
    server = MagicMock()
    thread = MagicMock()
    gunicorn = MagicMock()
    gunicorn.wait.return_value = 0
    migration = MagicMock(returncode=0)

    with (
        patch.object(render_start, "start_maintenance_server", return_value=(server, thread)),
        patch.object(render_start, "stop_maintenance_server") as stop_server,
        patch.object(render_start.subprocess, "run", return_value=migration) as run_migration,
        patch.object(render_start.subprocess, "Popen", return_value=gunicorn) as start_gunicorn,
        patch.object(render_start.time, "sleep"),
        patch.object(render_start.signal, "signal"),
        patch.dict(render_start.os.environ, {"PORT": "12345"}),
    ):
        assert render_start.main() == 0

    run_migration.assert_called_once()
    stop_server.assert_called_once_with(server, thread)
    start_gunicorn.assert_called_once_with(
        ["gunicorn", "--bind", "0.0.0.0:12345", "run:app"]
    )


def test_migration_failure_stops_maintenance_server_without_gunicorn() -> None:
    server = MagicMock()
    thread = MagicMock()
    migration = MagicMock(returncode=7)

    with (
        patch.object(render_start, "start_maintenance_server", return_value=(server, thread)),
        patch.object(render_start, "stop_maintenance_server") as stop_server,
        patch.object(render_start.subprocess, "run", return_value=migration),
        patch.object(render_start.subprocess, "Popen") as start_gunicorn,
        patch.object(render_start.time, "sleep"),
        patch.object(render_start.signal, "signal"),
    ):
        assert render_start.main() == 7

    stop_server.assert_called_once_with(server, thread)
    start_gunicorn.assert_not_called()


def test_maintenance_handler_supports_get_and_head() -> None:
    assert render_start.MaintenanceHandler.do_GET is render_start.MaintenanceHandler._respond
    assert render_start.MaintenanceHandler.do_HEAD is render_start.MaintenanceHandler._respond
