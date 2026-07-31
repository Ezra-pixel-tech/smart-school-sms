"""Bind Render's port, migrate the database, then start the real application."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MaintenanceHandler(BaseHTTPRequestHandler):
    """Keep a replacement instance reachable while its schema is upgraded."""

    def _respond(self) -> None:
        body = b'{"status":"starting","detail":"Database migration in progress"}\n'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = _respond
    do_HEAD = _respond

    def log_message(self, format: str, *args: object) -> None:
        return


def start_maintenance_server(port: int) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("0.0.0.0", port), MaintenanceHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def stop_maintenance_server(server: ThreadingHTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def main() -> int:
    port = int(os.getenv("PORT", "10000"))
    server, thread = start_maintenance_server(port)
    print(f"Maintenance server listening on 0.0.0.0:{port}", flush=True)

    terminated = False

    def forward_signal(_signum: int, _frame: object) -> None:
        nonlocal terminated
        terminated = True

    signal.signal(signal.SIGTERM, forward_signal)
    signal.signal(signal.SIGINT, forward_signal)

    # Binding the port lets Render retire the previous instance. Waiting briefly
    # then running migrations without importing the web app avoids requests and
    # database connections from this instance competing with schema locks.
    time.sleep(float(os.getenv("MIGRATION_START_DELAY_SECONDS", "2")))
    if terminated:
        stop_maintenance_server(server, thread)
        return 143

    print("Running database migrations", flush=True)
    migration = subprocess.run([sys.executable, "manage.py", "migrate"], check=False)
    if migration.returncode != 0 or terminated:
        stop_maintenance_server(server, thread)
        return migration.returncode or 143

    print("Database migrations completed", flush=True)
    stop_maintenance_server(server, thread)

    gunicorn = subprocess.Popen(["gunicorn", "--bind", f"0.0.0.0:{port}", "run:app"])

    def forward_to_gunicorn(signum: int, _frame: object) -> None:
        if gunicorn.poll() is None:
            gunicorn.send_signal(signum)

    signal.signal(signal.SIGTERM, forward_to_gunicorn)
    signal.signal(signal.SIGINT, forward_to_gunicorn)
    return gunicorn.wait()


if __name__ == "__main__":
    raise SystemExit(main())
