"""Log สรุปต่อรอบ — 1 รอบ = 1 ไฟล์ JSON + ทุก request ต่อท้ายใน requests.jsonl

ค่าที่โจทย์บังคับ: ปริมาณข้อมูล, อัตราความสำเร็จของ request, ระยะเวลาทั้งรอบ,
freshness latency และจำนวน request ต่อเว็บ
"""
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .storage import append_jsonl
from .timeutil import iso, now


def percentile(values: list[float], p: float) -> float | None:
    """nearest-rank: เรียงค่าแล้วหยิบตัวที่ ceil(p% x n) — อธิบายง่าย ตรวจมือได้"""
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, math.ceil(p / 100 * len(s)) - 1))
    return s[k]


def latency_summary(values: list[float], target_min: float) -> dict:
    if not values:
        return {"n": 0, "target_min": target_min}
    return {
        "n": len(values),
        "median_min": round(statistics.median(values), 1),
        "p95_min": round(percentile(values, 95), 1),
        "max_min": round(max(values), 1),
        "pct_within_target": round(sum(v <= target_min for v in values) / len(values) * 100, 1),
        "target_min": target_min,
    }


def requests_summary(records: list[dict]) -> dict:
    """success rate รวม + ต่อโดเมน และจำนวน request ต่อนาที (เฉลี่ย/สูงสุด)"""
    per_domain: dict[str, dict] = {}
    for r in records:
        d = per_domain.setdefault(r["domain"], {"total": 0, "ok": 0,
                                                "per_minute": defaultdict(int)})
        d["total"] += 1
        d["ok"] += int(r["ok"])
        d["per_minute"][r["ts"][:16]] += 1          # "2026-11-25T13:05" = นาทีเดียวกัน
    out = {}
    for dom, d in per_domain.items():
        counts = list(d["per_minute"].values())
        out[dom] = {"requests": d["total"],
                    "success_rate": round(d["ok"] / d["total"], 4),
                    "max_per_min": max(counts),
                    "avg_per_active_min": round(sum(counts) / len(counts), 1)}
    total = len(records)
    ok = sum(int(r["ok"]) for r in records)
    return {"total": total,
            "success_rate": round(ok / total, 4) if total else None,
            "per_domain": out}


def finish_round(source: str, started: datetime, summary: dict, records: list[dict],
                 interval_min: int, latency_target_min: float, log_dir: Path) -> dict:
    """สร้าง log ของรอบจาก summary ที่ collector คืนมา แล้วเขียนลงไฟล์"""
    ended = now()
    duration = (ended - started).total_seconds()
    items = {k: v for k, v in summary.items()
             if isinstance(v, (int, float)) and not isinstance(v, bool) and k != "duration_s"}
    log = {
        "source": source,
        "round_id": summary["round_id"],
        "started_at": iso(started),
        "ended_at": iso(ended),
        "duration_s": round(duration, 1),
        "interval_s": interval_min * 60,
        "duration_ok": duration < interval_min * 60,        # KPI: รอบต้องจบก่อนรอบถัดไป
        "items": items,                                      # ปริมาณข้อมูล
        "requests": requests_summary(records),               # success rate + ต่อเว็บ
        "freshness": latency_summary(summary.get("latency_min", []), latency_target_min),
        "errors": summary.get("errors", [])[:50],
    }
    folder = log_dir / source
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{summary['round_id']}.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    # ทุก request ของรอบ -> ไฟล์เดียวต่อแหล่ง ใช้พิสูจน์ req/นาที จาก log จริงในรายงาน
    append_jsonl(folder / "requests.jsonl", [{**r, "round_id": summary["round_id"]} for r in records])
    return log