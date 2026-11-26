"""รวม log ทุกรอบ -> รายงาน KPI ไฟล์เดียว

    python scripts/kpi_report.py --root run-24h
    python scripts/kpi_report.py --root run-24h --since 2026-11-25T21:42 --until 2026-11-26T21:42

อ่านจาก <root>/data, <root>/logs, <root>/state (+ reports/quality.json ถ้ามี)
เขียน reports/kpi.md และ reports/kpi.json
"""
import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector.config import ROOT  # noqa: E402
from collector.roundlog import percentile  # noqa: E402
from collector.storage import load_json, read_jsonl  # noqa: E402
from collector.timeutil import latency_minutes, parse_iso  # noqa: E402

NEWS_TARGET, NEWS_P95_TARGET, YT_TARGET = 15, 30, 60


class Window:
    """กรองตามช่วงเวลา (ไม่ระบุ = ทั้งหมด)"""

    def __init__(self, since: str | None, until: str | None):
        self.since, self.until = parse_iso(since), parse_iso(until)

    def ok(self, ts: str | None) -> bool:
        t = parse_iso(ts)
        if t is None:
            return False
        return (not self.since or t >= self.since) and (not self.until or t < self.until)


def dist(values: list[float], target: float | None = None) -> dict:
    if not values:
        return {"n": 0}
    out = {"n": len(values), "median": round(statistics.median(values), 1),
           "p95": round(percentile(values, 95), 1), "max": round(max(values), 1)}
    if target is not None:
        out["pct_within_target"] = round(sum(v <= target for v in values) / len(values) * 100, 1)
        out["target"] = target
    return out


# ---------------------------------------------------------------- freshness + coverage (ข่าว)
def news_kpis(root: Path, w: Window) -> dict:
    out = {}
    for site_dir in sorted((root / "data" / "news").glob("*")):
        site = site_dir.name
        recs = [r for f in sorted(site_dir.glob("*.jsonl")) for _, r in read_jsonl(f)
                if r and w.ok(r.get("collected_at"))]
        fresh = [r for r in recs if not r.get("backfill")]
        lat = [x for x in (latency_minutes(r["published_at"], r["collected_at"]) for r in fresh) if x is not None]
        # แยกที่มาของ latency: ฝั่งเว็บ (ขึ้นหน้ารายการช้า + รอรอบ) vs ฝั่งเรา (คิว/โหลด)
        web = [x for x in (latency_minutes(r["published_at"], r.get("first_seen_at")) for r in fresh) if x is not None]
        ours = [x for x in (latency_minutes(r.get("first_seen_at"), r["collected_at"]) for r in fresh) if x is not None]

        # coverage: ข่าวที่เก็บได้ ÷ ข่าวทุกข่าวที่ปรากฏบนหน้ารายการ (ทั้งช่วงที่รัน: state คือภาพ ณ ตอนจบ)
        #   ปรากฏ = เก็บได้ + เลิกลองแล้ว (อยู่ใน seen แต่ไม่มีในไฟล์) + ยังค้างรอเก็บ (first_seen)
        saved = {r["article_id"] for f in site_dir.glob("*.jsonl") for _, r in read_jsonl(f) if r}
        seen = set(load_json(root / "state" / f"news_seen_{site}.json", {}))
        pending = set(load_json(root / "state" / f"news_first_seen_{site}.json", {})) - saved
        gave_up = seen - saved
        appeared = saved | gave_up | pending
        out[site] = {
            "records": len(recs), "backfill": len(recs) - len(fresh),
            "freshness_min": dist(lat, NEWS_TARGET),
            "pct_within_p95_target": round(sum(x <= NEWS_P95_TARGET for x in lat) / len(lat) * 100, 1) if lat else None,
            "latency_breakdown_min": {"web_side_published_to_first_seen": dist(web),
                                      "our_side_first_seen_to_collected": dist(ours)},
            "coverage": {"appeared_on_listing": len(appeared), "collected": len(saved),
                         "gave_up": len(gave_up), "still_pending": len(pending),
                         "pct": round(len(saved) / len(appeared) * 100, 2) if appeared else None},
        }
    return out


