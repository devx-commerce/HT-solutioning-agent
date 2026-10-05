"""One structured log line per event, in the shape Cloud Logging reads.

Each event is a JSON line on stdout. Cloud Run turns it into a log entry
whose summary is the message ("brief.queued client=Decathlon ...") and whose
fields can be filtered on in Logs Explorer, for example
jsonPayload.event="build.done" or jsonPayload.thread_id="1a10a4873577a16b".
The pipeline logs the same way (app/logs.py).
"""

from __future__ import annotations

import json
import sys


def event(name: str, severity: str = "INFO", **fields) -> None:
    fields = {k: v for k, v in fields.items() if v is not None and v != ""}
    summary = " ".join(f"{k}={_short(v)}" for k, v in fields.items())
    line = {"severity": severity, "message": f"{name} {summary}".strip(), "event": name, **fields}
    print(json.dumps(line, default=str), file=sys.stdout, flush=True)


def _short(value) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= 60 else text[:57] + "..."
