"""When a run is over.

The rule is that the last window closing ends it, and only that: a run nobody
has opened yet stays up, and a window replaced by the next step is not a window
that closed.  Ending it is a signal and a hard exit, so what is tested here is
the decision rather than the act.
"""

import claims_processor.lifetime as lifetime
from claims_processor.lifetime import Lifetime


def open_window(life: Lifetime):
    """A page arriving: the stream is held open from its first frame.

    Hold on to what this returns for as long as the window is meant to be
    open - dropping it closes the stream, which is exactly what a closed
    window does.
    """
    stream = life.stream()
    next(stream)
    return stream


def close(window) -> None:
    window.close()  # what the server does when the socket is gone


def test_a_run_nobody_has_opened_yet_is_not_over():
    """Started with --no-browser and left alone: it stays up, as it always did."""
    assert not Lifetime().is_over()


def test_a_run_with_a_window_open_is_not_over(monkeypatch):
    monkeypatch.setattr(lifetime, "GRACE_SECONDS", 0)
    life = Lifetime()
    window = open_window(life)  # noqa: F841 - holding it open is the point
    assert not life.is_over()


def test_a_run_is_over_once_its_last_window_closes(monkeypatch):
    monkeypatch.setattr(lifetime, "GRACE_SECONDS", 0)
    life = Lifetime()
    close(open_window(life))
    assert life.is_over()


def test_a_window_still_within_the_grace_has_not_ended_the_run():
    life = Lifetime()
    close(open_window(life))
    assert not life.is_over()  # the real grace has not elapsed


def test_a_window_replaced_by_the_next_step_keeps_the_run(monkeypatch):
    monkeypatch.setattr(lifetime, "GRACE_SECONDS", 0)
    life = Lifetime()
    close(open_window(life))
    next_step = open_window(life)  # noqa: F841 - holding it open is the point
    assert not life.is_over()


def test_the_run_lasts_as_long_as_any_window_does(monkeypatch):
    """Two tabs on the same claim: closing one is not closing the wizard."""
    monkeypatch.setattr(lifetime, "GRACE_SECONDS", 0)
    life = Lifetime()
    first, second = open_window(life), open_window(life)
    close(first)
    assert not life.is_over()
    close(second)
    assert life.is_over()


def test_an_open_stream_lets_go_once_the_run_is_ending(monkeypatch):
    """Otherwise closing the server would hang on the way out.

    Coming down means joining the threads still serving requests, and a page's
    stream sits waiting out its next pulse before it looks at anything else -
    so the streams are released first, and this is what says they were.
    """
    monkeypatch.setattr(lifetime, "GRACE_SECONDS", 0)
    life = Lifetime()
    window = life.stream()
    next(window)  # attached, and now waiting for its next pulse

    lifetime._stopping.set()
    try:
        assert list(window) == []  # it ends, rather than pulsing on
        assert life.is_over()      # and it let go on its way out
    finally:
        lifetime._stopping.clear()  # process-wide, so put it back for the rest


def test_the_stream_opens_by_saying_how_soon_to_come_back():
    """A dropped stream must reconnect well inside the grace, not after it."""
    frame = next(Lifetime().stream())
    assert frame == b"retry: %d\n\n" % lifetime.RECONNECT_MS
    assert lifetime.RECONNECT_MS / 1000 < lifetime.GRACE_SECONDS
