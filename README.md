# Mini Data Collector

ระบบเก็บข้อมูลขนาดเล็กสำหรับโจทย์ Data Collection Engineer

- **ส่วน A:** YouTube Data API v3 (search + pagination, สถิติวิดีโอ, คอมเมนต์)
- **ส่วน B:** ข่าวจาก **ข่าวสด** (requests + BeautifulSoup) และ **Thai PBS** (Playwright)

ข้อมูลเก็บเป็นไฟล์ JSONL มี log ต่อรอบ รายงาน KPI และแจ้งเตือนผ่าน Telegram

> **ผลการรันจริง:** 25 พ.ย. 2026 20:34 → 26 พ.ย. 2026 22:05 (≈ 25.5 ชั่วโมง) ข่าว 305 รอบต่อเว็บ, YouTube 27 รอบ
> มีช่วงหยุด 9 นาที (21:33–21:42) อธิบายไว้ในข้อ 6 · ตัวเลขทั้งหมดใน README นี้มาจาก [`docs/kpi.md`](docs/kpi.md) และ [`docs/quality.md`](docs/quality.md)

---

## ติดตั้งและรัน

```bash
cp .env.example .env        # ใส่ YOUTUBE_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
docker compose up -d --build   # คำสั่งเดียว รันทั้งระบบ (scheduler อยู่ใน container)
docker compose logs -f         # ดู log
docker compose down            # หยุด
```

รันบนเครื่องโดยไม่ใช้ Docker (Python 3.12+):

```bash
python -m venv .venv && .venv\Scripts\activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python run.py                 # scheduler
python run.py news            # ข่าว 1 รอบ
python run.py youtube         # YouTube 1 รอบ
python run.py test-alert      # ทดสอบ Telegram
pytest                        # unit test (77 ข้อ)
```

สร้างรายงาน:

```bash
python scripts/quality_report.py --data data        # คุณภาพข้อมูล -> reports/quality.md
python scripts/kpi_report.py --root .               # KPI ทั้งหมด  -> reports/kpi.md
```

## โครงสร้าง

```
collector/
  config.py        ค่าตั้งจาก .env + NewsSite
  timeutil.py      เวลาแบบมี timezone ทั้งระบบ
  http_client.py   rate limit ต่อโดเมน, retry + backoff, Retry-After, นับทุก request
  storage.py       JSONL, atomic write, SeenStore (กันซ้ำ)
  youtube.py       ส่วน A
  news.py          ส่วน B (requests + Playwright)
  roundlog.py      log ต่อรอบ
  alerts.py        Telegram + กฎแจ้งเตือน
  quality.py       กฎ missing / malformed / duplicate
run.py             scheduler (thread ต่องาน) + คำสั่งรันรอบเดียว
sites.json         นิยามเว็บข่าว (เพิ่มเว็บได้โดยไม่แก้โค้ด)
scripts/           quality_report.py, kpi_report.py
samples/           ตัวอย่างข้อมูลและ log จากการรันจริง
docs/              รายงาน KPI, คุณภาพข้อมูล, screenshot แจ้งเตือน, รายงาน 1 หน้า
```

ผลลัพธ์ตอนรัน:

```
data/youtube/<round_id>/{search,videos,comments,comment_status}.jsonl
data/news/<site>/<YYYY-MM-DD>.jsonl
logs/<source>/<round_id>.json      สรุป 1 รอบ
logs/<source>/requests.jsonl       ทุก request (ใช้พิสูจน์ req/นาที)
state/                             SeenStore, โควตา, heartbeat, สถานะแจ้งเตือน
```

---

## 1. Architecture

