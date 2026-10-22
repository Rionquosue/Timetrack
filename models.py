"""Segment model, validation, and in-memory review edits."""
import json
import math
from dataclasses import dataclass, replace
from datetime import datetime

ON, OFF = "on_task", "off_task"
NAMES = {ON: "On task", OFF: "Off task"}


def fmt(seconds):
    """HH:MM:SS; hours may exceed 24."""
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


@dataclass
class Segment:
    session_id: str
    segment_id: int
    start: datetime
    end: datetime
    state: str
    label: str = ""
    session_end: bool = False

    @property
    def duration(self):
        return (self.end - self.start).total_seconds()

    def to_json(self):
        return json.dumps(
            {
                "schema_version": 1,
                "session_id": self.session_id,
                "segment_id": self.segment_id,
                "start": self.start.isoformat(),
                "end": self.end.isoformat(),
                "duration_seconds": self.duration,
                "state": self.state,
                "label": self.label,
                "session_end": self.session_end,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, line):
        """Parse and validate one JSONL record; raises ValueError if invalid."""
        try:
            d = json.loads(line)
            if (not isinstance(d, dict) or type(d.get("schema_version")) is not int
                    or d["schema_version"] != 1 or d.get("state") not in (ON, OFF)
                    or not isinstance(d.get("label"), str)
                    or not isinstance(d.get("session_id"), str) or not d["session_id"]
                    or type(d.get("segment_id")) is not int or d["segment_id"] < 1
                    or type(d.get("session_end")) is not bool):
                raise ValueError("unsupported or malformed fields")
            s = cls(
                str(d["session_id"]),
                int(d["segment_id"]),
                datetime.fromisoformat(d["start"]),
                datetime.fromisoformat(d["end"]),
                d["state"],
                d["label"],
                bool(d["session_end"]),
            )
            if s.start.tzinfo is None or s.end.tzinfo is None or s.end < s.start:
                raise ValueError("bad timestamps")
            duration = d.get("duration_seconds")
            if (type(duration) not in (int, float) or not math.isfinite(duration)
                    or duration < 0 or not math.isclose(duration, s.duration, abs_tol=0.000001)):
                raise ValueError("invalid duration")
            return s
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(str(e)) from None


def split(segs, i, at):
    """Split segs[i] at `at`; both halves must keep a nonzero duration."""
    s = segs[i]
    if not s.start < at < s.end:
        raise ValueError("The split time must fall strictly inside the segment.")
    segs[i:i + 1] = [replace(s, end=at, session_end=False), replace(s, start=at)]


def move_boundary(segs, i, at):
    """Move the boundary shared by segs[i] and segs[i+1]; no gaps or overlaps result."""
    if i + 1 >= len(segs):
        raise ValueError("Select a segment that is followed by another segment.")
    a, b = segs[i], segs[i + 1]
    if not a.start < at < b.end:
        raise ValueError("The boundary must stay inside the two neighbouring segments.")
    a.end = b.start = at
