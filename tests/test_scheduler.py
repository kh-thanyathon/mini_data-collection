import threading
import time

from run import Job


class SpyAlerter:
    def __init__(self):
        self.fired, self.resolved = [], []

    def fire(self, key, msg):
        self.fired.append(key)

    def resolve(self, key, msg):
        self.resolved.append(key)


def wait_idle(job):
    for _ in range(100):
        if not job.running.locked():
            return
        time.sleep(0.01)


def test_runs_immediately_then_every_interval():
    t = [0.0]
    calls = []
    job = Job("x", lambda: calls.append(1), 5, SpyAlerter(), clock=lambda: t[0])
    assert job.tick() is True                    # รอบแรกรันทันที
    wait_idle(job)
    t[0] = 299
    assert job.tick() is False                   # ยังไม่ถึง 5 นาที
    t[0] = 300
    assert job.tick() is True
    wait_idle(job)
    assert len(calls) == 2


def test_no_drift_after_late_tick():
    t = [0.0]
    job = Job("x", lambda: None, 5, SpyAlerter(), clock=lambda: t[0])
    job.tick(); wait_idle(job)
    t[0] = 310                                   # tick ช้าไป 10 วินาที
    job.tick(); wait_idle(job)
    assert job.next_run == 600                   # รอบถัดไปยังเป็น 600 ไม่ใช่ 610


def test_overlapping_round_is_skipped_and_alerted():
    t = [0.0]
    gate = threading.Event()
    alerter = SpyAlerter()
    job = Job("slow", gate.wait, 5, alerter, clock=lambda: t[0])
    job.tick()                                   # รอบแรกค้างอยู่ (รอ gate)
    t[0] = 300
    assert job.tick() is False
    assert job.skipped == 1 and "slow:skipped" in alerter.fired
    gate.set(); wait_idle(job)


def test_crash_is_caught_alerted_and_recovers():
    t = [0.0]
    alerter = SpyAlerter()
    boom = [True]

    def fn():
        if boom[0]:
            raise ValueError("พัง")

    job = Job("j", fn, 5, alerter, clock=lambda: t[0])
    job.tick(); wait_idle(job)
    assert job.crashes == 1 and "j:crash" in alerter.fired
    boom[0] = False
    t[0] = 300
    job.tick(); wait_idle(job)
    assert job.rounds == 1 and "j:crash" in alerter.resolved

def test_another_instance_detection(tmp_path):
    import json
    from datetime import timedelta

    from collector.timeutil import now
    from run import another_instance_running

    hb = tmp_path / "heartbeat.json"
    assert another_instance_running(hb) is False                      # ยังไม่เคยรัน
    hb.write_text(json.dumps({"at": now().isoformat()}), encoding="utf-8")
    assert another_instance_running(hb) is True                       # อีกตัวเพิ่งเขียน
    old = (now() - timedelta(minutes=5)).isoformat()
    hb.write_text(json.dumps({"at": old}), encoding="utf-8")
    assert another_instance_running(hb) is False                      # ตัวเก่าตายไปแล้ว

def test_another_instance_detection(tmp_path):
    import json
    from datetime import timedelta

    from collector.timeutil import now
    from run import another_instance_running

    hb = tmp_path / "heartbeat.json"
    assert another_instance_running(hb) is False                      # ยังไม่เคยรัน
    hb.write_text(json.dumps({"at": now().isoformat()}), encoding="utf-8")
    assert another_instance_running(hb) is True                       # อีกตัวเพิ่งเขียน
    old = (now() - timedelta(minutes=5)).isoformat()
    hb.write_text(json.dumps({"at": old}), encoding="utf-8")
    assert another_instance_running(hb) is False                      # ตัวเก่าตายไปแล้ว