```
                  run.py scheduler (process เดียว ตรวจทุก 5 วินาที)
     ┌─────────────────────┬──────────────────────┬──────────────────────┐
     │ youtube (60 นาที)   │ news/khaosod (5 นาที) │ news/thaipbs (5 นาที) │   1 งาน = 1 thread
     ▼                     ▼                      ▼                          กันรอบซ้อนด้วย Lock
 YouTubeCollector     NewsCollector           NewsCollector
 search→videos→       listing (requests)      listing (Playwright)
 comments             → article (requests)    → article (requests)
     │                     │                      │
     └──────── HttpClient: rate limit ต่อโดเมน, retry, Retry-After, นับ request ────────┘
                           │
          data/*.jsonl ◄───┼───► state/ (seen, โควตา, first_seen, heartbeat)
                           ▼
              roundlog.finish_round ──► logs/<source>/<round>.json + requests.jsonl
                           │
                           ▼
              Alerter.check_round ──► Telegram
                           │
    scripts/quality_report.py + kpi_report.py ──► reports/*.md
```

- **Scheduler:** นับรอบถัดไปจากเวลาที่ "ควร" รัน จึงไม่เลื่อน (ข่าวรันครบ 293 รอบใน 24 ชม. 20 นาทีหลังเริ่มต่อเนื่อง ตรงกับที่คำนวณ)
- **Collector ไม่รู้เรื่องแจ้งเตือน:** Alerter ตัดสินจาก log ของรอบ สิ่งที่แจ้งกับสิ่งที่อยู่ใน log จึงตรงกันเสมอ
- **heartbeat:** เขียนทุก 5 วินาที ใช้ทั้ง Docker healthcheck และกันรัน scheduler ซ้อน

## 2. การตรวจจับข้อมูลใหม่และกันซ้ำ

**ข่าว:**
1. โหลดหน้ารายการทุกรอบ (ข่าวสดใช้ conditional GET ด้วย ETag)
2. ดึงลิงก์ตาม regex แล้ว normalize URL (ตัด query, fragment, `/` ท้าย)
3. **กันซ้ำด้วยเลขข่าว ไม่ใช่ URL** เพราะข่าวเดียวกันของข่าวสดมีหลาย path (`/update-news/news_10483352` กับ `/sports/news_10483352`)
4. โหลดเฉพาะเลขข่าวที่ไม่อยู่ใน SeenStore ข่าวเดิมไม่ถูกโหลดซ้ำ
5. Thai PBS อ่านลิงก์จากทั้ง `/news/today` และ `/news` เพราะ `/news/today` เปลี่ยนวันตอนเที่ยงคืน ข่าวช่วง 23:5x อาจหลุดถ้าอ่านหน้าเดียว

**YouTube:**
- `publishedAfter` = เวลาเริ่มรอบล่าสุดที่สำเร็จ − 15 นาที (OVERLAP กันคลิปที่เข้า search index ช้า) แล้วกรองด้วย SeenStore ของ video_id
- เลื่อนหน้าต่างเวลาเฉพาะรอบที่สำเร็จ รอบที่พังจะถูกค้นย้อนในรอบถัดไป
- คอมเมนต์กันซ้ำด้วย comment_id
- `videos.jsonl` เป็น snapshot ทุกชั่วโมงโดยตั้งใจ key = (video_id, round_id) จึงไม่ถือว่าซ้ำ

**ลำดับการเขียน:** เขียนไฟล์ก่อน แล้วค่อย mark ว่าเห็นแล้ว (at-least-once) ถ้าบอทตายกลางทางอย่างแย่สุดคือได้ข้อมูลซ้ำซึ่งตรวจเจอได้ ไม่ใช่ข้อมูลหาย state ทุกไฟล์เขียนแบบ atomic (ไฟล์ชั่วคราว + `os.replace`)

**ผลจริง:** duplicate = **0** ทุกชนิดข้อมูล แม้ระหว่างรันจะมีการหยุดกะทันหัน (ปิด VS Code) และเริ่มใหม่หลายครั้ง

## 3. การบรรลุ KPI ความเร็ว

**latency มาจาก:** ดีเลย์ของเว็บ + เวลารอรอบถัดไป (0–5 นาที) + คิว rate limit + เวลาโหลด