# ---------------------------------------------------------------- freshness (YouTube)
def youtube_kpis(root: Path, w: Window) -> dict:
    yt = root / "data" / "youtube"
    recs = [r for f in sorted(yt.glob("*/search.jsonl")) for _, r in read_jsonl(f) if r and w.ok(r.get("collected_at"))]
    lat = [x for x in (latency_minutes(r["published_at"], r["collected_at"]) for r in recs) if x is not None]
    count = lambda name: sum(1 for f in yt.glob(f"*/{name}") for _, r in read_jsonl(f)  # noqa: E731
                             if r and w.ok(r.get("collected_at")))
    return {"new_videos": len(recs), "video_snapshots": count("videos.jsonl"),
            "comments": count("comments.jsonl"), "freshness_min": dist(lat, YT_TARGET)}


# ---------------------------------------------------------------- รอบ: duration, success rate, โควตา
def round_kpis(root: Path, w: Window) -> dict:
    out = {}
    for f in sorted((root / "logs").rglob("*.json")):
        log = json.loads(f.read_text(encoding="utf-8"))
        if "round_id" not in log or not w.ok(log.get("started_at")):
            continue
        s = out.setdefault(log["source"], {"rounds": 0, "durations": [], "ok_dur": 0, "rates": [],
                                           "first": log["started_at"], "last": log["ended_at"],
                                           "quota": defaultdict(int)})
        s["rounds"] += 1
        s["durations"].append(log["duration_s"])
        s["ok_dur"] += bool(log["duration_ok"])
        if log["requests"]["success_rate"] is not None:
            s["rates"].append(log["requests"]["success_rate"])
        s["first"], s["last"] = min(s["first"], log["started_at"]), max(s["last"], log["ended_at"])
        if "quota_today" in log["items"]:                               # YouTube: ค่าสูงสุดของแต่ละวัน (PT)
            day = log["started_at"][:10]
            s["quota"][day] = max(s["quota"][day], log["items"]["quota_today"])
    result = {}
    for src, s in out.items():
        result[src] = {
            "rounds": s["rounds"], "from": s["first"], "to": s["last"],
            "duration_s": dist(s["durations"]),
            "pct_rounds_shorter_than_interval": round(s["ok_dur"] / s["rounds"] * 100, 1),
            "pct_rounds_success_ge_98": round(sum(r >= 0.98 for r in s["rates"]) / len(s["rates"]) * 100, 1)
            if s["rates"] else None,
        }
        if s["quota"]:
            result[src]["quota_used_max_per_day"] = dict(s["quota"])
    return result


# ---------------------------------------------------------------- request: success rate + ต่อนาทีต่อโดเมน
def request_kpis(root: Path, w: Window) -> dict:
    per_min = defaultdict(lambda: defaultdict(int))
    tot, ok = defaultdict(int), defaultdict(int)
    for f in (root / "logs").rglob("requests.jsonl"):
        for _, r in read_jsonl(f):
            if r and w.ok(r.get("ts")):
                per_min[r["domain"]][r["ts"][:16]] += 1
                tot[r["domain"]] += 1
                ok[r["domain"]] += int(r["ok"])
    out = {}
    for d, mins in per_min.items():
        v = list(mins.values())
        out[d] = {"requests": tot[d], "success_rate_pct": round(ok[d] / tot[d] * 100, 2),
                  "avg_per_active_min": round(sum(v) / len(v), 1), "max_per_min": max(v)}
    return out


# ---------------------------------------------------------------- markdown
def mark(ok) -> str:
    return "—" if ok is None else ("✅" if ok else "❌")


