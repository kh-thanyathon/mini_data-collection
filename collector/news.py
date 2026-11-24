import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from .config import DATA_DIR, STATE_DIR, NewsConfig, NewsSite, load_sites
from .http_client import DomainRateLimiter, GiveUp, HttpClient, RequestStats
from .storage import SeenStore, append_jsonl, atomic_write_json, load_json, round_id
from .timeutil import LOCAL_TZ, iso, latency_minutes, now, parse_iso

MAX_FAILS = 3          # หน้าข่าวที่ parse ไม่ได้ 3 รอบติด -> เลิกลอง
ARTICLE_TYPES = {"NewsArticle", "Article", "ReportageNewsArticle"}


# ======================================================================
# 1) ส่วนที่ไม่ต้องต่อเน็ต (pure functions) — test ง่าย
# ======================================================================
def normalize_url(href: str, base: str) -> str:
    """ทำให้ URL เดียวกันหน้าตาเหมือนกันเสมอ: เติมโดเมน, ตัด ?query #fragment และ / ท้าย"""
    u = urlparse(urljoin(base, href))
    return urlunparse((u.scheme, u.netloc.lower(), u.path.rstrip("/"), "", "", ""))


def extract_links(html: str, base: str, pattern: str) -> list[str]:
    """ดึงลิงก์ข่าวจากหน้ารายการ เรียงตามลำดับที่เจอ ไม่ซ้ำ"""
    rx = re.compile(pattern)
    out, seen = [], set()
    for a in BeautifulSoup(html, "lxml").select("a[href]"):
        url = normalize_url(a["href"], base)
        if rx.search(url) and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def article_id(url: str, id_pattern: str) -> str | None:
    """เลขข่าวจาก URL — ใช้เป็น key กันซ้ำ (ข่าวเดียวกันอาจมีหลาย path)"""
    m = re.search(id_pattern, url)
    return m.group(1) if m else None


def parse_published(value) -> datetime | None:
    """รับได้ทั้ง ISO 8601 และ Unix epoch (วินาทีหรือมิลลิวินาที) คืนเป็นเวลาไทย"""
    if value is None:
        return None
    s = str(value).strip()
    if s.isdigit():
        n = int(s)
        if n > 10**11:          # ใหญ่ขนาดนี้ต้องเป็นมิลลิวินาที
            n = n / 1000
        return datetime.fromtimestamp(n, tz=LOCAL_TZ)
    dt = parse_iso(s)
    return dt.astimezone(LOCAL_TZ) if dt else None   # เก็บเป็นเวลาไทยทั้งหมด อ่านง่ายและเทียบกันได้


def _jsonld_nodes(soup: BeautifulSoup) -> list[dict]:
    """รวมทุก object ใน <script type="application/ld+json"> (รวม @graph) เป็น list เดียว"""
    nodes = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue                      # JSON-LD เสีย 1 ก้อน ไม่ทำให้ทั้งหน้าพัง
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop(0)
            if isinstance(d, list):
                stack.extend(d)
            elif isinstance(d, dict):
                nodes.append(d)
                if isinstance(d.get("@graph"), list):
                    stack.extend(d["@graph"])
    return nodes


def _is_article(node: dict) -> bool:
    t = node.get("@type")
    types = t if isinstance(t, list) else [t]
    return any(x in ARTICLE_TYPES for x in types)


def _ld_get(nodes: list[dict], key: str):
    """หา key ใน node ที่เป็น Article ก่อน ถ้าไม่มีค่อยหาใน node อื่น"""
    ordered = [n for n in nodes if _is_article(n)] + [n for n in nodes if not _is_article(n)]
    for n in ordered:
        if n.get(key):
            return n[key]
    return None


