"""Event-controlled scheduling exercises real worker threads without interval sleeps."""

import queue
import threading

import pytest


class ControlledStop:
    def __init__(self):
        self.ticks = queue.Queue()
        self.stopped = False

    def wait(self, seconds):
        assert seconds == 30
        self.ticks.get(timeout=5)
        return self.stopped

    def set(self):
        self.stopped = True
        self.ticks.put(None)


def test_scheduler_idempotence_close_and_exception_continuation(prepared, monkeypatch, caplog):
    stop = ControlledStop()
    prepared._stop = stop
    observed = queue.Queue()
    original = prepared.checkpoint
    calls = 0

    def checkpoint():
        nonlocal calls
        calls += 1
        observed.put(calls)
        if calls == 1:
            raise OSError("injected checkpoint failure")
        return original()

    monkeypatch.setattr(prepared, "checkpoint", checkpoint)
    assert prepared.start() is prepared
    worker = prepared._worker
    assert prepared.start() is prepared
    assert prepared._worker is worker
    for expected in (1, 2):
        stop.ticks.put(None)
        assert observed.get(timeout=5) == expected
    prepared.close()
    assert not worker.is_alive()
    assert prepared.last_checkpoint_status.state.value == "UNLICENSED"
    assert "license checkpoint failed" in caplog.text
    assert "license checkpoint restricted" in caplog.text
    prepared.close()
    assert calls == 2
    with pytest.raises(ValueError, match="closed"):
        prepared.start()


def test_close_waits_for_checkpoint_without_runtime_mutex_deadlock(prepared, monkeypatch):
    stop = ControlledStop()
    prepared._stop = stop
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    original = prepared.checkpoint

    def checkpoint():
        with prepared._lock:
            entered.set()
            assert release.wait(5)
            return original()

    monkeypatch.setattr(prepared, "checkpoint", checkpoint)
    prepared.start()
    stop.ticks.put(None)
    assert entered.wait(5)
    thread = threading.Thread(target=lambda: (prepared.close(), closed.set()))
    thread.start()
    release.set()
    assert closed.wait(5)
    thread.join(timeout=5)
    assert not thread.is_alive()