ระบบจด `first_seen_at` (เวลาที่เห็นลิงก์บนหน้ารายการครั้งแรก) จึงแยก latency ได้จากข้อมูลจริง:

| เว็บ | Freshness (≤ 15 นาที) | median / p95 | ฝั่งเว็บ (published → เห็นลิงก์) | ฝั่งเรา (เห็น → เก็บได้) |
|---|---|---|---|---|
| ข่าวสด | **97.8%** ✅ (n=186) | 3.9 / 6.9 นาที | median 3.8, p95 6.7 | median 0.1, p95 0.2 |
| Thai PBS | **7.0%** ❌ (n=57) | 33.4 / 105.7 นาที | median **33.2**, p95 **105.4** | median 0.3, p95 1.5 |

**ข่าวสดที่ไม่ผ่าน 2.2% บางส่วนไม่ใช่ความช้า** เช่น news_10484916 (lifestyle) ระบุ datePublished 06:00 แต่เลขข่าวอยู่ระหว่างข่าว 12:21 กับ 12:26 แปลว่าถูกสร้างราว 12:2x แล้วตั้งเวลาย้อนหลัง เราเก็บได้ 12:27 latency ที่คำนวณได้ 387 นาทีจึงสะท้อนเวลาที่เว็บระบุ ไม่ใช่ความช้าของระบบ

**ทำไม Thai PBS ไม่ผ่าน:** ฝั่งเราเก็บได้ภายใน 18 วินาทีหลังเห็นลิงก์ แต่ข่าวขึ้นหน้ารายการช้ากว่า `datePublished` ที่เว็บประกาศเฉลี่ย 33 นาที แปลว่า Thai PBS ใส่ `datePublished` เป็นเวลาสร้างข่าวในระบบก่อนเผยแพร่จริง **ดีเลย์อยู่ที่ต้นทาง เช็คถี่ขึ้นก็ลดไม่ได้**

**ทำไมเลือกทุก 5 นาที:** รอรอบเฉลี่ย 2.5 นาที สูงสุด 5 นาที เหลือเผื่อสำหรับส่วนอื่นภายในเป้า 15 นาที ผลจริงของข่าวสดคือ p95 6.9 นาที ส่วนภาระเพิ่มแค่หน้ารายการ (หน้าข่าวโหลดครั้งเดียวต่อข่าวไม่ว่าเช็คถี่แค่ไหน)

**YouTube:** median 34.9 นาที, p95 **67.9** ❌ (92.2% ≤ 60 นาที) รันทุก 1 ชั่วโมงตามโจทย์ latency สูงสุดตามทฤษฎีจึงประมาณ 60 นาที บวกดีเลย์ของ search index คลิปที่เข้า index ช้าถูกเก็บในรอบถัดไปด้วย OVERLAP ทำให้ latency เกิน 60 แลกกับการไม่หลุดหายไปเลย

**ถ้าต้องลดเหลือ 5 นาที:**
- ข่าวสด: ปัจจุบัน p95 6.9 นาทีอยู่แล้ว ลดรอบเหลือทุก 1–2 นาทีด้วย conditional GET (หน้าที่ไม่เปลี่ยนตอบ 304)
- Thai PBS: ต้องหาแหล่งที่สะท้อนเวลาเผยแพร่จริง เช่น news sitemap หรือ RSS ถ้ามี หรือใช้ `first_seen_at` เป็นเวลาอ้างอิงแทน และรายงานดีเลย์ของต้นทางแยกไว้
- YouTube: search ถี่ขึ้นเป็นไปไม่ได้ด้วยโควตาฟรี (100 หน่วยต่อ search) ต้องใช้ `playlistItems.list` ของช่องที่รู้จัก (1 หน่วย) หรือขอเพิ่มโควตา

## 4. การไม่สร้างภาระให้เว็บต้นทาง

