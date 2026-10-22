"""The only module that touches disk: append, recovery, ownership lock, CSV export."""
import csv
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from models import NAMES, Segment, fmt

CSV_HEADER = ["Session ID", "Segment ID", "Start", "End", "Duration Seconds", "State", "Label"]


class StorageError(Exception):
    pass


class PartialTail(StorageError):
    """The file ends with an incomplete line; repair_tail() must run before appending."""


# ---------- session files ----------

def ensure_temp(base):
    temp = Path(base) / "TEMP"
    try:
        temp.mkdir(exist_ok=True)
        probe = temp / f".probe_{uuid.uuid4().hex[:8]}"
        probe.touch()
        probe.unlink()
    except OSError as e:
        raise StorageError(f"Cannot write to the TEMP folder:\n{temp}\n\n{e}") from e
    return temp


def new_session_file(temp):
    sid = str(uuid.uuid4())
    path = temp / f"session_{datetime.now():%Y%m%d_%H%M%S}_{sid}.jsonl"
    try:
        open(path, "xb").close()  # exclusive creation
    except OSError as e:
        raise StorageError(f"Cannot create session file:\n{path}\n\n{e}") from e
    return sid, path


def _last_record(path):
    try:
        size = path.stat().st_size
        if not size:
            return {}
        with open(path, "rb") as f:
            f.seek(size - 1)
            if f.read(1) != b"\n":
                raise PartialTail(f"{path.name} ends with a partial line.")
            # Read backwards until the complete last record is available.
            pos, chunks = size - 1, []
            while pos:
                count = min(pos, 65536)
                pos -= count
                f.seek(pos)
                chunk = f.read(count)
                if b"\n" in chunk:
                    chunks.append(chunk.rsplit(b"\n", 1)[-1])
                    break
                chunks.append(chunk)
            data = b"".join(reversed(chunks)) + b"\n"
    except OSError as e:
        raise StorageError(f"Cannot read {path}: {e}") from e
    if not data.endswith(b"\n"):
        raise PartialTail(f"{path.name} ends with a partial line.")
    try:
        rec = json.loads(data.rstrip(b"\n").rsplit(b"\n", 1)[-1])
    except ValueError:
        raise StorageError(f"{path.name} has a damaged last record.") from None
    return rec if isinstance(rec, dict) else {}


def append_segment(path, seg):
    """Central writer: one completed segment, one line, flushed. Safe to retry."""
    last = _last_record(path)
    if last.get("session_id") == seg.session_id and last.get("segment_id") == seg.segment_id:
        if last != json.loads(seg.to_json()):
            raise StorageError("Conflicting data for an existing segment ID.")
        return  # an earlier attempt already landed
    try:
        with open(path, "ab") as f:
            f.write((seg.to_json() + "\n").encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        raise StorageError(f"Could not write to:\n{path}\n\n{e}") from e


def repair_tail(path):
    """Explicit repair: finish a complete-but-unterminated record, else drop the partial line."""
    try:
        data = path.read_bytes()
        if not data or data.endswith(b"\n"):
            return
        cut = data.rfind(b"\n") + 1
        try:
            Segment.from_json(data[cut:].decode("utf-8"))
            with open(path, "ab") as f:
                f.write(b"\n")
        except ValueError:
            with open(path, "r+b") as f:
                f.truncate(cut)
    except OSError as e:
        raise StorageError(f"Could not repair {path}: {e}") from e


def load_segments(path):
    """Return (segments, truncated). Damage mid-file raises; a bad tail returns the intact prefix."""
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        raise StorageError(f"Cannot read {path}: {e}") from e
    *lines, tail = data.split(b"\n")
    segs, truncated = [], False
    for n, raw in enumerate(lines, 1):
        try:
            segs.append(Segment.from_json(raw.decode("utf-8")))
        except ValueError as e:
            if n == len(lines) and not tail:  # malformed final line
                truncated = True
                break
            raise StorageError(f"{Path(path).name}: line {n} is damaged ({e}).") from None
    if tail:
        try:
            segs.append(Segment.from_json(tail.decode("utf-8")))
        except ValueError:
            truncated = True
    return segs, truncated


# ---------- ownership ----------

class SessionLock:
    """OS lock on a session file, held for the life of the owning process/session."""

    def __init__(self, path):
        self.path, self.f = path, None

    def acquire(self):
        if self.f is not None:
            return True
        f = None
        try:
            f = open(self.path, "rb", buffering=0)
            if os.name == "nt":
                import msvcrt
                f.seek(1 << 30)  # lock a byte far past EOF so appends are never blocked
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if f:
                f.close()
            return False
        self.f = f
        return True

    def release(self):
        if self.f:
            self.f.close()
            self.f = None


def list_sessions(temp):
    """Session files not owned by any running instance (including this one)."""
    if not Path(temp).is_dir():
        return []
    out = []
    for p in sorted(Path(temp).glob("session_*.jsonl")):
        lock = SessionLock(p)
        if lock.acquire():
            lock.release()
            out.append(p)
    return out


def describe(path):
    try:
        segs, truncated = load_segments(path)
    except StorageError as e:
        return f"{path.name}: unreadable ({e})"
    if not segs:
        return f"{path.name}: empty" + (" (damaged tail)" if truncated else "")
    total = sum(s.duration for s in segs)
    return f"{path.name}: {len(segs)} segments, {fmt(total)}" + (" (damaged tail)" if truncated else "")


def remove_session(path):
    os.remove(path)


# ---------- CSV ----------

def _safe(text):
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def export_csv(segs, dest):
    """Write to a staging file beside dest, then replace dest in one step."""
    dest = Path(dest)
    tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(CSV_HEADER)
            for i, s in enumerate(segs, 1):
                w.writerow([
                    s.session_id, i,
                    s.start.isoformat(timespec="milliseconds"),
                    s.end.isoformat(timespec="milliseconds"),
                    f"{s.duration:.3f}", NAMES[s.state], _safe(s.label),
                ])
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
