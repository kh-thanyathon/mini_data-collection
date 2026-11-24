from datetime import timedelta

import pytest

from collector.config import YouTubeConfig
from collector.timeutil import now
from collector.youtube import QuotaExhausted, QuotaGuard, YouTubeCollector


class FakeResp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeClient:
    """จำลอง YouTube: แต่ละ query มี 3 หน้า หน้าละ 2 คลิป"""
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, headers=None):
        self.calls.append(params)
        page = int(params.get("pageToken", "0"))
        q = params["q"]
        items = [{"id": {"videoId": f"{q}-{page}-{i}"},
                  "snippet": {"title": "t", "channelId": "c", "channelTitle": "ct",
                              "publishedAt": "2026-11-24T05:00:00Z"}} for i in range(2)]
        data = {"items": items}
        if page < 2:
            data["nextPageToken"] = str(page + 1)
        return FakeResp(data)


def make(tmp_path, queries, max_pages=3, budget=9000):
    cfg = YouTubeConfig(api_key="fake", queries=queries, max_pages=max_pages,
                        daily_quota_budget=budget, interval_minutes=60)
    client = FakeClient()
    yt = YouTubeCollector(cfg, client, state_dir=tmp_path / "state", data_dir=tmp_path / "data")
    return yt, client


def test_pagination_follows_token_until_max_pages(tmp_path):
    yt, client = make(tmp_path, ["a"], max_pages=2)
    out = yt.search(now() - timedelta(hours=1), "2026-11-24T12:10:00+07:00", "r1")
    assert len(client.calls) == 2                    # หยุดที่ max_pages
    assert client.calls[1]["pageToken"] == "1"       # ส่ง token ของหน้าก่อนไปจริง
    assert len(out) == 4


def test_round_budget_limits_extra_pages(tmp_path):
    # งบ 9000/24 = 375 - 40 = 335 → หน้าแรก a,b (200) + หน้า 2 ได้อีก 1 หน้า (300)
    yt, client = make(tmp_path, ["a", "b"], max_pages=3)
    yt.search(now() - timedelta(hours=1), "2026-11-24T12:10:00+07:00", "r1")
    assert [c["q"] for c in client.calls] == ["a", "b", "a"]
    assert yt.round_spent == 300
    assert yt.pages_skipped > 0


def test_second_round_skips_seen_videos(tmp_path):
    yt, _ = make(tmp_path, ["a"], max_pages=1)
    first = yt.run_search_round()
    second = yt.run_search_round()
    assert first["new_videos"] == 2
    assert second["new_videos"] == 0                 # ไม่เก็บซ้ำข้ามรอบ


def test_quota_guard_blocks_over_budget(tmp_path):
    g = QuotaGuard(250, tmp_path / "q.json")
    g.spend(100)
    g.spend(100)
    with pytest.raises(QuotaExhausted):
        g.spend(100)
    assert g.used() == 200                           # ครั้งที่ถูกบล็อกไม่ถูกนับ