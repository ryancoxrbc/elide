"""End the terminal run when the browser window closes.

The wizard is one window driving one process: leaving the server running after
the window has gone only strands a port and a terminal.  Every page holds an
event stream open for as long as it is on screen, so what is counted here is
real windows rather than polling.  A tab left in the background is still
attached - its timers may be throttled, its socket is not - and a window that
is closed, or a browser that is quit outright, drops the connection whether or
not the page got the chance to say so.

Only the last window ending the run is a decision; the first one starting it is
not.  Nothing happens until a page has actually connected, so a server started
with ``--no-browser`` and left alone stays up, as it always did.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time

# How often the open page is written to. Discovering that a window has gone
# means writing to its socket and failing - which takes two writes, since the
# first only fills a buffer nobody is reading - so this sets how quickly a
# closed window is noticed.
PULSE_SECONDS = 1.0

# Moving between steps closes one connection and opens the next. This is how
# long the replacement has to arrive before the run is taken to be over; it
# only has to cover rendering a page the server has already sent, and usually
# the next window is attached before the last one is even missed.
GRACE_SECONDS = 3.0

# Long enough for the reply to reach the page that asked to be finished, so it
# can put its closing message up before the server that served it goes away.
FAREWELL_SECONDS = 0.5

# A backstop, in case the shutdown below finds something that will not let go.
# Reaching it means something is wrong; hanging on to the terminal is worse.
SHUTDOWN_SECONDS = 5.0

# What a page waits before re-opening a stream that dropped. Browsers default
# to about three seconds, which is the whole of the grace above; saying so
# outright keeps a hiccup from reading as a closed window.
RECONNECT_MS = 1000

# Set once the run is ending, so every open stream lets go at once. The server
# joins its request threads on the way down, and a stream still waiting out its
# next pulse would hold the whole shutdown up for that long.
_stopping = threading.Event()


class Lifetime:
    """The browser windows attached to this run, and the end of it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._open = 0
        self._connected = False  # has any window ever been here?
        self._empty_since: float | None = None
        self._server = None
        self._watching = False

    # ------------------------------------------------------------ the page

    def stream(self):
        """The event stream one page holds open, until that page goes away.

        After the opening line the frames are comments rather than events:
        nothing is being told to the page, and the write itself is the whole
        point of sending them.
        """
        self._attach()
        try:
            yield b"retry: %d\n\n" % RECONNECT_MS
            while not _stopping.wait(PULSE_SECONDS):
                yield b": open\n\n"
        finally:
            self._detach()

    def _attach(self) -> None:
        with self._lock:
            self._open += 1
            self._connected = True
            self._empty_since = None

    def _detach(self) -> None:
        with self._lock:
            self._open = max(0, self._open - 1)
            if not self._open:
                self._empty_since = time.monotonic()

    def is_over(self) -> bool:
        """Has the last window been gone long enough to call the run finished?"""
        with self._lock:
            if not self._connected or self._open or self._empty_since is None:
                return False
            return time.monotonic() - self._empty_since >= GRACE_SECONDS

    # ------------------------------------------------------------- the end

    def serve(self, server, announce=None) -> None:
        """Serve until the last window closes, then come back.

        The server is run from here rather than by ``app.run`` so that there is
        something to call ``shutdown`` on. Signalling the process would be the
        other way, and it is the less dependable one: whether an interrupt can
        reach a program at all is decided by whoever launched it.
        """
        self._server = server
        if not self._watching:
            self._watching = True
            threading.Thread(target=self._wait, args=(announce,), daemon=True).start()
        try:
            signal.signal(signal.SIGINT, _on_interrupt)
        except ValueError:
            pass  # not the main thread, so not ours to handle
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass  # Ctrl-C; the server catches its own, so this is a backstop
        finally:
            server.server_close()

    def _wait(self, announce) -> None:
        while not self.is_over():
            time.sleep(0.5)
        self.stop(announce)

    def finish(self, announce=None) -> None:
        """End the run now, because the page said to rather than because it left.

        Letting go of the stream would end it a few seconds later anyway; this
        is what makes the Close button feel like a button.
        """

        def go() -> None:
            time.sleep(FAREWELL_SECONDS)  # let the reply reach the page first
            self.stop(announce)

        threading.Thread(target=go, daemon=True).start()

    def stop(self, announce=None) -> None:
        """Bring the run down. Safe to call twice; the second call does nothing.

        Never called from the serving thread itself - ``shutdown`` waits for
        that thread to come out of its loop, so being that thread would wait
        for ever.
        """
        if _stopping.is_set():
            return
        _stopping.set()  # every open stream lets go, so nothing holds this up
        if announce is not None:
            announce()
        sys.stdout.flush()
        sys.stderr.flush()
        threading.Thread(target=_insist, daemon=True).start()
        if self._server is not None:
            self._server.shutdown()


def _on_interrupt(signum, frame) -> None:
    """Ctrl-C, with the streams let go first.

    Closing the server joins the threads still serving requests, and a page's
    stream waits out its next pulse before looking at anything else - so
    without this the interrupt would be caught, and then hang on the way out.
    """
    _stopping.set()
    raise KeyboardInterrupt


def _insist() -> None:
    """The backstop: leave anyway if the shutdown does not finish."""
    time.sleep(SHUTDOWN_SECONDS)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