**กลไก:**
- `DomainRateLimiter` เว้นระยะขั้นต่ำต่อโดเมน 60/12 = 5 วินาที requests และ Playwright ใช้ limiter ตัวเดียวกัน
- โหลดหน้าข่าวครั้งเดียวต่อข่าว + จำกัด 40 ข่าวต่อรอบ
- conditional GET (ETag) สำหรับหน้ารายการข่าวสด
- Playwright บล็อกรูป, ฟอนต์, CSS, เว็บภายนอก และ **path ที่ robots.txt ของ Thai PBS ห้าม** (`/api-v/`, `/thaipbs-api/` ฯลฯ) บล็อกได้ประมาณ 80 request ต่อรอบ
- Playwright ใช้ profile ถาวร ไฟล์ JS ถูก cache ข้ามรอบ
- หลังเปิดหน้าด้วย Playwright เรียก `limiter.charge()` เพื่อนับ request ที่เบราว์เซอร์ยิงเอง
- User-Agent ระบุตัวตนและช่องทางติดต่อ

**ผลจริงจาก `logs/**/requests.jsonl`:**

| โดเมน | requests | avg / max ต่อนาที | ≤ 20 |
|---|---:|---|---|
| www.khaosod.co.th | 613 | 2.0 / 12 | ✅ |
| www.thaipbs.or.th | 4,300 | 11.2 / **22** | ❌ |
| www.googleapis.com | 866 | 11.1 / 20 | ✅ |

**Thai PBS เกิน 20 ใน 7 จาก 385 นาที (1.8%) สูงสุด 22** กระจายตลอดวัน เกิดจากการเปิดหน้ารายการ 2 หน้าด้วย Playwright ซึ่งเบราว์เซอร์โหลด JS เองหลายไฟล์ในไม่กี่วินาที (`charge()` ช่วยเลื่อนคิวถัดไปแต่ย่อย burst ภายในการเปิดหน้าเดียวไม่ได้) และตัวเลขนี้**รวมไฟล์ที่หยิบจาก cache** (Chromium ส่ง event ให้ด้วย) ภาระจริงจึงต่ำกว่าที่วัดได้ ทางแก้: เปิด `/news` เฉพาะช่วงใกล้เที่ยงคืน (ลด burst ครึ่งหนึ่ง) และแยก request ที่มาจาก cache ออกจากการนับ

**เหตุการณ์จริงระหว่างทดสอบ:** ข่าวสดตอบ 403 เมื่อมีบอทรันซ้อน 2 ตัว ระบบแจ้งเตือน "ถูกบล็อก" ทันที จึงหยุดทั้งหมดและพัก 2 ชั่วโมงจนปลดบล็อก แล้วเพิ่มตัวกันรัน scheduler ซ้อน (ตรวจ heartbeat ก่อนเริ่ม)

## 5. โควตา YouTube API

โควตาฟรี 10,000 หน่วยต่อวัน (รีเซ็ตเที่ยงคืนเวลาแปซิฟิก) ตั้งงบ 9,000 เผื่อ 10%

| call | หน่วย | ต่อรอบ |
|---|---:|---:|
| search.list: 6 คำค้นรวมเป็น 2 query ด้วย `\|` (OR) | 100 | 200 + หน้าถัดไปเท่าที่งบพอ |
| videos.list: วิดีโอที่ติดตาม ≤ 500 ตัว ÷ 50 | 1 | ≤ 10 |
| commentThreads.list: top 20 | 1 | 20 |

- งบต่อรอบ = 9,000 / 24 = 375 หน่วย กันไว้ 40 สำหรับ videos/comments เหลือให้ search 335 → ได้หน้าแรกของทุก query (200) + หน้าถัดไปอีก 1 หน้า (300)
- ดึงหน้าแรกของทุก query ก่อนเสมอ แล้วค่อยใช้งบที่เหลือกับหน้าถัดไป (เคยมีบั๊กที่ query แรกกินงบจนหมด test จับได้)
- ถ้าค้น 5 คำแยกกันจะใช้ 12,000 หน่วยต่อวันจาก search อย่างเดียว เกินโควตา
- `QuotaGuard` นับหน่วยก่อนเรียกทุกครั้ง (request ที่ล้มเหลวก็กินโควตา) นับวันตามเวลาแปซิฟิก

