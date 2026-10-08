"""The backend behind a JSON API on 127.0.0.1: every interface is a client of it.

`serve(store)` starts it in-process and returns `(server, base_url)`; `stop(server)` ends it.
The contract is `openapi.json` beside this file (`docs/api/openapi.json` in the repository).
"""

from .server import serve, stop

__all__ = ["serve", "stop"]
