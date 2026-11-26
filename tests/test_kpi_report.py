from collector.config import NewsConfig
from scripts.kpi_report import dist, main
from tests.test_news import SITE, FakeSite


def test_dist():
    d = dist([2, 4, 6, 20], target=15)
    assert d["median"] == 5 and d["p95"] == 20 and d["pct_within_target"] == 75.0


def test_end_to_end_from_real_collector_output(tmp_path):
    from collector.news import NewsCollector

    fake = FakeSite()
    nc = NewsCollector(NewsConfig(), [SITE], fake, state_dir=tmp_path / "state", data_dir=tmp_path / "data")
    nc.run_site(SITE)                            # รอบแรก = backfill 2 ข่าว
    fake.ids, fake.etag = [3, 4, 1, 2], "v2"
    nc.run_site(SITE)                            # ข่าวใหม่ 2 ข่าว

    r = main(["--root", str(tmp_path), "--out", str(tmp_path / "rep")])
    n = r["news"]["t"]
    assert n["records"] == 4 and n["backfill"] == 2
    assert n["freshness_min"]["n"] == 2                       # ไม่นับ backfill
    assert n["coverage"] == {"appeared_on_listing": 4, "collected": 4, "gave_up": 0,
                             "still_pending": 0, "pct": 100.0}
    assert r["rounds"]["news/t"]["rounds"] == 2
    assert r["rounds"]["news/t"]["pct_rounds_shorter_than_interval"] == 100.0
    assert (tmp_path / "rep" / "kpi.md").read_text(encoding="utf-8").startswith("# KPI Report")


def test_window_filter(tmp_path):
    from collector.news import NewsCollector

    nc = NewsCollector(NewsConfig(), [SITE], FakeSite(), state_dir=tmp_path / "state",
                       data_dir=tmp_path / "data")
    nc.run_site(SITE)
    r = main(["--root", str(tmp_path), "--out", str(tmp_path / "rep"), "--since", "2099-01-01T00:00:00+07:00"])
    assert r["news"]["t"]["records"] == 0 and r["rounds"] == {}


def test_requests_per_domain_per_minute(tmp_path):
    import json
    d = tmp_path / "logs" / "news" / "x"
    d.mkdir(parents=True)
    rows = [{"ts": "2026-11-25T22:00:05+07:00", "domain": "a.com", "ok": True},
            {"ts": "2026-11-25T22:00:50+07:00", "domain": "a.com", "ok": False},
            {"ts": "2026-11-25T22:01:10+07:00", "domain": "a.com", "ok": True}]
    (d / "requests.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    q = main(["--root", str(tmp_path), "--out", str(tmp_path / "rep")])["requests"]["a.com"]
    assert q == {"requests": 3, "success_rate_pct": 66.67, "avg_per_active_min": 1.5, "max_per_min": 2}