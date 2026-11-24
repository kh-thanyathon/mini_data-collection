import re
from .http_client import DomainRateLimiter, GiveUp, HttpClient, RequestStats
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import DATA_DIR, STATE_DIR, YouTubeConfig
from .storage import (SeenStore, append_jsonl, atomic_write_json, load_json,
                      round_id)
from .timeutil import iso, latency_minutes, now, parse_iso, to_utc_rfc3339

API = "https://www.googleapis.com/youtube/v3"
COST = {"search": 100, "videos": 1, "commentThreads": 1}
PT = ZoneInfo("America/Los_Angeles")      # โควตารีเซ็ตเที่ยงคืนเวลาแปซิฟิก
RESERVE_FOR_DETAILS = 40                  # เผื่อ videos + comments (บทที่ 5)
OVERLAP = timedelta(minutes=15)           # ช่วงเวลาซ้อนกันระหว่างรอบ


class QuotaExhausted(Exception):
    pass


class QuotaGuard:
    def __init__(self, budget: int, path: Path):
        self.budget = budget
        self.path = path
        self.state = load_json(path, {})

    def today(self) -> str:
        return now().astimezone(PT).strftime("%Y-%m-%d")

    def used(self) -> int:
        return self.state.get(self.today(), 0)

    def spend(self, units: int) -> None:
        if self.used() + units > self.budget:
            raise QuotaExhausted(f"used {self.used()} + {units} > budget {self.budget}")
        self.state = {self.today(): self.used() + units}   # เก็บแค่วันปัจจุบัน
        atomic_write_json(self.path, self.state)

def classify_youtube(resp):
    if resp.status_code not in (403, 404):
        return None
    try:
        reason = resp.json()["error"]["errors"][0]["reason"]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    if reason in ("commentsDisabled", "videoNotFound"):
        return True, False, reason
    if reason in ("rateLimitExceeded", "userRateLimitExceeded"):
        return False, True, reason
    if reason in ("quotaExceeded", "dailyLimitExceeded"):
        return False, False, reason
    return None


_DURATION = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?")


def duration_seconds(value: str | None) -> int | None:
    """PT12M30S -> 750"""
    m = _DURATION.fullmatch(value or "")
    if not value or not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def _int(v):
    """'1520' -> 1520, ไม่มีค่า -> None (ไม่ใช่ 0)"""
    return int(v) if v is not None else None

