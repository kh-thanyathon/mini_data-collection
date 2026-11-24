"""ค่าตั้งทั้งหมดของระบบ อ่านจาก .env ที่เดียว"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# ---------- 1) โหลด .env ----------
load_dotenv()

# ---------- 2) path หลักของโปรเจกต์ ----------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
STATE_DIR = ROOT / "state"
LOG_DIR = ROOT / "logs"


# ---------- 3) ฟังก์ชันช่วยอ่านค่าแบบ list ----------
def _list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------- 4) ค่าตั้งของ YouTube ----------
@dataclass
class YouTubeConfig:
    api_key: str = os.getenv("YOUTUBE_API_KEY", "")
    queries: list[str] = field(
        default_factory=lambda: _list("YT_QUERIES", "ข่าว,ฟุตบอล,เกม,รีวิวมือถือ,ท่องเที่ยว")
    )
    interval_minutes: int = int(os.getenv("YT_INTERVAL_MIN", "60"))
    max_pages: int = int(os.getenv("YT_MAX_PAGES", "2"))
    top_videos_for_comments: int = int(os.getenv("YT_TOP_COMMENTS", "20"))
    daily_quota_budget: int = int(os.getenv("YT_DAILY_BUDGET", "9000"))


# ---------- 5) ค่าตั้งของข่าว ----------
@dataclass
class NewsConfig:
    interval_minutes: int = int(os.getenv("NEWS_INTERVAL_MIN", "5"))
    max_req_per_min: int = int(os.getenv("NEWS_MAX_RPM", "12"))
    user_agent: str = os.getenv(
        "USER_AGENT", "MiniDataCollector/1.0 (+contact: you@example.com)"
    )


# ---------- 6) ค่าตั้งของการแจ้งเตือน ----------
@dataclass
class AlertConfig:
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    cooldown_minutes: int = int(os.getenv("ALERT_COOLDOWN_MIN", "30"))