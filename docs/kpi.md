# KPI Report

ช่วงข้อมูล: ต้น → สุด (run-24h)

## สรุป KPI

| KPI | ผล | เป้า | ผ่าน |
|---|---|---|---|
| Freshness ข่าว khaosod | 97.8% ≤ 15 นาที, median 3.9, p95 6.9 นาที (n=186) | ≥ 90% ≤ 15 นาที, p95 ≤ 30 | ✅ |
| Coverage ข่าว khaosod | 99.0% (298/301) | ≥ 95% | ✅ |
| Freshness ข่าว thaipbs | 7.0% ≤ 15 นาที, median 33.4, p95 105.7 นาที (n=57) | ≥ 90% ≤ 15 นาที, p95 ≤ 30 | ❌ |
| Coverage ข่าว thaipbs | 100.0% (111/111) | ≥ 95% | ✅ |
| Freshness YouTube | median 34.9, p95 67.9 นาที, 92.2% ≤ 60 (n=2558) | ≤ 60 นาที | ❌ |
| Success rate www.googleapis.com | 100.0% (866 req) | ≥ 98% | ✅ |
| Success rate www.khaosod.co.th | 98.21% (613 req) | ≥ 98% | ✅ |
| Success rate www.thaipbs.or.th | 99.98% (4,300 req) | ≥ 98% | ✅ |
| Round duration news/khaosod | median 0.9 วิ, max 200.5 วิ, 100.0% สั้นกว่าช่วงห่าง | สั้นกว่าช่วงห่าง | ✅ |
| Round duration news/thaipbs | median 16.6 วิ, max 226.0 วิ, 100.0% สั้นกว่าช่วงห่าง | สั้นกว่าช่วงห่าง | ✅ |
| Round duration youtube | median 96.2 วิ, max 96.6 วิ, 100.0% สั้นกว่าช่วงห่าง | สั้นกว่าช่วงห่าง | ✅ |
| Field completeness news | 99.76% | ≥ 99% | ✅ |
| Field completeness yt_search | 100.0% | ≥ 99% | ✅ |
| Field completeness yt_video | 98.86% | ≥ 99% | ❌ |
| Field completeness yt_comment | 99.98% | ≥ 99% | ✅ |
| ภาระ www.googleapis.com | avg 11.1 / max 20 req/นาที | ≤ 20 | ✅ |
| ภาระ www.khaosod.co.th | avg 2.0 / max 12 req/นาที | ≤ 20 | ✅ |
| ภาระ www.thaipbs.or.th | avg 11.2 / max 22 req/นาที | ≤ 20 | ❌ |

## ปริมาณข้อมูล

| แหล่ง | จำนวน |
|---|---:|
| ข่าว khaosod | 298 (backfill 112) |
| ข่าว thaipbs | 111 (backfill 54) |
| YouTube วิดีโอใหม่ | 2,558 |
| YouTube snapshot สถิติ | 12,353 |
| YouTube คอมเมนต์ | 4,371 |

## รอบการทำงาน

| แหล่ง | รอบ | ช่วงเวลา | duration median / p95 / max (วิ) | รอบ success ≥ 98% |
|---|---:|---|---|---:|
| news/khaosod | 305 | 11-25T20:34 → 11-26T22:02 | 0.9 / 15.5 / 200.5 | 96.4% |
| news/thaipbs | 306 | 11-25T20:34 → 11-26T22:02 | 16.6 / 91.7 / 226.0 | 99.7% |
| youtube | 27 | 11-25T20:34 → 11-26T21:44 | 96.2 / 96.5 / 96.6 | 100.0% |
|  ↳ โควตา YouTube สูงสุดต่อวัน | | {'2026-11-25': 1531, '2026-11-26': 5981} | | |

## ที่มาของ latency ข่าว (นาที)

| เว็บ | ฝั่งเว็บ: published → เห็นบนหน้ารายการ | ฝั่งเรา: เห็น → เก็บได้ |
|---|---|---|
| khaosod | median 3.8, p95 6.7 | median 0.1, p95 0.2 |
| thaipbs | median 33.2, p95 105.4 | median 0.3, p95 1.5 |

หมายเหตุ: ฝั่งเว็บรวมเวลารอรอบถัดไป (สูงสุด 5 นาที) เพราะเห็นลิงก์ได้เฉพาะตอนที่บอทเช็ค
