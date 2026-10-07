from datetime import UTC, datetime, timedelta
from threading import Lock
from time import monotonic

SEED_TIME = datetime(2026, 10, 7, 9, tzinfo=UTC)


class Clock:
    def __init__(self, controlled=False):
        self.controlled = controlled
        self.lock = Lock()
        self.set(SEED_TIME)

    def now(self):
        with self.lock:
            return (
                self.base
                if self.controlled
                else self.base + timedelta(seconds=monotonic() - self.started)
            )

    def set(self, value):
        with self.lock:
            self.base = value.astimezone(UTC)
            self.started = monotonic()

    def advance(self, seconds):
        with self.lock:
            self.base += timedelta(seconds=seconds)
