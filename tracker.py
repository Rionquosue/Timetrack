"""Session state, transitions, totals and timing. No UI code."""
import time
from datetime import datetime, timedelta

import storage
from models import OFF, ON, Segment

GAP_SECONDS = 5.0  # wall-clock vs elapsed discrepancy that is flagged for review


class Tracker:
    def __init__(self, base):
        self.base = base
        self.lock = None
        self.reset()

    def reset(self):
        if self.lock:
            self.lock.release()
        self.lock = self.path = self.state = self.pending = None
        self.sid, self.label = "", ""
        self.segments, self.gaps = [], []
        self.tot = {ON: 0.0, OFF: 0.0}
        self._w0 = self._wr0 = self._m0 = None  # segment start: timeline, real wall clock, monotonic

    def start(self):
        if self.state or self.pending:
            raise storage.StorageError("A session is already running.")
        self.reset()
        temp = storage.ensure_temp(self.base)
        self.sid, self.path = storage.new_session_file(temp)
        self.lock = storage.SessionLock(self.path)
        if not self.lock.acquire():
            raise storage.StorageError("Could not acquire the session lock.")
        self.state = ON
        self._w0 = self._wr0 = datetime.now().astimezone()
        self._m0 = time.monotonic()

    def toggle(self):
        self._close(False)

    def end(self):
        self._close(True)

    def _close(self, final):
        """Freeze the current segment at one captured boundary, then persist it."""
        if self.pending:
            return self.retry()
        if self.state is None:
            raise storage.StorageError("No session is running.")
        m = time.monotonic()
        now = datetime.now().astimezone()
        edge = self._w0 + timedelta(seconds=m - self._m0)  # elapsed time is authoritative
        seg = Segment(self.sid, len(self.segments) + 1, self._w0, edge, self.state, self.label, final)
        drift = (now - self._wr0).total_seconds() - (m - self._m0)
        if abs(drift) > GAP_SECONDS:  # sleep or clock change: flag, never silently assign
            self.gaps.append((seg.segment_id, drift))
        self.pending = (seg, edge, m, now)
        self.retry()

    def retry(self):
        """Write the frozen segment, then start the next one from the same boundary."""
        if not self.pending:
            return
        seg, edge, m, now = self.pending
        storage.append_segment(self.path, seg)  # raises StorageError; pending is kept
        self.pending = None
        self.segments.append(seg)
        self.tot[seg.state] += seg.duration
        if seg.session_end:
            self.state = None
        else:
            self.state = OFF if seg.state == ON else ON
            self._w0, self._wr0, self._m0, self.label = edge, now, m, ""

    def elapsed(self):
        if self.pending:
            return self.pending[0].duration
        return time.monotonic() - self._m0 if self.state else 0.0

    def totals(self):
        t = dict(self.tot)
        if self.state:
            t[self.state] += self.elapsed()
        return t[ON], t[OFF]
