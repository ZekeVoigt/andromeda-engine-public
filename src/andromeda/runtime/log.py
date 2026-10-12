"""Append-only `events.jsonl`: the timeline a run leaves behind when it dies mid-flight.

Harvested from agent-world's `log.py` and trimmed to the surface `cli.run()` actually
reaches. Dropped as swarm-only, with no caller here: `board()`, `board.jsonl`,
`self.posts`, the `on_event` callback and the `agent` positional. Changed: the single
`OPENROUTER_API_KEY` scrub key becomes the `CREDENTIAL_ENV` tuple, and `event()` returns
`None` and never raises.

Stdlib only, and nothing from `andromeda.outcome`: this is infrastructure next to
`sandbox.py`, and reaching up into `adapters/` from `runtime/` would invert the layering.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# The credential names this repo knows about, in the order the broker prefers them. ONE
# tuple, in the lowest module, serving both callers: `RunLog._secrets` below scrubs these
# values out of events.jsonl, and `broker.host_credential()` picks the first one set on the
# host. The stub asked whether this and docker.py's copy should drift -- they do not, because
# E02 deletes docker.py's outright and makes credential names a runtime/ concern. Order
# matters: it is the broker's resolution order, not an alphabetical list.
#
# The broker's per-container HANDLE is deliberately absent: it is worthless once its
# container exits, and scrubbing it would hide the one value safe to read in a log.
CREDENTIAL_ENV = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "OPENROUTER_API_KEY")
REDACTED = "[REDACTED]"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunLog:
    """One append-only JSONL timeline per run dir. Every write is best-effort."""

    def __init__(self, run_dir: str | Path) -> None:
        self.run_dir = Path(run_dir)
        self.events_path = self.run_dir / "events.jsonl"
        # Truthy values only: an unset var is "" for os.environ.get's default and
        # "".replace would inject REDACTED between every character of every line.
        self._secrets = [v for k in CREDENTIAL_ENV if (v := os.environ.get(k))]
        # The constructor is best-effort for the same reason event() is: it runs *before*
        # run()'s try, so a raising RunLog(run_dir) would kill a run with no record.json
        # at all -- the exact opposite of what the logger is for.
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            self.events_path.touch()
        except OSError:
            pass

    def _scrub(self, line: str) -> str:
        for secret in self._secrets:
            line = line.replace(secret, REDACTED)
        return line

    def event(self, kind: str, **data: Any) -> None:
        """Append one event line. Never raises -- a broken log must not break the run.

        Broader than OSError on purpose: the contract the caller is written against is
        "never raises", so a payload json.dumps chokes on must not take the run down
        either. Nothing above this line is allowed to depend on an event being written.
        """
        try:
            record = {"ts": now_iso(), "kind": kind, **data}
            line = self._scrub(json.dumps(record, ensure_ascii=False, default=str))
            with self.events_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
