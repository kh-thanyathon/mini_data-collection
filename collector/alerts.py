"""แจ้งเตือนเข้า Telegram

- ตัดสินจาก log ของแต่ละรอบ (check_round) -> collector ไม่ต้องรู้เรื่อง alert
- กันแจ้งถี่: เรื่องเดียวกัน (key เดียวกัน) แจ้งซ้ำได้เมื่อพ้น cooldown เท่านั้น
- แจ้ง ✅ ครั้งเดียวเมื่อกลับมาปกติ
"""
import logging
import re
from datetime import timedelta
from pathlib import Path

import requests

from .config import STATE_DIR, AlertConfig
from .storage import atomic_write_json, load_json
from .timeutil import now, parse_iso

log = logging.getLogger(__name__)

NEWS_P95_LIMIT = 30          # KPI ข่าว: p95 <= 30 นาที
YOUTUBE_P95_LIMIT = 60       # KPI YouTube: <= 60 นาที
MIN_SUCCESS = 0.98           # KPI: success rate >= 98%
MIN_REQUESTS_FOR_RATE = 10   # รอบที่ request น้อยกว่านี้ ไม่ตัดสิน success rate (1 ใน 3 พัง = 67% ดูน่ากลัวเกินจริง)
MIN_SAMPLES_FOR_P95 = 3      # ข่าวน้อยกว่านี้ p95 ไม่มีความหมาย


