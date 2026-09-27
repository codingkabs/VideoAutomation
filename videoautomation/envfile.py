"""Edit a .env file in place, keeping comments and order."""

from __future__ import annotations

import os
import re
from pathlib import Path

KEY_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _quote(value: str) -> str:
    if value == "" or re.fullmatch(r"[A-Za-z0-9_./:@,+\-]*", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def update_env(path: Path, values: dict[str, str]) -> list[str]:
    """Set KEY=value for each item (empty string clears it). Returns the keys changed."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    pending = dict(values)
    changed = []
    for i, line in enumerate(lines):
        m = KEY_RE.match(line)
        if m and m.group(1) in pending:
            key = m.group(1)
            new = f"{key}={_quote(pending.pop(key))}"
            if line != new:
                lines[i] = new
                changed.append(key)
    if pending:
        if lines and lines[-1].strip():
            lines.append("")
        for key, value in pending.items():
            lines.append(f"{key}={_quote(value)}")
            changed.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return changed