def to_markdown(r: dict) -> str:
    L = ["# KPI Report", "", f"ช่วงข้อมูล: {r['window']}", "", "## สรุป KPI", "",
         "| KPI | ผล | เป้า | ผ่าน |", "|---|---|---|---|"]
    for site, n in r["news"].items():
        f = n["freshness_min"]
        if f["n"]:
            L.append(f"| Freshness ข่าว {site} | {f['pct_within_target']}% ≤ 15 นาที, median {f['median']}, "
                     f"p95 {f['p95']} นาที (n={f['n']}) | ≥ 90% ≤ 15 นาที, p95 ≤ 30 | "
                     f"{mark(f['pct_within_target'] >= 90 and f['p95'] <= 30)} |")
        c = n["coverage"]
        L.append(f"| Coverage ข่าว {site} | {c['pct']}% ({c['collected']}/{c['appeared_on_listing']}) | ≥ 95% | "
                 f"{mark(c['pct'] is not None and c['pct'] >= 95)} |")
    f = r["youtube"]["freshness_min"]
    if f["n"]:
        L.append(f"| Freshness YouTube | median {f['median']}, p95 {f['p95']} นาที, "
                 f"{f['pct_within_target']}% ≤ 60 (n={f['n']}) | ≤ 60 นาที | {mark(f['p95'] <= 60)} |")
    for d, q in r["requests"].items():
        L.append(f"| Success rate {d} | {q['success_rate_pct']}% ({q['requests']:,} req) | ≥ 98% | "
                 f"{mark(q['success_rate_pct'] >= 98)} |")
    for src, s in r["rounds"].items():
        L.append(f"| Round duration {src} | median {s['duration_s']['median']} วิ, max {s['duration_s']['max']} วิ, "
                 f"{s['pct_rounds_shorter_than_interval']}% สั้นกว่าช่วงห่าง | สั้นกว่าช่วงห่าง | "
                 f"{mark(s['pct_rounds_shorter_than_interval'] == 100)} |")
    for kind, q in (r.get("quality") or {}).items():
        if q.get("records"):
            L.append(f"| Field completeness {kind} | {q['field_completeness_pct']}% | ≥ 99% | "
                     f"{mark(q['field_completeness_pct'] >= 99)} |")
    for d, q in r["requests"].items():
        L.append(f"| ภาระ {d} | avg {q['avg_per_active_min']} / max {q['max_per_min']} req/นาที | ≤ 20 | "
                 f"{mark(q['max_per_min'] <= 20)} |")

    L += ["", "## ปริมาณข้อมูล", "", "| แหล่ง | จำนวน |", "|---|---:|"]
    for site, n in r["news"].items():
        L.append(f"| ข่าว {site} | {n['records']:,} (backfill {n['backfill']}) |")
    y = r["youtube"]
    L += [f"| YouTube วิดีโอใหม่ | {y['new_videos']:,} |", f"| YouTube snapshot สถิติ | {y['video_snapshots']:,} |",
          f"| YouTube คอมเมนต์ | {y['comments']:,} |"]

    L += ["", "## รอบการทำงาน", "", "| แหล่ง | รอบ | ช่วงเวลา | duration median / p95 / max (วิ) | รอบ success ≥ 98% |",
          "|---|---:|---|---|---:|"]
    for src, s in r["rounds"].items():
        d = s["duration_s"]
        L.append(f"| {src} | {s['rounds']} | {s['from'][5:16]} → {s['to'][5:16]} | "
                 f"{d['median']} / {d['p95']} / {d['max']} | {s['pct_rounds_success_ge_98']}% |")
        if "quota_used_max_per_day" in s:
            L.append(f"|  ↳ โควตา YouTube สูงสุดต่อวัน | | {s['quota_used_max_per_day']} | | |")

    L += ["", "## ที่มาของ latency ข่าว (นาที)", "",
          "| เว็บ | ฝั่งเว็บ: published → เห็นบนหน้ารายการ | ฝั่งเรา: เห็น → เก็บได้ |", "|---|---|---|"]
    for site, n in r["news"].items():
        a, b = n["latency_breakdown_min"].values()
        if a["n"]:
            L.append(f"| {site} | median {a['median']}, p95 {a['p95']} | median {b['median']}, p95 {b['p95']} |")
    L += ["", "หมายเหตุ: ฝั่งเว็บรวมเวลารอรอบถัดไป (สูงสุด 5 นาที) เพราะเห็นลิงก์ได้เฉพาะตอนที่บอทเช็ค"]
    return "\n".join(L) + "\n"


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=ROOT, help="โฟลเดอร์ที่มี data/ logs/ state/")
    ap.add_argument("--since"), ap.add_argument("--until")
    ap.add_argument("--out", type=Path, default=ROOT / "reports")
    a = ap.parse_args(argv)
    w = Window(a.since, a.until)
    quality = load_json(a.out / "quality.json", {}).get("kinds")
    report = {"window": f"{a.since or 'ต้น'} → {a.until or 'สุด'} ({a.root})",
              "news": news_kpis(a.root, w), "youtube": youtube_kpis(a.root, w),
              "rounds": round_kpis(a.root, w), "requests": request_kpis(a.root, w), "quality": quality}
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "kpi.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (a.out / "kpi.md").write_text(to_markdown(report), encoding="utf-8")
    print(to_markdown(report))
    return report


if __name__ == "__main__":
    main()