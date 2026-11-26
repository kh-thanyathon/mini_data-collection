"""ตรวจคุณภาพข้อมูลทั้งหมด -> % missing, duplicate, malformed ต่อชนิดข้อมูล

    python scripts/quality_report.py                    # ตรวจ data/
    python scripts/quality_report.py --data run-24h/data

ผลลัพธ์:
    reports/quality.md            ตารางสรุป (ใส่ README ได้)
    reports/quality.json          ตัวเลขทั้งหมด (ให้สคริปต์ KPI บทที่ 12 อ่านต่อ)
    reports/quality_issues.jsonl  record ที่ไม่ผ่าน: ไฟล์ บรรทัด ปัญหา (ไม่ลบข้อมูลต้นฉบับ)
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector.config import DATA_DIR, ROOT  # noqa: E402
from collector.quality import REQUIRED, check, dedup_key  # noqa: E402
from collector.storage import read_jsonl  # noqa: E402


def sources(data_dir: Path) -> dict[str, list[Path]]:
    """ชนิดข้อมูล -> ไฟล์ทั้งหมดของชนิดนั้น"""
    yt = data_dir / "youtube"
    return {
        "news": sorted((data_dir / "news").glob("*/*.jsonl")),
        "yt_search": sorted(yt.glob("*/search.jsonl")),
        "yt_video": sorted(yt.glob("*/videos.jsonl")),
        "yt_comment": sorted(yt.glob("*/comments.jsonl")),
    }


def audit(kind: str, files: list[Path], data_dir: Path, issues: list[dict]) -> dict:
    total = bad_json = with_missing = with_malformed = 0
    missing_by_field, malformed_by_rule = Counter(), Counter()
    first_seen: dict[tuple, str] = {}
    duplicates = 0

    for f in files:
        rel = f.relative_to(data_dir).as_posix() 
        for line_no, rec in read_jsonl(f):
            total += 1
            where = f"{rel}:{line_no}"
            if rec is None:
                bad_json += 1
                issues.append({"kind": kind, "where": where, "problems": ["bad JSON line"]})
                continue

            q = check(kind, rec)
            missing_by_field.update(q["missing"])
            malformed_by_rule.update(m.split(":")[0] if ":" in m else m for m in q["malformed"])
            with_missing += bool(q["missing"])
            with_malformed += bool(q["malformed"])

            problems = [f"missing {m}" for m in q["missing"]] + q["malformed"]
            key = dedup_key(kind, rec)
            if key in first_seen:
                duplicates += 1
                problems.append(f"duplicate of {first_seen[key]}")
            else:
                first_seen[key] = where
            if problems:
                issues.append({"kind": kind, "where": where, "key": list(key), "problems": problems})

    def pct(n):
        return round(n / total * 100, 2) if total else None

    complete = total - with_missing - bad_json
    return {
        "files": len(files), "records": total,
        "field_completeness_pct": pct(complete),           # KPI: >= 99%
        "missing_pct": pct(with_missing), "missing_by_field": dict(missing_by_field),
        "malformed_pct": pct(with_malformed + bad_json), "malformed_by_rule": dict(malformed_by_rule),
        "bad_json_lines": bad_json,
        "duplicates": duplicates, "duplicate_pct": pct(duplicates),
        "unique_records": len(first_seen),
    }


LABEL = {"news": "ข่าว", "yt_search": "YouTube search", "yt_video": "YouTube videos (snapshot)",
         "yt_comment": "YouTube comments"}


def to_markdown(report: dict) -> str:
    lines = ["# Data Quality Report", "",
             f"ข้อมูลจาก `{report['data_dir']}`", "",
             "| ชนิด | records | field ครบ | missing | malformed | duplicate |",
             "|---|---:|---:|---:|---:|---:|"]
    for kind, r in report["kinds"].items():
        if not r["records"]:
            continue
        ok = "✅" if (r["field_completeness_pct"] or 0) >= 99 else "❌"
        lines.append(f"| {LABEL[kind]} | {r['records']:,} | {r['field_completeness_pct']}% {ok} | "
                     f"{r['missing_pct']}% | {r['malformed_pct']}% | {r['duplicates']} ({r['duplicate_pct']}%) |")
    lines += ["", "## รายละเอียด", ""]
    for kind, r in report["kinds"].items():
        if r["missing_by_field"] or r["malformed_by_rule"] or r["duplicates"]:
            lines.append(f"**{LABEL[kind]}**")
            if r["missing_by_field"]:
                lines.append(f"- missing ต่อ field: {r['missing_by_field']}")
            if r["malformed_by_rule"]:
                lines.append(f"- malformed ต่อกฎ: {r['malformed_by_rule']}")
            if r["duplicates"]:
                lines.append(f"- duplicate: {r['duplicates']} records (ดูใน quality_issues.jsonl)")
            lines.append("")
    lines += ["## กฎที่ใช้", "",
              "- **missing:** field บังคับเป็น None, สตริงว่าง หรือ list ว่าง "
              f"(ข่าว: {', '.join(REQUIRED['news'])})",
              "- **malformed:** เวลาไม่ใช่ ISO 8601 + timezone, published_at หลัง collected_at เกิน 5 นาที, "
              "URL ไม่ใช่ http(s), เนื้อหาสั้นกว่า 50 ตัวอักษร, category ยังเป็น list ที่ encode, "
              "ตัวเลขติดลบ/ไม่ใช่ int, duration แปลงไม่ได้, บรรทัด JSON เสีย",
              "- **duplicate:** key เดียวกันปรากฏมากกว่า 1 ครั้งข้ามทุกไฟล์ "
              "(ข่าว = site+article_id, คอมเมนต์ = comment_id, สถิติวิดีโอ = video_id+round_id)",
              "- **ข้อมูลที่ไม่ผ่าน:** ไม่ลบจากไฟล์ต้นฉบับ แต่ระบุตำแหน่งไว้ใน quality_issues.jsonl "
              "ให้ขั้นตอนถัดไปกรองออกได้"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=DATA_DIR)
    ap.add_argument("--out", type=Path, default=ROOT / "reports")
    args = ap.parse_args(argv)

    issues: list[dict] = []
    report = {"data_dir": str(args.data),
              "kinds": {k: audit(k, files, args.data, issues) for k, files in sources(args.data).items()}}

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "quality.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out / "quality.md").write_text(to_markdown(report), encoding="utf-8")
    with (args.out / "quality_issues.jsonl").open("w", encoding="utf-8") as f:
        for i in issues:
            f.write(json.dumps(i, ensure_ascii=False) + "\n")
    print(to_markdown(report))
    print(f"รายการที่มีปัญหา {len(issues)} รายการ -> {args.out / 'quality_issues.jsonl'}")
    return report


if __name__ == "__main__":
    main()
    