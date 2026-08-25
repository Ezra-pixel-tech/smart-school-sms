"""Compatibility entrypoint for the Smart School SMS application.

The production application lives in :mod:`run`.  Keep this module as a thin
adapter so older WSGI/import paths cannot accidentally activate the legacy
blueprint routes that predate the hardened authentication and tenant controls.
"""

from run import create_app

__all__ = ["create_app"]