def _meta(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        tag = (soup.find("meta", attrs={"property": name})
               or soup.find("meta", attrs={"name": name})
               or soup.find("meta", attrs={"itemprop": name}))
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def _images(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [value["url"]] if value.get("url") else []
    if isinstance(value, list):
        return [u for v in value for u in _images(v)]
    return []


def _first_text(value) -> str | None:
    """articleSection อาจมาเป็น str, list หรือ str ที่ข้างในเป็น JSON list อีกชั้น"""
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            value = json.loads(value)          # '["ข่าวด่วน", ...]' -> ["ข่าวด่วน", ...]
        except json.JSONDecodeError:
            pass
    if isinstance(value, list):
        value = next((v for v in value if isinstance(v, str) and v.strip()), None)
    return value.strip() if isinstance(value, str) and value.strip() else None

def parse_article(html: str, url: str, site: NewsSite) -> dict:
    """ลำดับความน่าเชื่อถือ: JSON-LD -> meta tag -> HTML"""
    soup = BeautifulSoup(html, "lxml")
    nodes = _jsonld_nodes(soup)

    # URL จริงของข่าว (canonical) — ข่าวสดเปิดผ่าน /update-news/ แต่ URL จริงคือ /sports/
    canon = soup.find("link", rel="canonical")
    canonical = normalize_url(canon["href"], url) if canon and canon.get("href") else url

    title = (_ld_get(nodes, "headline") or _meta(soup, "og:title")
             or (soup.title.get_text(strip=True) if soup.title else None))
    if title and site.title_suffix and title.strip().endswith(site.title_suffix):
        title = title.strip()[: -len(site.title_suffix)]      # ตัด " - ข่าวสด" ท้ายหัวข้อ

    published = parse_published(_ld_get(nodes, "datePublished")
                                or _meta(soup, "article:published_time", "datePublished"))

    category = _first_text(_ld_get(nodes, "articleSection")) or _first_text(_meta(soup, "article:section"))
    if not category and site.category_segment is not None:
        parts = urlparse(canonical).path.strip("/").split("/")
        if len(parts) > site.category_segment + 1:       # ต้องมีอย่างน้อยหมวด + ตัวข่าว
            category = parts[site.category_segment]

    images = _images(_ld_get(nodes, "image")) or _images(_meta(soup, "og:image"))

    content = _ld_get(nodes, "articleBody")
    if not content:
        box = (soup.select_one(site.content_selector) if site.content_selector else None) \
              or soup.find("article") or soup
        paras = [p.get_text(" ", strip=True) for p in box.find_all("p")]
        content = "\n".join(p for p in paras if len(p) > 20)

    return {
        "site": site.name,
        "article_id": article_id(canonical, site.id_pattern) or article_id(url, site.id_pattern),
        "url": canonical,
        "source_url": url,
        "title": title.strip() if title else None,
        "published_at": iso(published) if published else None,
        "content": content.strip() if content else None,
        "images": list(dict.fromkeys(images)),           # ตัดรูปซ้ำ คงลำดับเดิม
        "category": category,
    }


# ======================================================================
# 2) ส่วนที่ต่อเน็ต
# ======================================================================
class NewsCollector:
    def __init__(self, cfg: NewsConfig | None = None, sites: list[NewsSite] | None = None,
                 client: HttpClient | None = None,
                 state_dir: Path = STATE_DIR, data_dir: Path = DATA_DIR):
        self.cfg = cfg or NewsConfig()
        self.sites = sites if sites is not None else load_sites()
        self.client = client or HttpClient(DomainRateLimiter(self.cfg.max_req_per_min),
                                           RequestStats(), self.cfg.user_agent)
        self.state_dir, self.data_dir = state_dir, data_dir

    # ---------- หน้ารายการ ----------
    def fetch_listing(self, site: NewsSite, url: str, summary: dict) -> list[str]:
        """โหลดหน้ารายการแบบ conditional GET: ถ้าหน้าไม่เปลี่ยน เว็บตอบ 304 ไม่ส่งเนื้อหา"""
        cache_path = self.state_dir / f"news_http_cache_{site.name}.json"
        cache = load_json(cache_path, {})
        entry = cache.get(url, {})
        headers = {}
        if entry.get("etag"):
            headers["If-None-Match"] = entry["etag"]
        if entry.get("last_modified"):
            headers["If-Modified-Since"] = entry["last_modified"]

        resp = self.client.get(url, headers=headers)
        if resp.status_code == 304:
            summary["listing_not_modified"] += 1
            return entry.get("links", [])                 # ใช้ลิงก์ชุดเดิม

        links = extract_links(resp.text, url, site.article_pattern)
        cache[url] = {"etag": resp.headers.get("ETag"),
                      "last_modified": resp.headers.get("Last-Modified"),
                      "links": links}
        atomic_write_json(cache_path, cache)
        return links

    # ---------- 1 รอบของ 1 เว็บ ----------
    def run_site(self, site: NewsSite) -> dict:
        started = now()
        rid = round_id(started)
        summary = {"site": site.name, "round_id": rid, "listing_links": 0,
                   "listing_not_modified": 0, "new_links": 0, "saved": 0,
                   "backfill": 0, "failed": 0, "errors": [], "latency_min": []}

        seen = SeenStore(self.state_dir / f"news_seen_{site.name}.json", ttl_days=14)
        fails_path = self.state_dir / f"news_fails_{site.name}.json"
        fails: dict[str, int] = load_json(fails_path, {})
        baseline_path = self.state_dir / f"news_baseline_{site.name}.json"

        try:
            # 1) รวมลิงก์จากทุกหน้ารายการ กันซ้ำด้วยเลขข่าว
            by_id: dict[str, str] = {}
            for list_url in site.list_urls:
                for link in self.fetch_listing(site, list_url, summary):
                    aid = article_id(link, site.id_pattern)
                    if aid and aid not in by_id:
                        by_id[aid] = link
            summary["listing_links"] = len(by_id)

            # 2) รอบแรกสุด: จำว่าข่าวที่ค้างอยู่บนหน้าตอนนั้นเป็นข่าวเก่า (backfill)
            if baseline_path.exists():
                baseline = set(load_json(baseline_path, []))
            else:
                baseline = set(by_id)
                atomic_write_json(baseline_path, sorted(baseline))

            # 3) โหลดเฉพาะข่าวที่ยังไม่เคยเก็บ
            new = [(aid, link) for aid, link in by_id.items() if aid not in seen]
            summary["new_links"] = len(new)
            # จำกัดต่อรอบ ให้รอบจบก่อนรอบถัดไป (40 ข่าว x 5 วิ = 200 วิ < 300 วิ) ที่เหลือไปรอบหน้า
            new = new[: self.cfg.max_articles_per_round]

            records = []
            for aid, link in new:
                try:
                    rec = parse_article(self.client.get(link).text, link, site)
                except GiveUp as e:
                    summary["errors"].append(str(e)[:200])
                    self._fail(aid, fails, seen, summary)
                    continue
                if not rec["title"] or not rec["published_at"]:
                    self._fail(aid, fails, seen, summary)   # ข้อมูลสำคัญขาด -> ลองใหม่รอบหน้า
                    continue
                rec["article_id"] = aid                     # ใช้เลขจากหน้ารายการเป็นหลัก
                rec["collected_at"] = iso(now())
                rec["round_id"] = rid
                rec["backfill"] = aid in baseline
                records.append(rec)

            # 4) เขียนไฟล์ก่อน แล้วค่อยจำ
            out_path = self.data_dir / "news" / site.name / f"{started:%Y-%m-%d}.jsonl"
            summary["saved"] = append_jsonl(out_path, records)
            for r in records:
                seen.add(r["article_id"])
                fails.pop(r["article_id"], None)
                if r["backfill"]:
                    summary["backfill"] += 1
                else:
                    lat = latency_minutes(r["published_at"], r["collected_at"])
                    if lat is not None:
                        summary["latency_min"].append(round(lat, 1))
        except GiveUp as e:
            summary["errors"].append(f"listing: {e}"[:200])
        finally:
            seen.save()
            atomic_write_json(fails_path, fails)

        summary["duration_s"] = round((now() - started).total_seconds(), 1)
        return summary

    def _fail(self, aid: str, fails: dict, seen: SeenStore, summary: dict) -> None:
        summary["failed"] += 1
        fails[aid] = fails.get(aid, 0) + 1
        if fails[aid] >= MAX_FAILS:       # เลิกลอง ไม่ให้โหลดหน้าเสียซ้ำทุก 5 นาทีไปตลอด
            seen.add(aid)
            fails.pop(aid)

    def run_round(self) -> list[dict]:
        """ทุกเว็บในรอบเดียว — เว็บหนึ่งพังไม่กระทบเว็บอื่น"""
        results = []
        for site in self.sites:
            if site.render:
                continue                      # เว็บที่ต้องใช้ Playwright รอ บทที่ 7
            try:
                results.append(self.run_site(site))
            except Exception as e:            # noqa: BLE001 — กันบอทตายทั้งตัวเพราะเว็บเดียว
                results.append({"site": site.name, "errors": [f"{type(e).__name__}: {e}"],
                                "latency_min": []})
        return results


if __name__ == "__main__":
    for s in NewsCollector().run_round():
        lat = sorted(s.pop("latency_min"))
        print(s)
        if lat:
            print(f"  latency (นาที): min={lat[0]} median={lat[len(lat) // 2]} max={lat[-1]}")