from collector.alerts import Alerter, http_status_from_error
from collector.config import AlertConfig
from collector.http_client import GiveUp


def make(tmp_path, cooldown=30):
    sent = []
    a = Alerter(AlertConfig(cooldown_minutes=cooldown), state_path=tmp_path / "alerts.json",
                sender=lambda m: sent.append(m) or True)
    return a, sent


def rlog(source="news/x", items=None, errors=None, success=1.0, total=20,
         fresh=None, duration_ok=True):
    return {"source": source, "items": items or {"listing_links": 50, "saved": 5, "failed": 0},
            "errors": errors or [], "requests": {"total": total, "success_rate": success},
            "freshness": fresh or {"n": 0}, "duration_ok": duration_ok,
            "duration_s": 40, "interval_s": 300}


def test_healthy_round_sends_nothing(tmp_path):
    a, sent = make(tmp_path)
    a.check_round(rlog())
    assert sent == []


def test_cooldown_then_resolve(tmp_path):
    a, sent = make(tmp_path)
    bad = rlog(success=0.5)
    a.check_round(bad)
    a.check_round(bad)                       # ภายใน cooldown -> ไม่ส่งซ้ำ
    assert len(sent) == 1 and "success rate 50.0%" in sent[0]
    a.check_round(rlog())                    # กลับมาปกติ -> ✅ ครั้งเดียว
    a.check_round(rlog())
    assert len(sent) == 2 and sent[1].startswith("✅")


def test_suppressed_count_shown_after_cooldown(tmp_path):
    a, sent = make(tmp_path, cooldown=0)     # cooldown 0 = ส่งได้ทุกครั้ง
    a.state["k"] = {"suppressed": 4}
    a.fire("k", "ปัญหา")
    assert "เกิดซ้ำอีก 4 ครั้ง" in sent[0]


def test_blocked_vs_down(tmp_path):
    a, sent = make(tmp_path)
    a.check_round(rlog(items={"listing_links": 0, "listing_error_status": 403},
                       errors=["listing: HTTP 403 https://x"]))
    assert len(sent) == 1 and "ถูกบล็อก" in sent[0]           # ไม่แจ้ง layout ซ้ำซ้อน
    a2, sent2 = make(tmp_path / "b")
    a2.check_round(rlog(items={"listing_links": 0}, errors=["listing: network error"]))
    assert len(sent2) == 1 and "โหลดหน้ารายการไม่ได้" in sent2[0]


def test_layout_change_and_parse_failures(tmp_path):
    a, sent = make(tmp_path)
    a.check_round(rlog(items={"listing_links": 0}))
    a.check_round(rlog(items={"listing_links": 30, "saved": 2, "failed": 3}))
    assert "layout" in sent[0]
    assert sent[1].startswith("✅")                          # รอบสองเจอลิงก์แล้ว -> layout หาย
    assert "parse ข่าวไม่ได้ 3/5" in sent[2]


def test_latency_needs_enough_samples(tmp_path):
    a, sent = make(tmp_path)
    a.check_round(rlog(fresh={"n": 2, "p95_min": 90}))        # ตัวอย่างน้อยเกิน ไม่ตัดสิน
    assert sent == []
    a.check_round(rlog(fresh={"n": 5, "p95_min": 45}))
    assert "p95 = 45" in sent[0]


def test_low_request_round_not_judged(tmp_path):
    a, sent = make(tmp_path)
    a.check_round(rlog(success=0.67, total=3))
    assert sent == []


def test_youtube_quota(tmp_path):
    a, sent = make(tmp_path)
    a.check_round(rlog(source="youtube", items={}, errors=["quota guard: used 9000"]))
    assert "โควตา" in sent[0]


def test_http_status_from_error():
    class R:
        status_code = 429
    assert http_status_from_error(GiveUp("x", R())) == 429
    assert http_status_from_error(GiveUp("HTTP 403 https://x")) == 403
    assert http_status_from_error(GiveUp("network error")) is None