class Alerter:
    def __init__(self, cfg: AlertConfig | None = None, state_path: Path = STATE_DIR / "alerts.json",
                 sender=None):
        self.cfg = cfg or AlertConfig()
        self.state_path = state_path
        self.state: dict = load_json(state_path, {})
        self.sender = sender or self._send_telegram      # test ส่งตัวปลอมเข้ามาได้

    # ---------- ส่งข้อความ ----------
    def _send_telegram(self, text: str) -> bool:
        if not (self.cfg.telegram_token and self.cfg.telegram_chat_id):
            log.warning("ALERT (ยังไม่ได้ตั้ง Telegram): %s", text)
            return False
        try:
            r = requests.post(f"https://api.telegram.org/bot{self.cfg.telegram_token}/sendMessage",
                              data={"chat_id": self.cfg.telegram_chat_id, "text": text}, timeout=10)
            return r.ok
        except requests.RequestException as e:          # แจ้งเตือนพัง ต้องไม่ทำให้บอทพัง
            log.error("ส่ง Telegram ไม่สำเร็จ: %s", type(e).__name__)
            return False

    def fire(self, key: str, message: str) -> bool:
        """แจ้งปัญหา key เดียวกันซ้ำได้เมื่อพ้น cooldown เท่านั้น"""
        st = self.state.setdefault(key, {})
        last = parse_iso(st.get("last_sent"))
        if last and now() - last < timedelta(minutes=self.cfg.cooldown_minutes):
            st["suppressed"] = st.get("suppressed", 0) + 1
            st["active"] = True
            self._save()
            return False
        extra = f"\n(ระหว่างนี้เกิดซ้ำอีก {st['suppressed']} ครั้ง)" if st.get("suppressed") else ""
        sent = self.sender(f"🔴 [Mini Collector] {message}{extra}\nเวลา: {now():%Y-%m-%d %H:%M}")
        st.update(last_sent=now().isoformat(timespec="seconds"), active=True, suppressed=0)
        self._save()
        return sent

    def resolve(self, key: str, message: str) -> None:
        """ส่ง ✅ ครั้งเดียว เฉพาะเมื่อเรื่องนี้เคยแจ้งเตือนอยู่"""
        st = self.state.get(key)
        if st and st.get("active"):
            self.sender(f"✅ [Mini Collector] กลับมาปกติ: {message}\nเวลา: {now():%Y-%m-%d %H:%M}")
            st.update(active=False, suppressed=0)
            self._save()

    def _save(self) -> None:
        atomic_write_json(self.state_path, self.state)

    def _rule(self, key: str, bad: bool, message: str, ok_message: str) -> None:
        if bad:
            self.fire(key, message)
        else:
            self.resolve(key, ok_message)

    # ---------- กฎแจ้งเตือน (อ่านจาก log ของรอบ) ----------
    def check_round(self, rlog: dict) -> None:
        src = rlog["source"]
        items, errors = rlog["items"], rlog["errors"]

        # 1) ถูกบล็อก / เว็บล่ม
        status = items.get("listing_error_status")
        listing_failed = any(e.startswith("listing") for e in errors)
        self._rule(f"{src}:blocked", status in (401, 403, 429),
                   f"{src} ถูกบล็อก (HTTP {status}) — หยุดชั่วคราวและตรวจ User-Agent/ความถี่",
                   f"{src} เข้าถึงได้แล้ว")
        self._rule(f"{src}:down", listing_failed and status not in (401, 403, 429),
                   f"{src} โหลดหน้ารายการไม่ได้: {errors[0] if errors else ''}"[:300],
                   f"{src} โหลดหน้ารายการได้แล้ว")

        # 2) เก็บได้ต่ำกว่าเกณฑ์: หน้ารายการไม่มีลิงก์เลย = selector/layout น่าจะเปลี่ยน
        if "listing_links" in items:
            self._rule(f"{src}:layout", not listing_failed and items["listing_links"] == 0,
                       f"{src} ไม่พบลิงก์ข่าวบนหน้ารายการ — layout หรือ pattern อาจเปลี่ยน",
                       f"{src} พบลิงก์ข่าวแล้ว")
        tried = items.get("saved", 0) + items.get("failed", 0)
        if tried:
            self._rule(f"{src}:parse", items.get("failed", 0) / tried > 0.2,
                       f"{src} parse ข่าวไม่ได้ {items.get('failed', 0)}/{tried} — หน้าข่าวอาจเปลี่ยนโครงสร้าง",
                       f"{src} parse ข่าวได้ตามปกติ")

        # 3) success rate ต่ำกว่า KPI
        rq = rlog["requests"]
        if rq["total"] >= MIN_REQUESTS_FOR_RATE:
            self._rule(f"{src}:success", rq["success_rate"] < MIN_SUCCESS,
                       f"{src} success rate {rq['success_rate']:.1%} ต่ำกว่า {MIN_SUCCESS:.0%}",
                       f"{src} success rate กลับมาปกติ")

        # 4) latency เกิน KPI
        fr = rlog["freshness"]
        limit = NEWS_P95_LIMIT if src.startswith("news") else YOUTUBE_P95_LIMIT
        if fr["n"] >= MIN_SAMPLES_FOR_P95:
            self._rule(f"{src}:latency", fr["p95_min"] > limit,
                       f"{src} freshness p95 = {fr['p95_min']:.0f} นาที เกินเป้า {limit} นาที",
                       f"{src} freshness กลับมาอยู่ในเป้า")

        # 5) รอบนานเกินช่วงห่าง -> รอบถัดไปจะชน
        self._rule(f"{src}:overrun", not rlog["duration_ok"],
                   f"{src} รอบใช้เวลา {rlog['duration_s']:.0f} วิ เกินช่วงห่าง {rlog['interval_s']} วิ",
                   f"{src} รอบจบทันเวลาแล้ว")

        # 6) โควตา YouTube หมด
        quota = any("quota" in e.lower() for e in errors)
        if src == "youtube":
            self._rule("youtube:quota", quota,
                       "YouTube หยุดเก็บ: โควตาวันนี้หมด (รีเซ็ตเที่ยงคืนเวลาแปซิฟิก ≈ 14:00-15:00 น.)",
                       "YouTube โควตากลับมาแล้ว")


def http_status_from_error(e) -> int | None:
    """ดึง HTTP status จาก GiveUp (มี response) หรือจากข้อความ 'HTTP 403 ...' (Playwright)"""
    resp = getattr(e, "response", None)
    if resp is not None:
        return resp.status_code
    m = re.search(r"HTTP (\d{3})", str(e))
    return int(m.group(1)) if m else None


if __name__ == "__main__":
    # python -m collector.alerts  -> ส่งข้อความทดสอบ (ใช้แคปหน้าจอเป็นหลักฐาน)
    ok = Alerter().sender("🧪 [Mini Collector] ทดสอบการแจ้งเตือน — ถ้าเห็นข้อความนี้แปลว่าตั้งค่าถูกต้อง")
    print("ส่งสำเร็จ" if ok else "ส่งไม่สำเร็จ — ตรวจ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID ใน .env")