**ผลจริง:** ใช้สูงสุด **5,981 หน่วยต่อวัน** ✅

**ข้อจำกัดที่พบ:** คำค้นกลุ่มข่าวมีคลิปใหม่เกิน 100 คลิปต่อชั่วโมง โควตาจ่าย pagination ได้แค่บางส่วน ถ้าต้องการครบต้องใช้คำค้นที่เฉพาะเจาะจงขึ้นหรือขอเพิ่มโควตา

## 6. การจัดการ error

| error | การจัดการ |
|---|---|
| timeout, connection error, 429, 5xx | retry 4 ครั้ง exponential backoff (1, 2, 4, 8 วินาที + jitter) ทุก retry ผ่าน rate limiter |
| `Retry-After` | รอตามที่เว็บบอก ถ้าเกิน 120 วินาทียอมแพ้รอบนี้ |
| 403 / 404 อื่น | ไม่ retry (ปัญหาคือสิทธิ์หรือสิ่งที่ขอ ไม่ใช่เวลา) |
| YouTube `commentsDisabled`, `videoNotFound` | นับว่าสำเร็จ บันทึกใน `comment_status.jsonl` แล้วไปต่อ |
| YouTube `rateLimitExceeded` | retry แบบ backoff |
| YouTube `quotaExceeded` | ไม่ retry หยุดส่วน A และแจ้งเตือน |
| หน้าข่าว parse ไม่ได้ | ลองใหม่รอบหน้า ครบ 3 ครั้งเลิกลอง (ข่าวสดเลิกลอง 3 ข่าวจาก 301) |
| Playwright timeout | แปลงเป็น `GiveUp` จัดการเหมือน error ของ requests |

**เมื่อแหล่งหนึ่งล้ม:** 1 เว็บ = 1 thread และแต่ละรอบมี `try/except` ของตัวเอง งานที่ crash ถูกจับที่ scheduler แจ้งเตือน แล้วรอบถัดไปทำงานตามปกติ

**ช่วงหยุด 9 นาทีระหว่างรันจริง:** 21:33 เผลอปิด VS Code ที่รันบอทอยู่ บอทหยุดโดย**ไม่มีการแจ้งเตือน** (process ถูกปิดก่อนส่ง ⏹️) จึงย้ายไปรันในหน้าต่าง PowerShell แยก ข้อสรุป: ระบบแจ้งเตือนจับ "บอทตายเงียบ" ไม่ได้เพราะตัวที่ต้องแจ้งคือตัวที่ตาย ต้องมี dead man's switch ภายนอก (ข้อ 11)

## 7. คุณภาพข้อมูล

**กฎ** (`collector/quality.py`):
- **missing:** field บังคับเป็น None, สตริงว่าง หรือ list ว่าง
- **malformed:** เวลาไม่ใช่ ISO 8601 + timezone, `published_at` หลัง `collected_at` เกิน 5 นาที, URL ไม่ใช่ http(s), เนื้อหาสั้นกว่า 50 ตัวอักษร, category ยังเป็น list ที่ encode, ตัวเลขติดลบ, duration แปลงไม่ได้, บรรทัด JSON เสีย
- **duplicate:** key ซ้ำข้ามไฟล์ (ข่าว = site + article_id)
- `like_count = None` ไม่นับเป็น missing (เจ้าของคลิปซ่อนไว้ ไม่ใช่เราเก็บไม่ได้)

**ข้อมูลที่ไม่ผ่าน:** ไม่ลบจากไฟล์ต้นฉบับ แต่ระบุไฟล์ + บรรทัด + ปัญหาใน `reports/quality_issues.jsonl` ข้อมูลที่ขาด title หรือ published_at จะไม่ถูกบันทึกตั้งแต่ตอนเก็บและลองใหม่รอบหน้า

