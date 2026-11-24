import json

from collector.storage import (SeenStore, append_jsonl, atomic_write_json,
                               load_json, read_jsonl, round_id)


def test_round_id_is_unique_per_second_and_sortable():
    rid = round_id()
    assert len(rid.split("_")) == 3


def test_append_keeps_thai_readable(tmp_path):
    p = tmp_path / "a" / "b" / "x.jsonl"           # โฟลเดอร์ยังไม่มี
    append_jsonl(p, [{"title": "ข่าว"}])
    append_jsonl(p, [{"title": "ข่าว 2"}])
    text = p.read_text(encoding="utf-8")
    assert "ข่าว" in text                           # ไม่ใช่ \u0e02...
    assert len(text.strip().splitlines()) == 2      # ต่อท้าย ไม่ทับ


def test_read_jsonl_marks_broken_line(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"a": 1}\n{"a": 2\n{"a": 3}\n', encoding="utf-8")
    rows = list(read_jsonl(p))
    assert [r for _, r in rows] == [{"a": 1}, None, {"a": 3}]


def test_atomic_write_and_load(tmp_path):
    p = tmp_path / "s.json"
    assert load_json(p, {}) == {}
    atomic_write_json(p, {"k": "ค่า"})
    assert load_json(p, {}) == {"k": "ค่า"}
    assert not list(tmp_path.glob("*.tmp"))         # ไม่มีไฟล์ชั่วคราวค้าง


def test_seen_store_persists_and_expires(tmp_path):
    p = tmp_path / "seen.json"
    s = SeenStore(p)
    s.add("https://a.com/1")
    s.data["https://a.com/old"] = "2000-01-01T00:00:00+07:00"   # ของเก่ามาก
    s.save()

    s2 = SeenStore(p)                               # จำลองการเปิดบอทใหม่
    assert "https://a.com/1" in s2
    assert "https://a.com/old" not in s2            # ถูกลบตาม TTL