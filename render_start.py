"""Start Gunicorn while applying production database migrations safely.

Render keeps the previous instance alive until the replacement binds a port.
Starting Gunicorn first lets the replacement bind promptly, after which the
additive migration can acquire the locks released by the previous instance.
If migration fails, Gunicorn is stopped and the deployment fails closed.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    gunicorn = subprocess.Popen(["gunicorn", "run:app"])

    def forward_signal(signum: int, _frame: object) -> None:
        if gunicorn.poll() is None:
            gunicorn.send_signal(signum)

    signal.signal(signal.SIGTERM, forward_signal)
    signal.signal(signal.SIGINT, forward_signal)

    # Give Gunicorn a brief opportunity to bind Render's assigned port before
    # the migration waits for locks held by the retiring instance.
    time.sleep(float(os.getenv("MIGRATION_START_DELAY_SECONDS", "2")))
    if gunicorn.poll() is not None:
        return gunicorn.returncode or 1

    migration = subprocess.run(
        [sys.executable, "manage.py", "migrate"],
        check=False,
    )
    if migration.returncode != 0:
        stop_process(gunicorn)
        return migration.returncode

    return gunicorn.wait()


if __name__ == "__main__":
    raise SystemExit(main())
