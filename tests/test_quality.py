import json

from collector.quality import check, dedup_key
from scripts.quality_report import main

GOOD_NEWS = {"site": "s", "article_id": "1", "url": "https://n.com/news_1", "title": "หัวข้อ",
             "published_at": "2026-11-25T21:37:33+07:00", "collected_at": "2026-11-25T21:40:00+07:00",
             "content": "เนื้อหาข่าวที่ยาวพอสมควร " * 5, "images": ["https://n.com/a.jpg"],
             "category": "การเมือง"}


def test_good_record_passes():
    assert check("news", GOOD_NEWS) == {"missing": [], "malformed": []}


def test_missing_fields():
    rec = {**GOOD_NEWS, "title": "  ", "images": [], "category": None}
    assert check("news", rec)["missing"] == ["title", "images", "category"]


def test_malformed_rules():
    rec = {**GOOD_NEWS, "published_at": "2026-11-25 21:37",          # ไม่มี T และ tz
           "url": "ftp://x", "content": "สั้น", "category": '["ข่าวด่วน"]'}
    m = check("news", rec)["malformed"]
    assert any(x.startswith("published_at") for x in m)
    assert "url: not http(s)" in m and any(x.startswith("content") for x in m)
    assert "category: looks like encoded list" in m


def test_future_published_and_negative_counts():
    assert "published_at after collected_at" in check(
        "news", {**GOOD_NEWS, "published_at": "2026-11-25T23:00:00+07:00"})["malformed"]
    v = {"video_id": "v", "view_count": -1, "duration": "PT1M", "duration_s": 60,
         "published_at": "2026-11-25T05:00:00Z", "collected_at": "2026-11-25T13:00:00+07:00"}
    assert "view_count: not a non-negative int" in check("yt_video", v)["malformed"]


def test_hidden_like_count_is_not_missing():
    v = {"video_id": "v", "view_count": 5, "like_count": None, "duration": "PT1M", "duration_s": 60,
         "published_at": "2026-11-25T05:00:00Z", "collected_at": "2026-11-25T13:00:00+07:00"}
    assert check("yt_video", v) == {"missing": [], "malformed": []}


def test_video_snapshots_in_different_rounds_are_not_duplicates():
    a = dedup_key("yt_video", {"video_id": "v", "round_id": "r1"})
    b = dedup_key("yt_video", {"video_id": "v", "round_id": "r2"})
    assert a != b


def test_report_counts_duplicates_and_bad_lines(tmp_path):
    d = tmp_path / "data" / "news" / "s"
    d.mkdir(parents=True)
    lines = [json.dumps(GOOD_NEWS, ensure_ascii=False),
             json.dumps(GOOD_NEWS, ensure_ascii=False),                 # ซ้ำ
             '{"site": "s", "article_id": "2"',                          # JSON เสีย
             json.dumps({**GOOD_NEWS, "article_id": "3", "title": ""}, ensure_ascii=False)]
    (d / "2026-11-25.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    r = main(["--data", str(tmp_path / "data"), "--out", str(tmp_path / "rep")])["kinds"]["news"]
    assert r["records"] == 4 and r["duplicates"] == 1 and r["bad_json_lines"] == 1
    assert r["field_completeness_pct"] == 50.0                          # 2 ใน 4 ครบ
    issues = (tmp_path / "rep" / "quality_issues.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(issues) == 3
    assert "duplicate of news/s/2026-11-25.jsonl:1" in issues[0]