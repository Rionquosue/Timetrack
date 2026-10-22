"""Deterministic regression tests for capture, recovery and export failures."""
import csv
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import storage
from models import ON, OFF, Segment, fmt, split, move_boundary
from tracker import Tracker

START = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def segment(**kwargs):
    fields = dict(session_id="test-session", segment_id=1, start=START,
                  end=START + timedelta(seconds=10), state=ON)
    fields.update(kwargs)
    return Segment(**fields)


class Models(unittest.TestCase):
    def test_unicode_roundtrip(self):
        s = segment(label='Résumé, "work"\nnext line', session_end=True)
        self.assertEqual(Segment.from_json(s.to_json()), s)

    def test_invalid_records(self):
        record = json.loads(segment().to_json())
        changes = [dict(schema_version=2), dict(schema_version=True), dict(state=[]),
                   dict(label=None), dict(session_id=""), dict(segment_id=True),
                   dict(segment_id=0), dict(segment_id=1.5), dict(session_end="false"),
                   dict(duration_seconds=-1), dict(duration_seconds=float('nan')),
                   dict(duration_seconds=11), dict(start="2026-10-21T12:00:00"),
                   dict(end="2026-10-20T12:00:00+00:00")]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                Segment.from_json(json.dumps(record | change))
        for raw in ['[]', 'null', '42', '{}', 'broken']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                Segment.from_json(raw)

    def test_duration_across_offsets(self):
        self.assertEqual(segment(end=datetime.fromisoformat('2026-10-21T08:01:00-04:00')).duration, 60)

    def test_format_over_day(self):
        self.assertEqual(fmt(90061.99), '25:01:01')

    def test_split_final_segment_preserves_only_last_end_marker(self):
        values = [segment(session_end=True, label='Task')]
        split(values, 0, START + timedelta(seconds=3))
        self.assertEqual([s.duration for s in values], [3, 7])
        self.assertEqual([s.session_end for s in values], [False, True])
        self.assertEqual([s.label for s in values], ['Task', 'Task'])

    def test_invalid_boundary_does_not_mutate(self):
        values = [segment(), segment(segment_id=2, start=START + timedelta(seconds=10),
                                    end=START + timedelta(seconds=20), state=OFF)]
        before = [s.to_json() for s in values]
        with self.assertRaises(ValueError):
            move_boundary(values, 0, START)
        self.assertEqual([s.to_json() for s in values], before)


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.path = self.base / 'session_test.jsonl'
        self.path.touch()

    def test_empty_recovery(self):
        self.assertEqual(storage.load_segments(self.path), ([], False))

    def test_missing_file_error(self):
        with self.assertRaises(storage.StorageError):
            storage.load_segments(self.base / 'missing')

    def test_long_unicode_label_and_retry(self):
        first = segment(label='é' * 70000)
        storage.append_segment(self.path, first)
        storage.append_segment(self.path, first)
        storage.append_segment(self.path, segment(segment_id=2))
        values, damaged = storage.load_segments(self.path)
        self.assertFalse(damaged)
        self.assertEqual(len(values), 2)
        self.assertEqual(values[0].label, first.label)

    def test_conflicting_id_is_rejected(self):
        storage.append_segment(self.path, segment())
        with self.assertRaises(storage.StorageError):
            storage.append_segment(self.path, segment(label='different'))

    def test_complete_unterminated_tail_repair(self):
        self.path.write_text(segment().to_json(), encoding='utf-8')
        storage.repair_tail(self.path)
        storage.append_segment(self.path, segment())
        self.assertEqual(len(self.path.read_bytes().splitlines()), 1)

    def test_invalid_utf8_tail_repair(self):
        storage.append_segment(self.path, segment())
        with self.path.open('ab') as f:
            f.write(b'\xff')
        self.assertTrue(storage.load_segments(self.path)[1])
        storage.repair_tail(self.path)
        self.assertFalse(storage.load_segments(self.path)[1])

    def test_fsync_failure_retry_does_not_duplicate(self):
        with patch('storage.os.fsync', side_effect=OSError('disk error')):
            with self.assertRaises(storage.StorageError):
                storage.append_segment(self.path, segment())
        storage.append_segment(self.path, segment())
        self.assertEqual(len(storage.load_segments(self.path)[0]), 1)

    def test_temp_failure_is_actionable(self):
        (self.base / 'TEMP').write_text('not a directory')
        with self.assertRaisesRegex(storage.StorageError, 'TEMP'):
            storage.ensure_temp(self.base)

    def test_unique_session_names(self):
        a, pa = storage.new_session_file(self.base)
        b, pb = storage.new_session_file(self.base)
        self.assertNotEqual(a, b)
        self.assertNotEqual(pa, pb)
        self.assertEqual(pa.stat().st_size, 0)

    def test_lock_reentrant_acquire_retains_ownership(self):
        first, second = storage.SessionLock(self.path), storage.SessionLock(self.path)
        self.addCleanup(first.release)
        self.addCleanup(second.release)
        self.assertTrue(first.acquire())
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        first.release()
        self.assertTrue(second.acquire())

    def test_export_failure_keeps_destination_and_capture(self):
        storage.append_segment(self.path, segment())
        dest = self.base / 'out.csv'
        dest.write_text('original')
        with patch('storage.os.replace', side_effect=OSError('permission denied')):
            with self.assertRaises(OSError):
                storage.export_csv([segment()], dest)
        self.assertEqual(dest.read_text(), 'original')
        self.assertEqual(len(storage.load_segments(self.path)[0]), 1)
        self.assertEqual(list(self.base.glob('*.tmp')), [])

    def test_csv_review_renumbers_split_rows(self):
        values = [segment(session_end=True)]
        split(values, 0, START + timedelta(seconds=3))
        dest = self.base / 'out.csv'
        storage.export_csv(values, dest)
        rows = list(csv.reader(io.StringIO(dest.read_text(encoding='utf-8-sig'))))
        self.assertEqual([row[1] for row in rows[1:]], ['1', '2'])
        self.assertEqual([row[4] for row in rows[1:]], ['3.000', '7.000'])

    def test_csv_formula_prefixes(self):
        for prefix in ('=', '+', '-', '@', '\t', '\r'):
            with self.subTest(prefix=prefix):
                self.assertTrue(storage._safe(prefix + 'value').startswith("'"))
        self.assertEqual(storage._safe(''), '')


