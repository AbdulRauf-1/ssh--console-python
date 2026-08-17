"""Captures a terminal's output as TIMED events, so a replay can play the session
back over time (what was run, and for how long) rather than just showing the final
screen. It keeps up to ``cap`` bytes of output; the full byte count is tracked so
truncation is visible.

The serialized shape is identical to the Go original, so the replay player is the
same JavaScript:

    {"v":1,"duration":ms,"truncated":bool,"events":[[tMs,"base64(chunk)"],...]}

Data is base64-encoded so arbitrary terminal bytes stay valid inside JSON.
"""

from __future__ import annotations

import base64
import json
import time


class TermRecorder:
    def __init__(self, cap: int):
        self._start = time.monotonic()
        self._events: list[tuple[int, bytes]] = []
        self._byte_len = 0
        self._n = 0
        self._cap = cap
        self._truncated = False

    def write(self, p: bytes) -> None:
        self._n += len(p)
        if self._byte_len + len(p) <= self._cap:
            t_ms = int((time.monotonic() - self._start) * 1000)
            self._events.append((t_ms, bytes(p)))
            self._byte_len += len(p)
        else:
            self._truncated = True

    def result(self) -> tuple[int, str]:
        """Return the total output byte count and the recording as JSON."""
        duration = self._events[-1][0] if self._events else 0
        events = [[t, base64.standard_b64encode(d).decode("ascii")] for t, d in self._events]
        payload = {
            "v": 1,
            "duration": duration,
            "truncated": self._truncated,
            "events": events,
        }
        return self._n, json.dumps(payload, separators=(",", ":"))
