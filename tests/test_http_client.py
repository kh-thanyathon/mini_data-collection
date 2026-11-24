import pytest
import requests

from collector.http_client import (DomainRateLimiter, GiveUp, HttpClient,
                                   RequestStats, parse_retry_after)


class FakeResp:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = headers or {}


class FakeSession:
    """ตอบตามลำดับที่กำหนดไว้ใน responses"""
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}

    def get(self, url, timeout=None, **kw):
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make_client(responses, sleeps):
    limiter = DomainRateLimiter(600, sleep=lambda s: None)   # ไม่ต้องรอจริงใน test
    stats = RequestStats()
    client = HttpClient(limiter, stats, "test", max_retries=3,
                        session=FakeSession(responses), sleep=sleeps.append)
    return client, stats


def test_retry_after_is_respected():
    sleeps = []
    c, stats = make_client([FakeResp(429, {"Retry-After": "7"}), FakeResp(200)], sleeps)
    assert c.get("https://a.com/x").status_code == 200
    assert sleeps == [7.0]                      # รอตามที่เว็บบอกเป๊ะ
    assert stats.summary()["total"] == 2        # นับ retry ด้วย


def test_5xx_uses_exponential_backoff():
    sleeps = []
    c, _ = make_client([FakeResp(503), FakeResp(503), FakeResp(200)], sleeps)
    c.get("https://a.com/x")
    assert 1 <= sleeps[0] < 1.6 and 2 <= sleeps[1] < 2.6


def test_404_is_not_retried():
    sleeps = []
    c, _ = make_client([FakeResp(404)], sleeps)
    with pytest.raises(GiveUp):
        c.get("https://a.com/x")
    assert sleeps == []                         # ไม่รอ ไม่ลองซ้ำ


def test_network_error_gives_up_after_retries():
    sleeps = []
    c, stats = make_client([requests.ConnectionError("x")] * 4, sleeps)
    with pytest.raises(GiveUp):
        c.get("https://a.com/x")
    assert stats.summary()["total"] == 4 and len(sleeps) == 3


def test_retry_after_too_long_gives_up():
    c, _ = make_client([FakeResp(429, {"Retry-After": "3600"})], [])
    with pytest.raises(GiveUp):
        c.get("https://a.com/x")


def test_rate_limiter_spaces_requests_per_domain():
    t = [0.0]
    slept = []
    lim = DomainRateLimiter(20, clock=lambda: t[0], sleep=slept.append)
    for _ in range(3):
        lim.wait("a.com")
    lim.wait("b.com")
    assert slept == [3.0, 6.0]                  # a.com ห่าง 3 วิ, b.com ไม่ต้องรอ


def test_parse_retry_after():
    assert parse_retry_after("12") == 12
    assert parse_retry_after(None) is None
    assert parse_retry_after("ขยะ") is None


def test_classify_hook_turns_403_into_success():
    session = FakeSession([FakeResp(403)])
    stats = RequestStats()
    c = HttpClient(DomainRateLimiter(600, sleep=lambda s: None), stats, "t",
                   session=session, classify=lambda r: (True, False, "commentsDisabled"))
    assert c.get("https://a.com/x").status_code == 403    # คืน response ไม่ raise
    assert stats.summary()["success"] == 1                # นับเป็นสำเร็จ