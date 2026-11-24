from collector.timeutil import iso, latency_minutes, now, parse_iso


def test_now_has_timezone():
    assert iso(now()).endswith("+07:00")


def test_parse_youtube_z_format():
    dt = parse_iso("2026-11-24T08:00:00Z")
    assert dt is not None and dt.tzinfo is not None


def test_parse_without_tz_assumes_bangkok():
    assert iso(parse_iso("2026-11-24 15:00:00")) == "2026-11-24T15:00:00+07:00"


def test_parse_garbage_returns_none():
    assert parse_iso("ไม่ใช่วันที่") is None
    assert parse_iso(None) is None


def test_latency_across_timezones():
    assert latency_minutes("2026-11-24T08:00:00Z", "2026-11-24T15:10:00+07:00") == 10