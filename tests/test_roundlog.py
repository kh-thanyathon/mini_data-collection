import json
from datetime import timedelta

from collector.roundlog import finish_round, latency_summary, percentile, requests_summary
from collector.timeutil import now


def test_percentile_nearest_rank():
    v = list(range(1, 101))                       # 1..100
    assert percentile(v, 50) == 50
    assert percentile(v, 95) == 95
    assert percentile([7], 95) == 7
    assert percentile([], 95) is None


def test_latency_summary():
    s = latency_summary([5, 10, 12, 20, 40], target_min=15)
    assert s["n"] == 5 and s["median_min"] == 12
    assert s["pct_within_target"] == 60.0          # 3 ใน 5 ข่าว <= 15 นาที
    assert s["p95_min"] == 40
    assert latency_summary([], 15) == {"n": 0, "target_min": 15}


def rec(ts, domain="a.com", ok=True):
    return {"ts": ts, "domain": domain, "status": 200 if ok else 503, "ok": ok}


def test_requests_summary_per_domain_and_minute():
    rs = [rec("2026-11-25T13:00:05+07:00"), rec("2026-11-25T13:00:40+07:00", ok=False),
          rec("2026-11-25T13:01:10+07:00"), rec("2026-11-25T13:00:10+07:00", domain="b.com")]
    s = requests_summary(rs)
    assert s["total"] == 4 and s["success_rate"] == 0.75
    assert s["per_domain"]["a.com"]["max_per_min"] == 2         # นาที 13:00 มี 2 ครั้ง
    assert s["per_domain"]["a.com"]["avg_per_active_min"] == 1.5
    assert s["per_domain"]["b.com"]["requests"] == 1


def test_finish_round_writes_log_and_requests(tmp_path):
    started = now() - timedelta(seconds=30)
    summary = {"round_id": "r1", "saved": 3, "backfill": 1, "errors": [],
               "latency_min": [4.0, 9.0], "duration_s": 30.0}
    log = finish_round("news/x", started, summary, [rec("2026-11-25T13:00:05+07:00")],
                       interval_min=5, latency_target_min=15, log_dir=tmp_path)
    saved = json.loads((tmp_path / "news" / "x" / "r1.json").read_text(encoding="utf-8"))
    assert saved == log
    assert log["items"] == {"saved": 3, "backfill": 1}            # ปริมาณข้อมูล
    assert log["duration_ok"] is True                              # 30 วิ < 300 วิ
    assert log["freshness"]["pct_within_target"] == 100.0
    assert log["requests"]["success_rate"] == 1.0
    lines = (tmp_path / "news" / "x" / "requests.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["round_id"] == "r1"