"""Create local credentials once. Passwords are written only to ignored .env."""

import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = root / ".env"
if target.exists():
    raise SystemExit(".env already exists; kept existing settings and credentials.")
settings = {
    "DEMO_MODE": "false",
    "DISPATCH_ENGINE": "logic",
    "RETENTION_HOURS": "48",
    "VIEWER_PASSWORD": secrets.token_urlsafe(18),
    "DISPATCHER_PASSWORD": secrets.token_urlsafe(18),
    "ADMIN_PASSWORD": secrets.token_urlsafe(18),
    "INGEST_TOKEN": secrets.token_urlsafe(32),
    "INGEST_URL": "http://127.0.0.1:8001",
    "COOKIE_SECURE": "false",
}
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as stream:
    stream.write("\n".join(f"{k}={v}" for k, v in settings.items()) + "\n")
print(
    "Created .env (mode 0600). Open it locally for the role passwords. No paid services are used."
)