**ผลจริง:**

| ชนิด | records | field ครบ | malformed | duplicate |
|---|---:|---:|---:|---:|
| ข่าว | 409 | 99.76% ✅ | 0% | 0 |
| YouTube search | 2,558 | 100% ✅ | 0% | 0 |
| YouTube videos (snapshot) | 12,353 | **98.86%** ❌ | 0% | 0 |
| YouTube comments | 4,371 | 99.98% ✅ | 0% | 0 |

**YouTube videos ไม่ผ่าน:** ขาด duration 141 snapshot จาก 39 วิดีโอ ตรวจผ่าน API (`liveStreamingDetails`) พบว่าเป็นวิดีโอ**ตั้งเวลาฉายล่วงหน้า** (`liveBroadcastContent = "upcoming"`) เช่น `4WWXpn2_YwA` อัปโหลด 25 พ.ย. กำหนดฉาย 4 ธ.ค. จึงยังไม่มีความยาว เป็นลักษณะข้อมูลต้นทาง แต่ละวิดีโอขาดแค่ 3–9 snapshot เพราะรายการติดตามจำกัด 500 วิดีโอล่าสุด (นิ่งที่ ~495 ตั้งแต่ชั่วโมงที่ 4 ส่วนที่ไม่ครบ 500 คือวิดีโอที่ถูกลบหรือตั้งเป็นส่วนตัวหลังเก็บ)

**ปัญหาที่เจอจากการเปิดดูข้อมูลจริง (test ผ่านหมดแต่ข้อมูลผิด):**
- ข่าวสดส่ง `articleSection` เป็นสตริงที่ข้างในเป็น JSON list → decode อีกชั้น
- หัวข้อข่าวสดมี ` - ข่าวสด` ติดท้าย → ตั้ง `title_suffix`
- เนื้อหา Thai PBS มีหัวข้อ "ข่าวที่เกี่ยวข้อง" ปนท้าย → `content_selector` + หยุดเมื่อเจอ "อ่านข่าว"
- ข่าวสดใช้เวลา UTC, Thai PBS ใช้ +07:00 → แปลงเป็นเวลาไทยทั้งหมด

## 8. การแจ้งเตือน

**เงื่อนไข** (ตัดสินจาก log ของแต่ละรอบ):

| เรื่อง | threshold |
|---|---|
| ถูกบล็อก | หน้ารายการได้ 401 / 403 / 429 |
| เว็บล่ม | หน้ารายการโหลดไม่ได้ (network, 5xx) |
| layout เปลี่ยน | หน้ารายการไม่มีลิงก์ที่ตรง pattern |
| parse ไม่ได้ | > 20% ของข่าวในรอบ |
| success rate ต่ำ | < 98% (เฉพาะรอบที่มี ≥ 10 request) |
| latency เกิน | p95 ข่าว > 30 นาที, YouTube > 60 นาที (≥ 3 ตัวอย่าง) |
| รอบนานเกิน / รอบถูกข้าม | duration ≥ ช่วงห่าง |
| โควตาหมด | error เรื่อง quota |
| งาน crash | exception ที่ระดับ scheduler |

**กันแจ้งถี่:** cooldown 30 นาทีต่อเรื่อง (key แยกต่อเว็บต่อปัญหา) ข้อความถัดไปบอกจำนวนครั้งที่ถูกระงับ และแจ้ง ✅ ครั้งเดียวเมื่อกลับมาปกติ

**ตัวอย่างข้อความ:**
```
🔴 [Mini Collector] news/khaosod ถูกบล็อก (HTTP 403) — หยุดชั่วคราวและตรวจ User-Agent/ความถี่
เวลา: 2026-11-25 21:39

✅ [Mini Collector] กลับมาปกติ: news/khaosod เข้าถึงได้แล้ว
เวลา: 2026-11-25 21:42
```