class Timing(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.t = Tracker(Path(temp.name))
        self.addCleanup(self.t.reset)
        self.clock = patch('tracker.time.monotonic', return_value=100).start()
        self.addCleanup(patch.stopall)
        self.wall = patch('tracker.datetime').start()
        self.wall.now.return_value = START

    def advance(self, seconds, wall_seconds=None):
        self.clock.return_value = 100 + seconds
        self.wall.now.return_value = START + timedelta(seconds=seconds if wall_seconds is None else wall_seconds)

    def test_exact_totals_and_no_periodic_writes(self):
        self.t.start()
        self.advance(10)
        self.assertEqual(self.t.totals(), (10, 0))
        self.assertEqual(self.t.path.read_bytes(), b'')
        self.t.toggle()
        self.advance(25)
        self.t.end()
        self.assertEqual(self.t.totals(), (10, 15))
        self.assertEqual(self.t.segments[0].end, self.t.segments[1].start)

    def test_retry_time_belongs_to_next_state(self):
        self.t.start()
        self.advance(10)
        with patch('tracker.storage.append_segment', side_effect=storage.StorageError('full')):
            with self.assertRaises(storage.StorageError):
                self.t.toggle()
        self.advance(20)
        self.t.retry()
        self.assertEqual(self.t.totals(), (10, 10))
        self.t.retry()
        self.assertEqual(len(self.t.segments), 1)

    def test_failed_end_retry_excludes_dialog_time(self):
        self.t.start()
        self.advance(10)
        with patch('tracker.storage.append_segment', side_effect=storage.StorageError('full')):
            with self.assertRaises(storage.StorageError):
                self.t.end()
        self.advance(50)
        self.t.retry()
        self.assertIsNone(self.t.state)
        self.assertEqual(self.t.totals(), (10, 0))

    def test_clock_change_is_flagged(self):
        self.t.start()
        self.advance(10, -100)
        self.t.end()
        self.assertEqual(self.t.segments[0].duration, 10)
        self.assertEqual(self.t.gaps, [(1, -110)])

    def test_label_clears_after_toggle(self):
        self.t.start()
        self.t.label = 'Work'
        self.advance(10)
        self.t.toggle()
        self.assertEqual(self.t.segments[0].label, 'Work')
        self.assertEqual(self.t.label, '')

    def test_invalid_lifecycle(self):
        with self.assertRaises(storage.StorageError):
            self.t.end()
        self.t.start()
        path = self.t.path
        with self.assertRaises(storage.StorageError):
            self.t.start()
        self.assertEqual(self.t.path, path)

    def test_second_session_resets_totals(self):
        self.t.start()
        self.advance(10)
        self.t.end()
        previous = self.t.path
        self.t.start()
        self.assertEqual(self.t.totals(), (0, 0))
        self.assertNotEqual(self.t.path, previous)

    def test_lock_failure_blocks_tracking(self):
        with patch('tracker.storage.SessionLock.acquire', return_value=False):
            with self.assertRaises(storage.StorageError):
                self.t.start()
        self.assertIsNone(self.t.state)


if __name__ == '__main__':
    unittest.main()
