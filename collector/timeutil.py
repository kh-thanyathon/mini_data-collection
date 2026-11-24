from datetime import datetime, timezone
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Asia/Bangkok")

def now() -> datetime:
    return datetime.now(LOCAL_TZ)

def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("datetime ต้องมี timezone")
    return dt.isoformat(timespec="seconds")

def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt

def latency_minutes(published_at: str | None, collected_at: str | None) -> float | None:
    p, c = parse_iso(published_at), parse_iso(collected_at)
    if p is None or c is None:
        return None
    return (c - p).total_seconds() / 60

def to_utc_rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")