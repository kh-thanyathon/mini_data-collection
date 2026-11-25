import json

from collector.config import NewsConfig, NewsSite
from collector.news import (NewsCollector, article_id, extract_links, normalize_url,
                            parse_article, parse_published)
from collector.timeutil import iso, now

SITE = NewsSite(name="t", list_urls=["https://n.com/home"], article_pattern=r"/news_\d+$",
                id_pattern=r"news_(\d+)$", category_segment=0)


# ---------- ส่วน pure ----------
def test_normalize_url():
    assert normalize_url("/a/news_1/?utm=fb#top", "https://N.com/home") == "https://n.com/a/news_1"


def test_extract_links_filters_and_dedups():
    html = ('<a href="/update-news/news_1">a</a><a href="/update-news/news_1?x=1">b</a>'
            '<a href="/about">c</a><a href="https://n.com/sports/news_2">d</a>')
    assert extract_links(html, "https://n.com/home", SITE.article_pattern) == [
        "https://n.com/update-news/news_1", "https://n.com/sports/news_2"]


def test_same_article_different_paths_same_id():
    assert article_id("https://n.com/update-news/news_99", SITE.id_pattern) == \
           article_id("https://n.com/sports/news_99", SITE.id_pattern) == "99"


def test_parse_published_formats():
    assert iso(parse_published("2026-11-24T14:37:33+00:00")) == "2026-11-24T21:37:33+07:00"
    assert iso(parse_published("1795531300000")) == "2026-11-24T21:41:40+07:00"   # epoch ms
    assert parse_published("ไม่ใช่เวลา") is None


ARTICLE_HTML = """<html><head>
<link rel="canonical" href="https://n.com/sports/news_99">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
  {"@type":"WebPage","datePublished":"2026-11-24T14:37:33+00:00"},
  {"@type":"NewsArticle","headline":"หัวข้อข่าว","articleSection":"กีฬา",
   "image":{"url":"https://n.com/a.jpg"}}]}</script>
<script type="application/ld+json">{ เสีย </script>
</head><body><article>
<p>ย่อหน้าแรกของข่าวที่ยาวพอจะนับเป็นเนื้อหา</p><p>สั้น</p>
<p>ย่อหน้าที่สองของข่าวที่ยาวพอจะนับเป็นเนื้อหา</p></article></body></html>"""


def test_parse_article_jsonld_with_graph_and_broken_block():
    r = parse_article(ARTICLE_HTML, "https://n.com/update-news/news_99", SITE)
    assert r["url"] == "https://n.com/sports/news_99"           # ใช้ canonical
    assert r["article_id"] == "99"
    assert r["title"] == "หัวข้อข่าว"
    assert r["published_at"] == "2026-11-24T21:37:33+07:00"    # หาเจอแม้อยู่ใน WebPage
    assert r["category"] == "กีฬา"
    assert r["images"] == ["https://n.com/a.jpg"]
    assert "ย่อหน้าแรก" in r["content"] and "สั้น" not in r["content"]


def test_parse_article_meta_fallback():
    html = """<html><head><meta property="og:title" content="จาก meta">
    <meta itemprop="datePublished" content="1795531300000"></head>
    <body><p>เนื้อหาที่ยาวพอจะนับเป็นย่อหน้าได้จริง ๆ</p></body></html>"""
    r = parse_article(html, "https://n.com/politics/news_5", SITE)
    assert r["title"] == "จาก meta"
    assert r["published_at"] == "2026-11-24T21:41:40+07:00"
    assert r["category"] == "politics"                          # จาก URL segment


# ---------- ส่วนที่ต่อเน็ต (จำลองเว็บ) ----------
class Resp:
    def __init__(self, text="", status=200, headers=None):
        self.text, self.status_code, self.headers = text, status, headers or {}


def article(n, published):
    return f"""<html><head><script type="application/ld+json">
    {{"@type":"NewsArticle","headline":"ข่าว {n}","datePublished":"{published}",
      "articleSection":"ทั่วไป","articleBody":"เนื้อหาข่าวที่ยาวพอสมควร {n}"}}
    </script></head></html>"""


class FakeSite:
    def __init__(self):
        self.ids = [1, 2]
        self.calls = []
        self.etag = "v1"

    def get(self, url, headers=None):
        self.calls.append(url)
        if url.endswith("/home"):
            if headers and headers.get("If-None-Match") == self.etag:
                return Resp(status=304)
            links = "".join(f'<a href="/update-news/news_{i}">x</a>' for i in self.ids)
            return Resp(links, headers={"ETag": self.etag})
        n = url.rsplit("_", 1)[1]
        return Resp(article(n, iso(now())))


def make(tmp_path):
    fake = FakeSite()
    nc = NewsCollector(NewsConfig(), [SITE], fake, state_dir=tmp_path / "s", data_dir=tmp_path / "d")
    return nc, fake


def test_first_round_is_backfill_then_only_new_articles(tmp_path):
    nc, fake = make(tmp_path)
    r1 = nc.run_site(SITE)
    assert r1["saved"] == 2 and r1["backfill"] == 2 and r1["latency_min"] == []

    fake.ids = [3, 1, 2]
    fake.etag = "v2"
    fake.calls.clear()
    r2 = nc.run_site(SITE)
    assert r2["new_links"] == 1 and r2["saved"] == 1 and r2["backfill"] == 0
    assert fake.calls == ["https://n.com/home", "https://n.com/update-news/news_3"]  # ไม่โหลดข่าวเดิม
    assert len(r2["latency_min"]) == 1


