# Data Quality Report

ข้อมูลจาก `run-24h\data`

| ชนิด | records | field ครบ | missing | malformed | duplicate |
|---|---:|---:|---:|---:|---:|
| ข่าว | 409 | 99.76% ✅ | 0.24% | 0.0% | 0 (0.0%) |
| YouTube search | 2,558 | 100.0% ✅ | 0.0% | 0.0% | 0 (0.0%) |
| YouTube videos (snapshot) | 12,353 | 98.86% ❌ | 1.14% | 0.0% | 0 (0.0%) |
| YouTube comments | 4,371 | 99.98% ✅ | 0.02% | 0.0% | 0 (0.0%) |

## รายละเอียด

**ข่าว**
- missing ต่อ field: {'content': 1}

**YouTube videos (snapshot)**
- missing ต่อ field: {'duration': 141}

**YouTube comments**
- missing ต่อ field: {'text': 1}

## กฎที่ใช้

- **missing:** field บังคับเป็น None, สตริงว่าง หรือ list ว่าง (ข่าว: url, title, published_at, collected_at, content, images, category)
- **malformed:** เวลาไม่ใช่ ISO 8601 + timezone, published_at หลัง collected_at เกิน 5 นาที, URL ไม่ใช่ http(s), เนื้อหาสั้นกว่า 50 ตัวอักษร, category ยังเป็น list ที่ encode, ตัวเลขติดลบ/ไม่ใช่ int, duration แปลงไม่ได้, บรรทัด JSON เสีย
- **duplicate:** key เดียวกันปรากฏมากกว่า 1 ครั้งข้ามทุกไฟล์ (ข่าว = site+article_id, คอมเมนต์ = comment_id, สถิติวิดีโอ = video_id+round_id)
- **ข้อมูลที่ไม่ผ่าน:** ไม่ลบจากไฟล์ต้นฉบับ แต่ระบุตำแหน่งไว้ใน quality_issues.jsonl ให้ขั้นตอนถัดไปกรองออกได้
