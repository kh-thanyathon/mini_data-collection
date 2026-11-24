import os
from .timeutil import now
def round_id(dt=None) -> str:
    """เช่น 2026-11-24_185200_12345"""
    dt = dt or now()
    return f"{dt:%Y-%m-%d_%H%M%S}_{os.getpid()}"

import json
from pathlib import Path
def append_jsonl(path: Path, records: list[dict]) -> int:
    if not records:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(records)

def read_jsonl(path: Path):
    """อ่านทีละบรรทัด บรรทัดที่เสียคืน None (ให้ quality report นับเป็น malformed)"""
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield line_no, json.loads(line)
            except json.JSONDecodeError:
                yield line_no, None

import tempfile
def atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)

def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default

from datetime import timedelta
from .timeutil import parse_iso


class SeenStore:
    def __init__(self, path: Path, ttl_days: int = 14):
        self.path = path
        self.ttl = timedelta(days=ttl_days)
        self.data: dict[str, str] = load_json(path, {})

    def __contains__(self, key: str) -> bool:
        return key in self.data

    def add(self, key: str) -> None:
        self.data[key] = now().isoformat(timespec="seconds")

    def save(self) -> None:
        cutoff = now() - self.ttl
        self.data = {k: v for k, v in self.data.items()
                     if (parse_iso(v) or now()) >= cutoff}
        atomic_write_json(self.path, self.data)