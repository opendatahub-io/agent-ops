"""Create local configuration and fill only missing internal secrets."""

import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / ".env"
if path.exists():
    path.chmod(0o600)
text = path.read_text() if path.exists() else (root / ".env.example").read_text()
lines = text.splitlines()
for name in ["DATABASE_PASSWORD", "SESSION_SECRET", "AGENT_TOKEN_SIGNING_SECRET"]:
    index = next(
        (i for i, line in enumerate(lines) if line.startswith(name + "=")), None
    )
    if index is None:
        lines.append(name + "=" + secrets.token_urlsafe(40))
    elif not lines[index].split("=", 1)[1].strip().strip("\"'"):
        lines[index] = name + "=" + secrets.token_urlsafe(40)
descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(descriptor, "w") as output:
    output.write("\n".join(lines) + "\n")
path.chmod(0o600)
print(
    ".env initialized; existing values preserved. Configure ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL and ANTHROPIC_MODEL before deployment."
)