หลักฐานจากรัน 24 ชม.: [`docs/alert-24h-afternoon.png`](docs/alert-24h-afternoon.png), [`docs/alert-24h-evening.png`](docs/alert-24h-evening.png)
- Thai PBS หน้ารายการ Playwright timeout 14:53 → แจ้ง 🔴 → รอบถัดไปกลับมาเอง ✅ 14:59
- ปิดระบบ 22:06 → แจ้ง ⏹️

ที่ต้องปรับจากการรันจริง
YouTube freshness p95 อยู่ที่ 61–62 นาที ใกล้เป้า 60 มาก ทำให้แจ้ง 🔴/✅ สลับกันทุกชั่วโมง (flapping) ควรใช้ hysteresis คือแจ้งเมื่อ > 60 แต่ถือว่าหายเมื่อ < 55 หรือต้องเกินติดกัน 2 รอบ

ขั้นต่ำ 3 ตัวอย่างของกฎ latency ก็น้อยเกินไป: 12:27 รอบที่มี 3 ข่าวแต่ 1 ข่าวตั้งเวลาย้อนหลัง ทำให้ p95 = 387 นาทีและแจ้งเตือนทั้งที่ระบบปกติ ควรคำนวณ p95 จากหน้าต่าง 1 ชั่วโมงแทนรอบเดียว

**ที่ต้องปรับจากการรันจริง:** กฎ parse ยังไม่มีจำนวนขั้นต่ำ รอบที่มีข่าวใหม่ข่าวเดียวแล้วข่าวนั้นเสีย = 100% จึงแจ้งเตือนทั้งที่ไม่ใช่ปัญหา ควรต้องมี ≥ 3 ข่าวเหมือนกฎ latency และควรข้ามการเช็ค latency ในรอบแรกหลังเริ่มระบบ

## 9. เมื่อเว็บหรือ API เปลี่ยน

**ออกแบบให้ทน:**
- parse จาก JSON-LD (schema.org) ก่อน → meta tag → HTML เพราะ JSON-LD เว็บต้องรักษาไว้เพื่อ SEO
- selector เลือกแบบมาตรฐานก่อนชื่อ class: `[itemprop="articleBody"]` (ข่าวสด), `#item-description` (Thai PBS) ไม่ใช้ class ที่มีรหัสสุ่มของ Svelte
- ค่าเฉพาะเว็บอยู่ใน `sites.json` ไม่ใช่ในโค้ด

**รู้ตัวได้อย่างไร:**
- หน้ารายการไม่มีลิงก์ → แจ้ง layout เปลี่ยนทันที
- parse ไม่ได้ > 20% → แจ้ง
- สคริปต์คุณภาพข้อมูลจับ field ที่หายหรือผิดรูปแบบ (เช่น category ที่ encode ซ้อน)
- YouTube: field หายจะเห็นใน quality report, error ใหม่จะเห็นใน log ต่อรอบ

**เวลาแก้:** แก้ pattern หรือ selector ใน `sites.json` 5–15 นาที ถ้าต้องแก้ parser 1–2 ชั่วโมง พร้อมเพิ่ม test จาก HTML ใหม่ (ทุกปัญหาที่เจอในโปรเจกต์นี้มี regression test แล้ว)

## 10. การขยายระบบ (50 เว็บ / 500 keyword)

