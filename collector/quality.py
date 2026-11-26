"""กฎตรวจคุณภาพข้อมูล: missing / malformed ต่อ record + key สำหรับหาข้อมูลซ้ำ

แยกเป็นโมดูลของตัวเอง เพื่อให้ทั้งสคริปต์รายงานและ test ใช้กฎชุดเดียวกัน
"""
from urllib.parse import urlparse

from .timeutil import parse_iso

# field บังคับของแต่ละชนิดข้อมูล (ตามที่โจทย์กำหนด)
REQUIRED = {
    "news": ["url", "title", "published_at", "collected_at", "content", "images", "category"],
    "yt_search": ["video_id", "title", "channel_id", "query", "published_at", "collected_at"],
    "yt_video": ["video_id", "view_count", "duration", "published_at", "collected_at"],
    "yt_comment": ["comment_id", "video_id", "text", "published_at", "collected_at"],
}
# like_count / comment_count ของวิดีโอไม่อยู่ใน REQUIRED: None = เจ้าของซ่อนไว้ ไม่ใช่ข้อมูลหาย

# key ที่ "ห้ามซ้ำ" ของแต่ละชนิด
DEDUP_KEY = {
    "news": ("site", "article_id"),
    "yt_search": ("video_id",),
    "yt_video": ("video_id", "round_id"),     # snapshot ต่อรอบ: วิดีโอเดียวกันต่างรอบไม่ถือว่าซ้ำ
    "yt_comment": ("comment_id",),
}

MIN_CONTENT_CHARS = 50
FUTURE_TOLERANCE_S = 300       # published_at เลย collected_at ได้ไม่เกิน 5 นาที (นาฬิกาเว็บอาจเพี้ยนเล็กน้อย)


def _empty(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, list) and not v)


def _is_iso_with_tz(v) -> bool:
    if not isinstance(v, str) or "T" not in v:
        return False
    dt = parse_iso(v)
    # parse_iso เติมเวลาไทยให้ถ้าไม่มี tz -> ต้องเช็คจากข้อความเองว่ามี tz จริง
    tail = v[19:]
    return dt is not None and (tail.endswith("Z") or "+" in tail or "-" in tail)


def _is_http_url(v) -> bool:
    u = urlparse(v) if isinstance(v, str) else None
    return bool(u and u.scheme in ("http", "https") and u.netloc)


def check(kind: str, rec: dict) -> dict:
    """คืน {"missing": [...], "malformed": [...]} — ว่างทั้งคู่ = ผ่าน"""
    missing = [f for f in REQUIRED[kind] if _empty(rec.get(f))]
    malformed = []

    for f in ("published_at", "collected_at"):
        if not _empty(rec.get(f)) and not _is_iso_with_tz(rec[f]):
            malformed.append(f"{f}: not ISO 8601 with timezone")
    p, c = parse_iso(rec.get("published_at")), parse_iso(rec.get("collected_at"))
    if p and c and (p - c).total_seconds() > FUTURE_TOLERANCE_S:
        malformed.append("published_at after collected_at")

    if kind == "news":
        if not _empty(rec.get("url")) and not _is_http_url(rec["url"]):
            malformed.append("url: not http(s)")
        if isinstance(rec.get("images"), list) and not all(_is_http_url(u) for u in rec["images"]):
            malformed.append("images: contains non-URL")
        if isinstance(rec.get("content"), str) and 0 < len(rec["content"].strip()) < MIN_CONTENT_CHARS:
            malformed.append(f"content: shorter than {MIN_CONTENT_CHARS} chars")
        if isinstance(rec.get("category"), str) and rec["category"].startswith("["):
            malformed.append("category: looks like encoded list")

    for f in ("view_count", "like_count", "comment_count", "reply_count"):
        v = rec.get(f)
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
            malformed.append(f"{f}: not a non-negative int")

    if kind == "yt_video" and rec.get("duration") and rec.get("duration_s") is None:
        malformed.append("duration: cannot parse ISO 8601 duration")

    return {"missing": missing, "malformed": malformed}


def dedup_key(kind: str, rec: dict) -> tuple:
    return tuple(rec.get(k) for k in DEDUP_KEY[kind])