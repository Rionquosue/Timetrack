"""Headless checks for the acceptance criteria that don't need a display. Run: python -m unittest -v"""
import csv
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import storage
from models import OFF, ON, Segment, move_boundary, split
from tracker import Tracker

TZ = timezone(timedelta(hours=-4))


def seg(i, start, secs, state=ON, label="", end=False):
    s = datetime(2026, 10, 21, 14, tzinfo=TZ) + timedelta(seconds=start)
    return Segment("sid", i, s, s + timedelta(seconds=secs), state, label, end)


class Core(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.base = Path(self._d.name)
        self.addCleanup(self._d.cleanup)  # release tracker locks before deleting files on Windows

    def tracker(self):
        t = Tracker(self.base)
        self.addCleanup(t.reset)
        return t

    def test_flow_one_record_per_transition(self):
        t = self.tracker()
        t.start()
        self.assertTrue((self.base / "TEMP").is_dir())
        time.sleep(0.05)
        self.assertEqual(t.path.stat().st_size, 0)  # no writes before a toggle
        t.toggle(); t.toggle(); t.end()
        segs, damaged = storage.load_segments(t.path)
        self.assertFalse(damaged)
        self.assertEqual([s.state for s in segs], [ON, OFF, ON])
        self.assertEqual([s.segment_id for s in segs], [1, 2, 3])
        self.assertTrue(all(a.end == b.start for a, b in zip(segs, segs[1:])))
        self.assertEqual([s.session_end for s in segs], [False, False, True])
        self.assertIsNone(t.state)
        self.assertAlmostEqual(sum(t.totals()), sum(s.duration for s in segs), places=6)

    def test_write_failure_then_retry_no_loss_no_duplicate(self):
        t = self.tracker()
        t.start()
        time.sleep(0.02)
        real = storage.append_segment

        def boom(*a):
            raise storage.StorageError("disk full")
        storage.append_segment = boom
        try:
            with self.assertRaises(storage.StorageError):
                t.toggle()
        finally:
            storage.append_segment = real
        self.assertIsNotNone(t.pending)
        frozen = t.elapsed()
        time.sleep(0.02)
        self.assertEqual(t.elapsed(), frozen)  # timer frozen while unsaved
        t.retry(); t.retry()
        self.assertEqual(len(t.path.read_text("utf-8").splitlines()), 1)
        self.assertEqual(t.state, OFF)

    def test_append_is_idempotent(self):
        p = self.base / "s.jsonl"
        p.touch()
        s = seg(1, 0, 10)
        storage.append_segment(p, s); storage.append_segment(p, s)
        self.assertEqual(len(p.read_text().splitlines()), 1)

    def test_truncated_tail_recovery_and_repair(self):
        p = self.base / "s.jsonl"
        p.touch()
        storage.append_segment(p, seg(1, 0, 10))
        with open(p, "ab") as f:
            f.write(b'{"schema_ver')
        segs, damaged = storage.load_segments(p)
        self.assertEqual(len(segs), 1); self.assertTrue(damaged)
        with self.assertRaises(storage.PartialTail):
            storage.append_segment(p, seg(2, 10, 5, OFF))
        storage.repair_tail(p)
        storage.append_segment(p, seg(2, 10, 5, OFF))
        segs, damaged = storage.load_segments(p)
        self.assertEqual(len(segs), 2); self.assertFalse(damaged)

    def test_mid_file_damage_is_reported(self):
        p = self.base / "s.jsonl"
        a, b = seg(1, 0, 1).to_json(), seg(3, 2, 1).to_json()
        p.write_text(f"{a}\nnot json\n{b}\n", "utf-8")
        with self.assertRaises(storage.StorageError):
            storage.load_segments(p)

    def test_ownership_lock(self):
        t = Tracker(self.base)
        t.start()
        path, temp = t.path, self.base / "TEMP"
        self.assertEqual(storage.list_sessions(temp), [])  # active file is never offered
        t.reset()
        self.assertEqual(storage.list_sessions(temp), [path])

    def test_csv_safety_and_cleanup(self):
        segs = [seg(1, 0, 1.5, label='=HYPERLINK("x"), é'), seg(2, 1.5, 3661.25, OFF, "multi\nline"),
                seg(3, 3662.75, 1, ON, "")]
        dest = self.base / "out.csv"
        storage.export_csv(segs, dest)
        raw = dest.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.reader(raw.decode("utf-8-sig").splitlines(True)))
        self.assertEqual(rows[0], storage.CSV_HEADER)
        self.assertEqual(rows[1][6], '\'=HYPERLINK("x"), é')
        self.assertEqual(rows[2][6], "multi\nline")
        self.assertEqual(rows[2][4], "3661.250")
        self.assertEqual(rows[2][5], "Off task")
        self.assertEqual(rows[3][6], "")
        self.assertTrue(rows[1][2].endswith("-04:00"))
        self.assertEqual([p.name for p in self.base.iterdir() if p.name.endswith(".tmp")], [])

    def test_split_and_move(self):
        segs = [seg(1, 0, 10), seg(2, 10, 10, OFF, end=True)]
        at = segs[0].start + timedelta(seconds=4)
        split(segs, 0, at)
        self.assertEqual(len(segs), 3)
        self.assertTrue(all(a.end == b.start for a, b in zip(segs, segs[1:])))
        move_boundary(segs, 1, segs[1].start + timedelta(seconds=2))
        self.assertTrue(all(a.end == b.start for a, b in zip(segs, segs[1:])))
        with self.assertRaises(ValueError):
            split(segs, 0, segs[0].end)  # boundary itself is not a valid split point
        with self.assertRaises(ValueError):
            move_boundary(segs, 2, segs[2].start)  # last segment has no neighbour


if __name__ == "__main__":
    unittest.main()