class YouTubeCollector:
    def __init__(self, cfg: YouTubeConfig | None = None, client: HttpClient | None = None,
                 state_dir: Path = STATE_DIR, data_dir: Path = DATA_DIR):
        self.seen_comments = SeenStore(state_dir / "yt_seen_comments.json", ttl_days=7)
        self.tracked_path = state_dir / "yt_tracked.json"
        self.tracked: dict[str, str] = load_json(self.tracked_path, {})  # video_id -> published_at
        self.pages_skipped = 0
        self.cfg = cfg or YouTubeConfig()
        if not self.cfg.api_key:
            raise RuntimeError("YOUTUBE_API_KEY is missing in .env")
        self.client = client or HttpClient(DomainRateLimiter(20), RequestStats(),
                                           "MiniDataCollector/1.0",
                                           classify=classify_youtube)
        self.data_dir = data_dir
        self.quota = QuotaGuard(self.cfg.daily_quota_budget, state_dir / "yt_quota.json")
        self.seen = SeenStore(state_dir / "yt_seen_videos.json", ttl_days=7)
        self.last_run_path = state_dir / "yt_last_run.json"

        rounds_per_day = 24 * 60 // self.cfg.interval_minutes
        self.round_budget = self.cfg.daily_quota_budget // rounds_per_day
        self.round_spent = 0

    # ---------- เรียก API ทุกครั้งผ่านตรงนี้ ----------
    def _call(self, endpoint: str, params: dict) -> dict:
        cost = COST[endpoint]
        self.quota.spend(cost)            # หักก่อนเรียก: request ที่ล้มเหลวก็กินโควตา
        self.round_spent += cost
        resp = self.client.get(f"{API}/{endpoint}", params=params,
                               headers={"X-goog-api-key": self.cfg.api_key})
        return resp.json()

    def _can_afford_extra_page(self) -> bool:
        return self.round_spent + COST["search"] <= self.round_budget - RESERVE_FOR_DETAILS

        # ---------- A1: search + pagination ----------
    def _search_page(self, q, token, published_after, collected_at, rid, out, in_round):
        """ดึง 1 หน้าของ query แล้วคืน token ของหน้าถัดไป (None = หมดแล้ว)"""
        params = {"part": "snippet", "type": "video", "q": q, "order": "date",
                  "maxResults": 50, "publishedAfter": to_utc_rfc3339(published_after),
                  "regionCode": "TH", "relevanceLanguage": "th"}
        if token:
            params["pageToken"] = token
        data = self._call("search", params)

        for item in data.get("items", []):
            vid = item.get("id", {}).get("videoId")
            if not vid or vid in self.seen or vid in in_round:
                continue
            in_round.add(vid)
            sn = item["snippet"]
            out.append({
                "video_id": vid,
                "title": sn.get("title"),
                "channel_id": sn.get("channelId"),
                "channel_title": sn.get("channelTitle"),
                "query": q,
                "published_at": sn.get("publishedAt"),
                "collected_at": collected_at,
                "round_id": rid,
            })
        return data.get("nextPageToken")

    def search(self, published_after, collected_at: str, rid: str) -> list[dict]:
        out, in_round = [], set()
        args = (published_after, collected_at, rid, out, in_round)

        # 1) หน้าแรกของทุก query ก่อนเสมอ
        tokens = {q: self._search_page(q, None, *args) for q in self.cfg.queries}

        # 2) หน้าถัดไป ไล่ทีละ query เท่าที่งบรอบนี้ยังพอ
        self.pages_skipped = 0
        for _ in range(1, self.cfg.max_pages):
            for q in self.cfg.queries:
                if not tokens.get(q):
                    continue
                if not self._can_afford_extra_page():
                    self.pages_skipped += 1      # มีหน้าถัดไปแต่งบไม่พอ (เอาไปรายงาน)
                    continue
                tokens[q] = self._search_page(q, tokens[q], *args)
        return out

    # ---------- A2: สถิติวิดีโอ ----------
    def videos(self, ids: list[str], collected_at: str, rid: str) -> list[dict]:
        out = []
        for i in range(0, len(ids), 50):          # 1 call รับได้ 50 id = 1 หน่วยโควตา
            data = self._call("videos", {"part": "snippet,statistics,contentDetails",
                                         "id": ",".join(ids[i:i + 50])})
            for it in data.get("items", []):
                st = it.get("statistics", {})
                cd = it.get("contentDetails", {})
                out.append({
                    "video_id": it["id"],
                    "title": it["snippet"].get("title"),
                    "view_count": _int(st.get("viewCount")),
                    "like_count": _int(st.get("likeCount")),
                    "comment_count": _int(st.get("commentCount")),
                    "duration": cd.get("duration"),
                    "duration_s": duration_seconds(cd.get("duration")),
                    "published_at": it["snippet"].get("publishedAt"),
                    "collected_at": collected_at,
                    "round_id": rid,
                })
        return out

    # ---------- A3: คอมเมนต์ ----------
    def comments(self, top: list[dict], collected_at: str, rid: str):
        out, status = [], []
        for v in top:
            vid = v["video_id"]
            try:
                data = self._call("commentThreads", {"part": "snippet", "videoId": vid,
                                                     "maxResults": 100, "order": "time",
                                                     "textFormat": "plainText"})
            except GiveUp as e:                    # วิดีโอเดียวพัง ไม่ควรทำให้ทั้งรอบพัง
                status.append({"video_id": vid, "status": "error", "detail": str(e)[:200],
                               "collected_at": collected_at, "round_id": rid})
                continue

            if "error" in data:                    # commentsDisabled / videoNotFound
                reason = data["error"]["errors"][0]["reason"]
                status.append({"video_id": vid, "status": reason,
                               "collected_at": collected_at, "round_id": rid})
                continue

            new = 0
            for it in data.get("items", []):
                c = it["snippet"]["topLevelComment"]
                if c["id"] in self.seen_comments:
                    continue
                s = c["snippet"]
                out.append({
                    "comment_id": c["id"],
                    "video_id": vid,
                    "author": s.get("authorDisplayName"),
                    "text": s.get("textDisplay"),
                    "like_count": s.get("likeCount"),
                    "reply_count": it["snippet"].get("totalReplyCount"),
                    "published_at": s.get("publishedAt"),
                    "collected_at": collected_at,
                    "round_id": rid,
                })
                new += 1
            status.append({"video_id": vid, "status": "ok", "new_comments": new,
                           "collected_at": collected_at, "round_id": rid})
        return out, status

    def _prune_tracked(self) -> None:
        """ติดตามวิดีโอ 3 วันล่าสุด ไม่เกิน 500 ตัว (= videos.list 10 หน่วย)"""
        cutoff = now() - timedelta(days=3)
        recent = [(k, v) for k, v in self.tracked.items() if (parse_iso(v) or now()) >= cutoff]
        recent.sort(key=lambda kv: kv[1], reverse=True)
        self.tracked = dict(recent[:500])

    # ---------- 1 รอบ ----------
        # ---------- 1 รอบ ----------
    def run_search_round(self) -> dict:
        started = now()
        rid = round_id(started)
        collected_at = iso(started)
        self.round_spent = 0

        last = parse_iso(load_json(self.last_run_path, {}).get("started_at"))
        published_after = (last - OVERLAP) if last else started - timedelta(hours=2)

        found = self.search(published_after, collected_at, rid)

        # เขียนไฟล์ก่อน แล้วค่อยจำ (บทที่ 3)
        append_jsonl(self.data_dir / "youtube" / rid / "search.jsonl", found)
        for r in found:
            self.seen.add(r["video_id"])
        self.seen.save()
        # เลื่อนหน้าต่างเวลาเฉพาะเมื่อรอบนี้สำเร็จ
        atomic_write_json(self.last_run_path, {"started_at": iso(started)})

        lat = [x for x in (latency_minutes(r["published_at"], collected_at) for r in found)
               if x is not None]
        return {"round_id": rid, "new_videos": len(found),
                "quota_this_round": self.round_spent, "quota_today": self.quota.used(),
                "pages_skipped_for_budget": self.pages_skipped,
                "latency_min": sorted(lat)}

    def run_round(self) -> dict:
        """รอบเต็มของส่วน A: search -> videos -> comments"""
        started = now()
        rid = round_id(started)
        collected_at = iso(started)
        out_dir = self.data_dir / "youtube" / rid
        self.round_spent = 0
        found, summary = [], {"round_id": rid, "errors": []}

        try:
            last = parse_iso(load_json(self.last_run_path, {}).get("started_at"))
            published_after = (last - OVERLAP) if last else started - timedelta(hours=2)

            # A1
            found = self.search(published_after, collected_at, rid)
            summary["new_videos"] = append_jsonl(out_dir / "search.jsonl", found)
            for r in found:
                self.seen.add(r["video_id"])
                self.tracked[r["video_id"]] = r["published_at"]
            self.seen.save()

            # A2
            self._prune_tracked()
            stats = self.videos(list(self.tracked), collected_at, rid)
            summary["videos"] = append_jsonl(out_dir / "videos.jsonl", stats)

            # A3
            top = sorted(stats, key=lambda s: s["view_count"] or 0, reverse=True)
            top = top[: self.cfg.top_videos_for_comments]
            comments, status = self.comments(top, collected_at, rid)
            summary["comments_new"] = append_jsonl(out_dir / "comments.jsonl", comments)
            for c in comments:
                self.seen_comments.add(c["comment_id"])
            self.seen_comments.save()
            append_jsonl(out_dir / "comment_status.jsonl", status)
            summary["comments_disabled"] = sum(s["status"] == "commentsDisabled" for s in status)

            atomic_write_json(self.tracked_path, self.tracked)
            atomic_write_json(self.last_run_path, {"started_at": iso(started)})
        except QuotaExhausted as e:
            summary["errors"].append(f"quota guard: {e}")
        except GiveUp as e:
            summary["errors"].append(str(e))

        lat = [x for x in (latency_minutes(r["published_at"], collected_at) for r in found)
               if x is not None]
        summary.update(quota_this_round=self.round_spent, quota_today=self.quota.used(),
                       pages_skipped_for_budget=self.pages_skipped, latency_min=sorted(lat))
        return summary


if __name__ == "__main__":
    s = YouTubeCollector().run_round()
    print({k: v for k, v in s.items() if k != "latency_min"})
    if s["latency_min"]:
        lat = s["latency_min"]
        print(f"latency (นาที): min={lat[0]:.0f} median={lat[len(lat)//2]:.0f} max={lat[-1]:.0f}")