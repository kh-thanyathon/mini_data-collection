from collector.config import YouTubeConfig
from collector.youtube import YouTubeCollector, classify_youtube, duration_seconds


class Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data


def yt_error(reason):
    return {"error": {"errors": [{"reason": reason}]}}


class FakeYouTube:
    """จำลอง 3 endpoint: search เจอ 2 คลิป, v0 ปิดคอมเมนต์, v1 มี 1 คอมเมนต์"""
    def get(self, url, params=None, headers=None):
        pub = "2026-11-24T05:00:00Z"
        if url.endswith("/search"):
            return Resp({"items": [{"id": {"videoId": v}, "snippet": {
                "title": "t", "channelId": "c", "channelTitle": "ct", "publishedAt": pub}}
                for v in ("v0", "v1")]})
        if url.endswith("/videos"):
            return Resp({"items": [
                {"id": "v0", "snippet": {"title": "t", "publishedAt": pub},
                 "statistics": {"viewCount": "100"}, "contentDetails": {"duration": "PT1M"}},
                {"id": "v1", "snippet": {"title": "t", "publishedAt": pub},
                 "statistics": {"viewCount": "50", "likeCount": "3", "commentCount": "1"},
                 "contentDetails": {"duration": "PT1H2M3S"}}]})
        if url.endswith("/commentThreads"):
            if params["videoId"] == "v0":
                return Resp(yt_error("commentsDisabled"), 403)
            return Resp({"items": [{"snippet": {"totalReplyCount": 0, "topLevelComment": {
                "id": "c1", "snippet": {"authorDisplayName": "a", "textDisplay": "สวัสดี",
                                        "likeCount": 0, "publishedAt": pub}}}}]})


def make(tmp_path):
    cfg = YouTubeConfig(api_key="fake", queries=["a"], max_pages=1,
                        daily_quota_budget=9000, interval_minutes=60)
    return YouTubeCollector(cfg, FakeYouTube(), state_dir=tmp_path / "s", data_dir=tmp_path / "d")


def test_full_round_handles_comments_disabled(tmp_path):
    s = make(tmp_path).run_round()
    assert s["errors"] == []
    assert s["new_videos"] == 2 and s["videos"] == 2
    assert s["comments_new"] == 1
    assert s["comments_disabled"] == 1          # ไม่ crash และจดไว้
    assert s["quota_this_round"] == 100 + 1 + 2  # search + videos + comments 2 คลิป


def test_hidden_counts_are_none_not_zero(tmp_path):
    yt = make(tmp_path)
    v0 = [v for v in yt.videos(["v0", "v1"], "2026-11-24T12:00:00+07:00", "r") if v["video_id"] == "v0"][0]
    assert v0["like_count"] is None and v0["comment_count"] is None


def test_comments_not_duplicated_across_rounds(tmp_path):
    yt = make(tmp_path)
    assert yt.run_round()["comments_new"] == 1
    assert yt.run_round()["comments_new"] == 0


def test_classify_youtube():
    assert classify_youtube(Resp(yt_error("commentsDisabled"), 403)) == (True, False, "commentsDisabled")
    assert classify_youtube(Resp(yt_error("rateLimitExceeded"), 403))[1] is True
    assert classify_youtube(Resp(yt_error("quotaExceeded"), 403)) == (False, False, "quotaExceeded")
    assert classify_youtube(Resp({}, 200)) is None


def test_duration_seconds():
    assert duration_seconds("PT1H2M3S") == 3723
    assert duration_seconds("PT45S") == 45
    assert duration_seconds("P1D") == 86400
    assert duration_seconds(None) is None