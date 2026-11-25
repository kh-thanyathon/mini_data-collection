import json
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
    max_articles_per_round: int = int(os.getenv("NEWS_MAX_ARTICLES", "40"))


# ---------- 6) ค่าตั้งของการแจ้งเตือน ----------
@dataclass
class AlertConfig:
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    cooldown_minutes: int = int(os.getenv("ALERT_COOLDOWN_MIN", "30"))

# ---------- เว็บข่าว ----------
@dataclass
class NewsSite:
    name: str                            # ชื่อสั้น ใช้ตั้งชื่อโฟลเดอร์และไฟล์ state
    list_urls: list[str]                 # หน้ารายการข่าว (มีได้หลายหน้า)
    article_pattern: str                 # regex แยกลิงก์ข่าวออกจากลิงก์อื่น
    id_pattern: str                      # regex ดึงเลขข่าวจาก URL (group 1)
    title_suffix: str = ""             # ข้อความท้ายหัวข้อที่ต้องตัด เช่น " - ข่าวสด"
    content_selector: str = ""           # CSS selector ของเนื้อหาข่าว (ว่าง = เดาเอง)
    content_stop: list[str] = field(default_factory=list)    # เจอย่อหน้านี้แล้วหยุด เช่น "อ่านข่าว"
    category_segment: int | None = None  # path segment ที่เป็นหมวด (ทางสำรอง)
    render: bool = False               # True = ใช้ Playwright โหลดหน้ารายการ
    scrolls: int = 0                   # จำนวนครั้งที่เลื่อนจอ (infinite scroll)
    blocked_paths: list[str] = field(default_factory=list)   # path ที่ robots.txt ห้าม


def load_sites(path: Path | None = None) -> list[NewsSite]:
    path = path or Path(os.getenv("NEWS_SITES_FILE", ROOT / "sites.json"))
    return [NewsSite(**s) for s in json.loads(path.read_text(encoding="utf-8"))]