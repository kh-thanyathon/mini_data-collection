import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from collector.config import NewsConfig, NewsSite
from collector.http_client import DomainRateLimiter, RequestStats
from collector.news import NewsCollector, PlaywrightFetcher, block_reason, is_allowed, same_site

BLOCKED = ["/api-v/", "/news/content/content/"]


# ---------- ส่วน pure ----------
def test_same_site():
    assert same_site("static.thaipbs.or.th", "www.thaipbs.or.th")
    assert not same_site("ads.google.com", "www.thaipbs.or.th")


def test_is_allowed_follows_robots_prefix_rule():
    assert is_allowed("https://x.com/news/content/573249", BLOCKED)
    assert not is_allowed("https://x.com/news/content/content/1", BLOCKED)
    assert not is_allowed("https://x.com/api-v/latest", BLOCKED)


def test_block_reason():
    d = "www.thaipbs.or.th"
    assert block_reason("https://www.thaipbs.or.th/a.jpg", "image", d, BLOCKED) == "resource_type"
    assert block_reason("https://ads.example.com/x.js", "script", d, BLOCKED) == "third_party"
    assert block_reason("https://www.thaipbs.or.th/api-v/x", "fetch", d, BLOCKED) == "robots"
    assert block_reason("https://www.thaipbs.or.th/app.js", "script", d, BLOCKED) is None


# ---------- จำลองเว็บ render ด้วย fetcher ปลอม ----------
class FakeFetcher:
    def __init__(self):
        self.calls = []

    def fetch(self, url, site):
        self.calls.append(url)
        return '<a href="/news/content/1">a</a><a href="/news/content/2">b</a>'

    def close(self):
        pass


class Resp:
    def __init__(self, text):
        self.text, self.status_code, self.headers = text, 200, {}


class ArticleClient:
    def get(self, url, headers=None):
        n = url.rsplit("/", 1)[1]
        return Resp(f"""<script type="application/ld+json">{{"@type":"NewsArticle",
        "headline":"ข่าว {n}","datePublished":"2026-11-24T20:27:45+07:00",
        "articleSection":"สังคม","articleBody":"เนื้อหาข่าวที่ยาวพอ {n}"}}</script>""")


def test_render_site_uses_browser_for_listing_and_requests_for_articles(tmp_path):
    site = NewsSite(name="pbs", list_urls=["https://n.com/news/today", "https://n.com/news"],
                    article_pattern=r"/news/content/\d+$", id_pattern=r"/news/content/(\d+)$",
                    render=True, blocked_paths=BLOCKED)
    fetcher = FakeFetcher()
    nc = NewsCollector(NewsConfig(), [site], ArticleClient(), fetcher=fetcher,
                       state_dir=tmp_path / "s", data_dir=tmp_path / "d")
    r = nc.run_site(site)
    assert fetcher.calls == site.list_urls          # หน้ารายการทั้ง 2 หน้าผ่านเบราว์เซอร์
    assert r["listing_links"] == 2                  # ลิงก์ซ้ำระหว่าง 2 หน้าถูกรวมด้วยเลขข่าว
    assert r["saved"] == 2


# ---------- Playwright จริง กับเว็บจำลองบนเครื่อง ----------
LIST_HTML = """<html><body><div id="list"></div>
<img src="/photo.jpg">
<script>
  // ลิงก์ข่าวถูกสร้างด้วย JS -> requests มองไม่เห็น ต้องใช้เบราว์เซอร์
  document.getElementById('list').innerHTML =
    '<a href="/news/content/101">ข่าว 101</a><a href="/news/content/102">ข่าว 102</a>';
  fetch('/api-v/latest');   // path ที่ robots.txt ห้าม -> ต้องถูกบล็อก
</script></body></html>"""


@pytest.fixture
def local_site(tmp_path):
    (tmp_path / "news").mkdir()
    (tmp_path / "news" / "today").write_text(LIST_HTML, encoding="utf-8")
    (tmp_path / "photo.jpg").write_bytes(b"x")
    hits = []

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            hits.append(self.path)

        def guess_type(self, path):
            return "text/html; charset=utf-8" if path.endswith("today") else super().guess_type(path)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(tmp_path)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", hits
    srv.shutdown()


def test_playwright_renders_js_and_blocks_robots_and_images(local_site, tmp_path):
    base, hits = local_site
    site = NewsSite(name="local", list_urls=[f"{base}/news/today"],
                    article_pattern=r"/news/content/\d+$", id_pattern=r"/news/content/(\d+)$",
                    render=True, blocked_paths=BLOCKED)
    stats = RequestStats()
    f = PlaywrightFetcher(DomainRateLimiter(600), stats, "test", tmp_path / "profile")
    try:
        html = f.fetch(f"{base}/news/today", site)
    finally:
        f.close()
    assert "/news/content/101" in html                       # เห็นลิงก์ที่ JS สร้าง
    assert f.blocked["robots"] == 1 and f.blocked["resource_type"] == 1
    assert "/news/today" in hits
    assert not any(h.startswith(("/api-v/", "/photo")) for h in hits)        # ไม่ถึงเซิร์ฟเวอร์เลย
    assert stats.summary()["total"] == 1                      # นับเฉพาะ request ที่ออกไปจริง

def test_limiter_charge_pushes_next_slot():
    t = [0.0]
    slept = []
    lim = DomainRateLimiter(12, clock=lambda: t[0], sleep=slept.append)   # ห่างกัน 5 วิ
    lim.wait("a.com")                 # หน้าหลัก ยิงทันที
    lim.charge("a.com", 3)            # เบราว์เซอร์โหลด JS เองอีก 3 ไฟล์
    lim.wait("a.com")                 # request ถัดไปต้องรอ 5 + 3x5 = 20 วิ
    assert slept == [20.0]