- **แยก scheduler กับ worker ด้วยคิว** (Redis + RQ/Celery หรือ SQS): scheduler ส่งงาน "poll หน้ารายการของเว็บ X" ตามความถี่ของแต่ละเว็บ worker ส่งงาน "โหลดหน้าข่าว" ต่อเข้าคิว
- **rate limit ต่อโดเมนต้องย้ายไปที่ส่วนกลาง** (token bucket ใน Redis) เพราะตอนนี้อยู่ใน process เดียว worker หลายตัวจะยิงเว็บเดียวกันเกินโดยไม่รู้ตัว
- **concurrency = 1 ต่อโดเมน** แต่ขนานกันข้ามโดเมน (ตอนนี้ทำแล้วระดับ thread)
- **ความถี่แบบ adaptive:** เว็บที่ออกข่าวถี่ poll ถี่ เว็บนิ่ง poll ห่าง (ข้อมูลจริง: ข่าวสดมีข่าวใหม่บ่อยกว่า Thai PBS มาก)
- **Playwright เป็น pool แยก** เพราะใช้ RAM มาก และใช้เฉพาะเว็บที่จำเป็น
- **50 เว็บ = 50 ก้อนใน sites.json** หรือย้ายไปฐานข้อมูลค่าตั้ง
- **500 keyword:** โควตาไม่พอแน่นอน ต้องจัดลำดับความสำคัญ (keyword หลักทุกชั่วโมง ที่เหลือหมุนเวียน), รวมคำด้วย `|`, ใช้ `playlistItems.list` (1 หน่วย) กับช่องที่รู้จัก, ขอเพิ่มโควตา
- **storage:** ย้ายไป object storage (S3) แบ่ง partition ตามวัน state ไป Redis

## 11. ข้อจำกัดและสิ่งที่จะทำต่อ

- **Docker กับข่าวสด:** รันใน container แล้วข่าวสดตอบ 403 ขณะที่รันบน Windows เครื่องเดียวกัน IP เดียวกันได้ 200 (ห่างกัน 3 นาที) ข้อสันนิษฐานคือเว็บตรวจลายนิ้วมือของการเชื่อมต่อ (TLS/HTTP fingerprint ของ Python บน Linux ต่างจากบน Windows) ซึ่งยังไม่ได้ยืนยัน การรัน 24 ชม. จริงจึงใช้ `python run.py` บน Windows ส่วน Docker ทดสอบแล้วว่า build และรันส่วน YouTube กับ Thai PBS ได้ปกติ ขั้นต่อไปคือเทียบ request ทั้งสองฝั่ง และจะไม่ปลอมลายนิ้วมือเพื่อข้ามการบล็อก
- **Freshness Thai PBS:** ดีเลย์อยู่ที่ต้นทาง (ข้อ 3) ต้องหาแหล่งเวลาเผยแพร่จริง
- **ภาระ Thai PBS เกิน 20 req/นาทีใน 1.8% ของนาที** และการนับรวม cache (ข้อ 4)
- **Coverage วัดเทียบกับลิงก์ที่เคยเห็นบนหน้ารายการ** ข่าวที่ขึ้นแล้วหลุดหน้าไปก่อนบอทเช็คจะไม่ถูกนับ ควรเทียบกับ sitemap เพิ่ม
- **ไม่มี dead man's switch:** บอทตายเงียบไม่มีใครรู้ ควรให้บริการภายนอกเช็ค heartbeat
- **Docker `restart: unless-stopped` ทำให้ crash ตอนเริ่มกลายเป็นวนยิงเว็บ** (เกิดจริงจากบั๊กพิมพ์ผิด 1 จุด ทุก restart เริ่มรอบเก็บข่าวก่อนพัง) ควรเขียน heartbeat ก่อนเริ่มงาน และใช้ `restart: on-failure:5`
- **YouTube:** เก็บ `liveBroadcastContent` และ `scheduledStartTime` เพิ่ม เพื่อไม่นับ duration ของวิดีโอ upcoming เป็น missing และติดตามวิดีโอได้แค่ 5–9 ชั่วโมงเพราะเพดาน 500
- **เวลาที่ไม่มี timezone ถือเป็นเวลาไทย** (ทั้ง 2 เว็บที่เลือกมี timezone จึงไม่กระทบผล)
- **ถูกบล็อกแล้วบอทยังยิงต่อ** (cooldown แค่กันแจ้งซ้ำ) ควรให้บอทหยุดยิงเว็บนั้นเองชั่วคราวเมื่อได้ 403/429
