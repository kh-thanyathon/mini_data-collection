"""จุดเริ่มระบบ

    python run.py              # scheduler: รันต่อเนื่อง (ใช้ใน Docker)
    python run.py youtube      # รัน YouTube 1 รอบ
    python run.py news         # รันข่าว 1 รอบ (ทุกเว็บ)
    python run.py test-alert   # ส่งข้อความทดสอบเข้า Telegram
"""
import logging
import signal
import sys
import threading
import time
import traceback
from logging.handlers import RotatingFileHandler

from collector.alerts import Alerter
from collector.config import LOG_DIR, STATE_DIR, NewsConfig, YouTubeConfig, load_sites
from collector.storage import atomic_write_json
from collector.timeutil import now

log = logging.getLogger("run")


def setup_logging() -> None:
    """ทำใน main() ไม่ใช่ตอน import -> test ที่ import run.py จะไม่ไปสร้างไฟล์ log จริง"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(threadName)s: %(message)s",
        handlers=[logging.StreamHandler(),                                   # ดูผ่าน docker logs
                  RotatingFileHandler(LOG_DIR / "collector.log", maxBytes=5_000_000,
                                      backupCount=3, encoding="utf-8")])     # ไฟล์ไม่โตไม่สิ้นสุด

def _brief(result) -> str:
    """ย่อผลของรอบให้อ่านได้ใน 1 บรรทัด เช่น saved=3 new_links=3 errors=0"""
    rows = result if isinstance(result, list) else [result]
    keys = ("new_videos", "comments_new", "new_links", "saved", "failed")
    parts = []
    for r in rows:
        if isinstance(r, dict):
            parts += [f"{k}={r[k]}" for k in keys if k in r]
            parts.append(f"errors={len(r.get('errors', []))}")
    return " ".join(parts)

class Job:
    """งาน 1 ชนิด: รันทันทีตอนเริ่ม แล้วทุก interval นาที

    - รอบซ้อนกันไม่ได้: รอบก่อนยังไม่จบ -> ข้ามรอบนี้และแจ้งเตือน
    - งานพัง (exception) -> log + แจ้งเตือน แต่ scheduler และงานอื่นทำงานต่อ
    """

    def __init__(self, name: str, fn, interval_min: int, alerter: Alerter, clock=time.monotonic):
        self.name, self.fn, self.interval = name, fn, interval_min * 60
        self.alerter, self.clock = alerter, clock
        self.next_run = clock()                    # รอบแรกรันทันที
        self.running = threading.Lock()
        self.rounds = self.skipped = self.crashes = 0

    def due(self) -> bool:
        return self.clock() >= self.next_run

    def tick(self) -> bool:
        """เรียกถี่ ๆ จาก scheduler คืน True ถ้าเริ่มรอบใหม่"""
        if not self.due():
            return False
        # นับรอบถัดไปจากเวลาที่ "ควร" รัน ไม่ใช่เวลาที่รันจริง -> ไม่ค่อย ๆ เลื่อน (drift)
        while self.next_run <= self.clock():
            self.next_run += self.interval
        if not self.running.acquire(blocking=False):
            self.skipped += 1
            log.warning("%s: รอบก่อนยังไม่จบ ข้ามรอบนี้", self.name)
            self.alerter.fire(f"{self.name}:skipped", f"{self.name}: รอบก่อนยังไม่จบ รอบใหม่ถูกข้าม")
            return False
        threading.Thread(target=self._run, name=self.name, daemon=True).start()
        return True

    def _run(self) -> None:
        t0 = time.monotonic()
        try:
            result = self.fn()
            self.rounds += 1
            log.info("%s: รอบที่ %d เสร็จใน %.0f วิ %s", self.name, self.rounds,
                     time.monotonic() - t0, _brief(result))
            self.alerter.resolve(f"{self.name}:crash", f"{self.name} ทำงานได้แล้ว")
        except Exception as e:                      # noqa: BLE001
            self.crashes += 1
            log.error("%s crashed:\n%s", self.name, traceback.format_exc())
            self.alerter.fire(f"{self.name}:crash", f"{self.name} ล้ม: {type(e).__name__}: {e}"[:300])
        finally:
            self.running.release()


def build_jobs(alerter: Alerter) -> list[Job]:
    from collector.news import NewsCollector
    from collector.youtube import YouTubeCollector

    jobs = []
    yt_cfg = YouTubeConfig()
    if yt_cfg.api_key:
        yt = YouTubeCollector(yt_cfg, alerter=alerter)         # instance เดียวตลอด -> limiter/stats ต่อเนื่อง
        jobs.append(Job("youtube", yt.run_round, yt_cfg.interval_minutes, alerter))
    else:
        log.warning("ไม่มี YOUTUBE_API_KEY -> ข้ามส่วน A")

    news_cfg = NewsConfig()
    for site in load_sites():
        # 1 เว็บ = 1 งาน = 1 thread -> เว็บช้าไม่ถ่วงเว็บอื่น (rate limit แยกโดเมนอยู่แล้ว)
        nc = NewsCollector(news_cfg, [site], alerter=alerter)
        jobs.append(Job(f"news/{site.name}", nc.run_round, news_cfg.interval_minutes, alerter))
    return jobs


def scheduler() -> None:
    alerter = Alerter()
    jobs = build_jobs(alerter)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())       # docker stop ส่ง SIGTERM
    log.info("scheduler เริ่มทำงาน: %s", [(j.name, j.interval // 60) for j in jobs])
    alerter.sender(f"▶️ [Mini Collector] เริ่มทำงาน {len(jobs)} งาน: "
                   + ", ".join(f"{j.name} ทุก {j.interval // 60} นาที" for j in jobs))
    try:
        while not stop.is_set():
            for j in jobs:
                j.tick()
            # heartbeat: ให้ Docker healthcheck รู้ว่า scheduler ยังไม่ค้าง
            atomic_write_json(STATE_DIR / "heartbeat.json", {
                "at": now().isoformat(timespec="seconds"),
                "jobs": {j.name: {"rounds": j.rounds, "skipped": j.skipped,
                                  "crashes": j.crashes, "running": j.running.locked()} for j in jobs}})
            stop.wait(5)
    except KeyboardInterrupt:
        pass
    log.info("scheduler หยุดทำงาน")
    alerter.sender("⏹️ [Mini Collector] หยุดทำงาน")


def main() -> None:
    setup_logging()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scheduler"
    if cmd == "scheduler":
        scheduler()
    elif cmd == "youtube":
        from collector.youtube import YouTubeCollector
        print(YouTubeCollector(alerter=Alerter()).run_round())
    elif cmd == "news":
        from collector.news import NewsCollector
        for s in NewsCollector(alerter=Alerter()).run_round():
            print(s)
    elif cmd == "test-alert":
        print(Alerter().sender("🧪 [Mini Collector] ทดสอบการแจ้งเตือน"))
    else:
        sys.exit(f"ไม่รู้จักคำสั่ง {cmd}: ใช้ scheduler | youtube | news | test-alert")


if __name__ == "__main__":
    main()