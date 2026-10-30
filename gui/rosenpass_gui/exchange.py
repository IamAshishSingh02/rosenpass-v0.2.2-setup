"""Popen orchestration for the long-running `rosenpass exchange` daemon."""

from __future__ import annotations

import atexit
import queue
import re
import subprocess
import threading
from collections import deque
from pathlib import Path

# The daemon prints one line per PSK production, on stdout, on purpose
# (rosenpass/src/app_server.rs: handle_out_key): e.g.
#   output-key peer <b64-peer-id> key-file "/path/to/psk.out" exchanged
# The path comes from Rust's Debug formatting, so it is usually quoted.
OUTPUT_KEY_RE = re.compile(
    r"^output-key peer (?P<peer>\S+) key-file (?P<q>\"?)(?P<file>.*?)(?P=q) "
    r"(?P<why>exchanged|stale)\s*$"
)

ERR_TAIL_LINES = 50  # bounded stderr kept for error reporting


class ExchangeProcess:
    """Runs `rosenpass exchange`, parses its stdout, and stops it cleanly.

    Reader threads only ever push into a thread-safe queue; the Tk main
    thread drains it via ``drain()`` from a ``root.after`` loop.
    """

    def __init__(self, cmd: list[str]):
        self.cmd = cmd
        self.proc = None
        self._queue: queue.Queue = queue.Queue()
        self._stderr_tail: deque[str] = deque(maxlen=ERR_TAIL_LINES)
        self._readers: list[threading.Thread] = []

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        self.proc = subprocess.Popen(  # noqa: S603 - cmd is built from profile fields
            self.cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._readers = [
            threading.Thread(target=self._pump, args=(stream, tag), daemon=True)
            for stream, tag in (
                (self.proc.stdout, "stdout"),
                (self.proc.stderr, "stderr"),
            )
        ]
        for t in self._readers:
            t.start()
        atexit.register(self.stop)

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        """terminate(), then kill() after a 3 s grace period."""
        atexit.unregister(self.stop)
        if not self.is_alive():
            self.proc = None
            return
        proc, self.proc = self.proc, None
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
        self._queue.put(("exited", ""))

    # -- output --------------------------------------------------------------

    def drain(self) -> list[tuple]:
        """Return and clear all pending events.

        Event tuples: ("stdout", line), ("stderr", line),
        ("output-key", file_path, why), ("exited", "").
        """
        events = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                return events

    def stderr_tail(self) -> str:
        return "".join(self._stderr_tail)

    # -- internals -----------------------------------------------------------

    def _pump(self, stream, tag: str) -> None:
        for line in stream:
            line = line.rstrip("\n")
            if tag == "stderr":
                self._stderr_tail.append(line + "\n")
                self._queue.put((tag, line))
                continue
            m = OUTPUT_KEY_RE.match(line)
            if m:
                self._queue.put(("output-key", Path(m.group("file")), m.group("why")))
            self._queue.put((tag, line))
        # EOF: let the app notice via is_alive()/poll
        self._queue.put(("exited", ""))
