import random
import threading
import time
from collections import defaultdict


class DomainRateLimiter:
    """เว้นระยะขั้นต่ำต่อโดเมน = 60 / max_per_min วินาที"""

    def __init__(self, max_per_min: int, clock=time.monotonic, sleep=time.sleep):
        self.interval = 60.0 / max_per_min
        self.clock = clock
        self.sleep = sleep
        self.next_ok = defaultdict(float)   # โดเมน -> เวลาที่ยิงครั้งถัดไปได้
        self.lock = threading.Lock()

    def wait(self, domain: str) -> float:
        with self.lock:
            t = self.clock()
            delay = max(0.0, self.next_ok[domain] - t)
            self.next_ok[domain] = max(t, self.next_ok[domain]) + self.interval
        if delay:
            self.sleep(delay)
        return delay

from datetime import datetime
from email.utils import parsedate_to_datetime

RETRY_STATUS = {429, 500, 502, 503, 504}


def backoff_delay(attempt: int, base: float = 1.0, cap: float = 60.0) -> float:
    """attempt 0,1,2,3 → ประมาณ 1, 2, 4, 8 วินาที (+ สุ่มไม่เกิน 0.5 วินาที)"""
    return min(cap, base * (2 ** attempt)) + random.uniform(0, 0.5)


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    if value.strip().isdigit():
        return float(value)
    try:
        dt = parsedate_to_datetime(value)
        return max(0.0, (dt - datetime.now(dt.tzinfo)).total_seconds())
    except (TypeError, ValueError):
        return None

from .timeutil import now


class RequestStats:
    def __init__(self):
        self.records: list[dict] = []
        self.lock = threading.Lock()

    def add(self, domain: str, status: int | None, ok: bool, elapsed: float, note: str = ""):
        with self.lock:
            self.records.append({
                "ts": now().isoformat(timespec="seconds"),
                "domain": domain,
                "status": status,       # None = ไม่ได้ response เลย (timeout ฯลฯ)
                "ok": ok,
                "elapsed_s": round(elapsed, 3),
                "note": note,
            })

    def summary(self) -> dict:
        total = len(self.records)
        ok = sum(r["ok"] for r in self.records)
        return {"total": total, "success": ok,
                "success_rate": round(ok / total, 4) if total else None}

from urllib.parse import urlparse

import requests


class GiveUp(Exception):
    """ลองครบแล้วยังไม่สำเร็จ หรือเป็น error ที่ไม่ควรลองซ้ำ"""

    def __init__(self, msg: str, response=None):
        super().__init__(msg)
        self.response = response


class HttpClient:
    def __init__(self, limiter: DomainRateLimiter, stats: RequestStats, user_agent: str,
                 max_retries: int = 4, timeout: float = 20, max_wait: float = 120,
                 session=None, sleep=time.sleep, classify=None):
        self.limiter = limiter
        self.stats = stats
        self.max_retries = max_retries
        self.timeout = timeout
        self.max_wait = max_wait
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.sleep = sleep
        self.classify = classify

    def get(self, url: str, **kwargs) -> requests.Response:
        domain = urlparse(url).netloc

        for attempt in range(self.max_retries + 1):
            self.limiter.wait(domain)                     # 1) รอคิว
            t0 = time.monotonic()
            try:
                resp = self.session.get(url, timeout=self.timeout, **kwargs)   # 2) ยิง
            except (requests.ConnectionError, requests.Timeout) as e:
                self.stats.add(domain, None, False, time.monotonic() - t0, type(e).__name__)
                if attempt == self.max_retries:
                    raise GiveUp(f"network error {url}: {e}") from e
                self.sleep(backoff_delay(attempt))
                continue

            elapsed = time.monotonic() - t0
            if self.classify:
                verdict = self.classify(resp)
                if verdict is not None:
                    ok, retry, note = verdict
                    self.stats.add(domain, resp.status_code, ok, elapsed, note)
                    if ok:
                        return resp                       # เช่น commentsDisabled
                    if not retry or attempt == self.max_retries:
                        raise GiveUp(f"{note} {url}", resp)   # เช่น quotaExceeded
                    self.sleep(backoff_delay(attempt))    # เช่น rateLimitExceeded
                    continue
            
            ok = resp.status_code < 400
            self.stats.add(domain, resp.status_code, ok, time.monotonic() - t0)   # 3) นับ
            if ok:
                return resp

            # 4) ตัดสินใจว่าจะลองใหม่ไหม
            if resp.status_code in RETRY_STATUS and attempt < self.max_retries:
                wait = parse_retry_after(resp.headers.get("Retry-After"))
                if wait is None:
                    wait = backoff_delay(attempt)
                if wait > self.max_wait:
                    raise GiveUp(f"Retry-After {wait:.0f}s too long: {url}", resp)
                self.sleep(wait)
                continue

            raise GiveUp(f"HTTP {resp.status_code} {url}", resp)

        raise GiveUp(f"exhausted retries {url}")