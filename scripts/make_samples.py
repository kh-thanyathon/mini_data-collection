"""ตัดตัวอย่างข้อมูลจริงมาไว้ใน samples/ (ไฟล์ละไม่กี่บรรทัด)"""
from pathlib import Path

SRC = Path("run-24h")
DST = Path("samples")
N = 3

def head(src: Path, dst: Path, n: int = N) -> None:
    lines = src.read_text(encoding="utf-8").splitlines()[:n]
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(lines) + "\n", encoding="utf-8")   # utf-8 ไม่มี BOM
    print(f"{dst}  ({len(lines)} บรรทัด)")

# ข่าว: ไฟล์แรกของแต่ละเว็บ
for site in ["khaosod", "thaipbs"]:
    first = sorted((SRC / "data/news" / site).glob("*.jsonl"))[0]
    head(first, DST / f"news_{site}.jsonl")

# YouTube: รอบที่ 2 (รอบแรกมักเป็น backfill)
rounds = sorted(p for p in (SRC / "data/youtube").iterdir() if p.is_dir())
yt = rounds[1]
for name in ["search", "videos", "comments", "comment_status"]:
    head(yt / f"{name}.jsonl", DST / f"youtube_{name}.jsonl")

# เพิ่มตัวอย่างวิดีโอที่ปิดคอมเมนต์ (โจทย์ถามเรื่องนี้โดยตรง)
for p in sorted((SRC / "data/youtube").glob("*/comment_status.jsonl")):
    hit = [l for l in p.read_text(encoding="utf-8").splitlines() if "commentsDisabled" in l]
    if hit:
        with (DST / "youtube_comment_status.jsonl").open("a", encoding="utf-8") as f:
            f.write(hit[0] + "\n")
        print(f"เพิ่ม commentsDisabled จาก {p.parent.name}")
        break