def test_not_modified_listing_costs_one_request(tmp_path):
    nc, fake = make(tmp_path)
    nc.run_site(SITE)
    fake.calls.clear()
    r = nc.run_site(SITE)                       # etag เดิม -> 304
    assert r["listing_not_modified"] == 1
    assert fake.calls == ["https://n.com/home"]


def test_records_have_required_fields(tmp_path):
    nc, _ = make(tmp_path)
    nc.run_site(SITE)
    f = next((tmp_path / "d" / "news" / "t").glob("*.jsonl"))
    rec = json.loads(f.read_text(encoding="utf-8").splitlines()[0])
    for k in ("article_id", "url", "title", "published_at", "collected_at", "content", "category"):
        assert rec[k], k


def test_max_articles_per_round_caps_work(tmp_path):
    fake = FakeSite()
    fake.ids = list(range(1, 11))
    nc = NewsCollector(NewsConfig(max_articles_per_round=4), [SITE], fake,
                       state_dir=tmp_path / "s", data_dir=tmp_path / "d")
    r1 = nc.run_site(SITE)
    assert r1["new_links"] == 10 and r1["saved"] == 4      # ที่เหลือรอรอบหน้า
    fake.etag = "v2"
    assert nc.run_site(SITE)["saved"] == 4

def test_category_double_encoded_and_title_suffix():
    site = NewsSite(name="t", list_urls=[], article_pattern=r"/news_\d+$",
                    id_pattern=r"news_(\d+)$", title_suffix=" - ข่าวสด")
    html = """<html><head><meta property="og:title" content="น้ำมันลดราคา - ข่าวสด">
    <script type="application/ld+json">{"@type":"NewsArticle",
      "datePublished":"2026-11-24T19:31:07+07:00",
      "articleSection":"[\\"ข่าวด่วน\\", \\"ข่าวอัปเดต\\"]"}</script></head></html>"""
    r = parse_article(html, "https://n.com/a/news_1", site)
    assert r["category"] == "ข่าวด่วน"
    assert r["title"] == "น้ำมันลดราคา"

def test_content_selector_and_stop_marker():
    site = NewsSite(name="t", list_urls=[], article_pattern=r"/news/content/\d+$",
                    id_pattern=r"/news/content/(\d+)$",
                    content_selector="#item-description", content_stop=["อ่านข่าว"])
    html = """<html><head><meta property="og:title" content="t">
    <meta property="article:published_time" content="2026-11-25T10:55:38+07:00"></head><body>
    <div id="item-description">
      <p>ย่อหน้าเนื้อข่าวจริงที่ยาวพอจะนับเป็นเนื้อหา</p>
      <p>อ่านข่าว</p>
      <p>หลากสีสัน "ลอยกระทง 2569" ทั่วไทย ปล่อยโคมยี่เป็ง</p>
    </div>
    <p>ข่าวแนะนำอื่นนอกกล่องเนื้อหาที่ยาวพอสมควร</p></body></html>"""
    r = parse_article(html, "https://n.com/news/content/1", site)
    assert r["content"] == "ย่อหน้าเนื้อข่าวจริงที่ยาวพอจะนับเป็นเนื้อหา"

def test_each_round_writes_its_own_log(tmp_path):
    nc, fake = make(tmp_path)
    nc.run_site(SITE)
    fake.ids, fake.etag = [3, 1, 2], "v2"
    nc.run_site(SITE)
    logs = sorted((tmp_path / "logs" / "news" / "t").glob("*.json"))
    assert len(logs) == 2
    second = json.loads(logs[1].read_text(encoding="utf-8"))
    assert second["items"]["saved"] == 1
    assert second["freshness"]["n"] == 1

def test_first_seen_at_recorded_even_if_collected_later(tmp_path):
    fake = FakeSite()
    fake.ids = [1, 2, 3]
    nc = NewsCollector(NewsConfig(max_articles_per_round=1), [SITE], fake,
                       state_dir=tmp_path / "s", data_dir=tmp_path / "d")
    nc.run_site(SITE)
    fake.etag = "v2"
    nc.run_site(SITE)
    rows = [json.loads(x) for x in next((tmp_path / "d" / "news" / "t").glob("*.jsonl"))
            .read_text(encoding="utf-8").splitlines()]
    first, second = rows
    assert first["first_seen_at"] <= first["collected_at"]
    # ข่าวที่ 2 ถูกเห็นตั้งแต่รอบแรก แต่เก็บได้รอบสอง -> first_seen_at คือเวลาของรอบแรก
    assert second["first_seen_at"] <= first["collected_at"] <= second["collected_at"]

def test_alerter_is_called_with_round_log(tmp_path):
    calls = []

    class SpyAlerter:
        def check_round(self, rlog):
            calls.append(rlog)

    fake = FakeSite()
    nc = NewsCollector(NewsConfig(), [SITE], fake, state_dir=tmp_path / "s",
                       data_dir=tmp_path / "d", alerter=SpyAlerter())
    nc.run_site(SITE)
    assert len(calls) == 1 and calls[0]["source"] == "news/t"