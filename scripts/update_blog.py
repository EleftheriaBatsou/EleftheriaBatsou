import os
import re
import sys
import json
import time
import unicodedata
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from dateutil import parser as dateparser

README_PATH = "README.md"
START_MARK = "<!-- recent-blog-posts start -->"
END_MARK = "<!-- recent-blog-posts end -->"

DEVTO_USERNAME = "eleftheriabatsou"
COSINE_BLOG_INDEX = "https://cosine.sh/blog"
COSINE_SITEMAP = "https://cosine.sh/sitemap.xml"
AUTHOR_NAME = "Eleftheria Batsou"
MAX_POSTS = 6
TIMEOUT = 20

VERBOSE = os.getenv("RECENT_BLOG_VERBOSE", "").lower() in {"1", "true", "yes"}

# Optional manual fallback: comma-separated list of Cosine post URLs you know are yours
COSINE_AUTHOR_URLS = [u.strip() for u in os.getenv("COSINE_AUTHOR_URLS", "").split(",") if u.strip()]

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; GitHubAction; +https://github.com/EleftheriaBatsou)",
    "Accept-Language": "en",
    "Referer": "https://cosine.sh/",
})

def log(msg):
    if VERBOSE:
        print(msg)

def normalize_text(s):
    if not s:
        return ""
    # Normalize Unicode (handle non-breaking spaces, etc.), collapse whitespace
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u00A0", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def normalize_date(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def safe_get(url, timeout=TIMEOUT):
    try:
        r = session.get(url, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e:
        log(f"[WARN] GET failed: {url} -> {e}")
        return None

def fetch_devto_posts():
    url = f"https://dev.to/api/articles?username={DEVTO_USERNAME}"
    resp = safe_get(url)
    if not resp:
        return []
    try:
        data = resp.json()
    except Exception as e:
        log(f"[WARN] dev.to JSON parse failed: {e}")
        return []
    posts = []
    for item in data:
        published = item.get("published_at") or item.get("created_at")
        try:
            dt_raw = dateparser.parse(published)
        except Exception:
            dt_raw = None
        dt = normalize_date(dt_raw)
        posts.append({
            "source": "dev.to",
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "cover_image": item.get("cover_image") or item.get("social_image") or "",
            "date": dt,
            "date_str": dt.strftime("%Y-%m-%d") if dt else (published or ""),
        })
    log(f"[INFO] dev.to posts fetched: {len(posts)}")
    return posts

def get_cosine_links_from_index():
    resp = safe_get(COSINE_BLOG_INDEX)
    if not resp:
        return set()
    soup = BeautifulSoup(resp.text, "html.parser")
    links = set()
    for a in soup.select("a[href^='/blog/']"):
        href = a.get("href", "").strip()
        if not href:
            continue
        if href.rstrip("/").endswith("/blog"):
            continue
        if href.count("/") >= 2:
            full = f"https://cosine.sh{href}" if href.startswith("/") else href
            links.add(full)
    log(f"[INFO] Cosine index links found: {len(links)}")
    return links

def get_cosine_links_from_sitemap():
    resp = safe_get(COSINE_SITEMAP)
    if not resp:
        return set()
    soup = BeautifulSoup(resp.text, "xml")
    links = set()
    for loc in soup.find_all("loc"):
        url = (loc.get_text() or "").strip()
        if "/blog/" in url:
            links.add(url)
    log(f"[INFO] Cosine sitemap blog links found: {len(links)}")
    return links

def detect_author(page):
    # Meta name="author"
    meta_author = page.find("meta", attrs={"name": "author"})
    if meta_author and meta_author.get("content"):
        return normalize_text(meta_author["content"])

    # Common meta properties
    for prop in ["article:author", "og:article:author"]:
        m = page.find("meta", attrs={"property": prop})
        if m and m.get("content"):
            return normalize_text(m["content"])

    # rel=author
    rel_author = page.select_one("a[rel='author']")
    if rel_author:
        txt = normalize_text(rel_author.get_text())
        if txt:
            return txt

    # JSON-LD
    for ld in page.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(ld.string or "")
        except Exception:
            continue
        if isinstance(data, dict):
            a = data.get("author")
            if isinstance(a, dict) and a.get("name"):
                return normalize_text(a["name"])
            if isinstance(a, list) and a:
                entry = a[0]
                if isinstance(entry, dict) and entry.get("name"):
                    return normalize_text(entry["name"])
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    a = item.get("author")
                    if isinstance(a, dict) and a.get("name"):
                        return normalize_text(a["name"])
                    if isinstance(a, list) and a:
                        entry = a[0]
                        if isinstance(entry, dict) and entry.get("name"):
                            return normalize_text(entry["name"])

    # Visible text fallback
    page_text = normalize_text(page.get_text(" "))
    # Require both tokens in order, tolerant of extra text around
    if re.search(r"Eleftheria\s+Batsou", page_text, flags=re.IGNORECASE):
        return AUTHOR_NAME

    return None

def parse_post_page(url):
    r = safe_get(url)
    if not r:
        return None
    page = BeautifulSoup(r.text, "html.parser")

    author = detect_author(page)
    if not author or "eleftheria" not in author.lower() or "batsou" not in author.lower():
        log(f"[SKIP] Not authored by {AUTHOR_NAME}: {url} (detected: {author})")
        return None

    # Title
    title = None
    og_title = page.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = normalize_text(og_title["content"])
    if not title and page.title and page.title.string:
        title = normalize_text(page.title.string)
    if not title:
        h1 = page.find("h1")
        if h1:
            title = normalize_text(h1.get_text())
    if not title:
        title = url

    # Date
    dt = None
    date_str = ""
    pub_meta = page.find("meta", property="article:published_time")
    if pub_meta and pub_meta.get("content"):
        try:
            dt = dateparser.parse(pub_meta["content"])
        except Exception:
            date_str = pub_meta["content"]

    if not dt:
        time_el = page.find("time")
        candidate = (
            (time_el.get("datetime") if time_el else None)
            or (normalize_text(time_el.get_text()) if time_el else None)
        )
        if candidate:
            try:
                dt = dateparser.parse(candidate)
            except Exception:
                date_str = candidate

    if not dt:
        page_text = normalize_text(page.get_text(" "))
        m = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}", page_text)
        if m:
            candidate = m.group(0)
            try:
                dt = dateparser.parse(candidate)
            except Exception:
                date_str = candidate

    dt = normalize_date(dt)
    if dt and not date_str:
        date_str = dt.strftime("%Y-%m-%d")

    # Cover image
    cover = ""
    og_img = page.find("meta", property="og:image")
    if og_img and og_img.get("content"):
        cover = og_img["content"].strip()
    if not cover:
        twitter_img = page.find("meta", property="twitter:image")
        if twitter_img and twitter_img.get("content"):
            cover = twitter_img["content"].strip()
    if not cover:
        img = page.select_one("article img") or page.find("img")
        if img and img.get("src"):
            src = img["src"].strip()
            cover = src if src.startswith("http") else f"https://cosine.sh{src}" if src.startswith("/") else src

    return {
        "source": "Cosine",
        "title": title,
        "url": url,
        "cover_image": cover,
        "date": dt,
        "date_str": date_str or (dt.strftime("%Y-%m-%d") if dt else ""),
    }

def fetch_cosine_author_posts():
    index_links = get_cosine_links_from_index()
    sitemap_links = get_cosine_links_from_sitemap()
    manual_links = set(COSINE_AUTHOR_URLS)
    links = sorted(index_links.union(sitemap_links).union(manual_links))

    posts = []
    for url in links:
        time.sleep(0.3)  # be gentle
        p = parse_post_page(url)
        if p:
            posts.append(p)

    log(f"[INFO] Cosine posts authored by {AUTHOR_NAME}: {len(posts)}")
    return posts

def render_markdown_grid(posts):
    # HTML grid: 2 columns x ceil(n/2) rows; images width-limited
    # GitHub README supports raw HTML.
    rows = []
    # pad to even for pairing
    items = posts[:MAX_POSTS]
    if len(items) % 2 == 1:
        items.append({"title": "", "url": "", "cover_image": "", "source": "", "date_str": ""})

    def cell_html(p):
        if not p.get("title"):
            return "<td></td>"
        img_html = f'<img src="{p["cover_image"]}" alt="cover" style="width:280px; max-width:100%; border-radius:8px;" />' if p.get("cover_image") else ""
        title_html = f'<a href="{p["url"]}">{p["title"]}</a>'
        meta_html = f'{p.get("source","")} • {p.get("date_str","")}'
        return f"<td valign='top' style='padding:8px;'>{img_html}<div style='margin-top:6px; font-weight:600;'>{title_html}</div><div style='color:#666;'>{meta_html}</div></td>"

    for i in range(0, len(items), 2):
        left = cell_html(items[i])
        right = cell_html(items[i+1])
        rows.append(f"<tr>{left}{right}</tr>")

    html = []
    html.append("")
    html.append("### Recent Articles")
    html.append("")
    html.append("<table>")
    for r in rows[:3]:  # ensure 3 rows max
        html.append(r)
    html.append("</table>")
    html.append("")
    html.append("_Auto-updated daily from dev.to and cosine.sh/blog_")
    html.append("")
    return "\n".join(html)

def update_readme_section(new_content):
    if not os.path.exists(README_PATH):
        print("README.md not found.", file=sys.stderr)
        sys.exit(1)

    with open(README_PATH, "r", encoding="utf-8") as f:
        readme = f.read()

    if START_MARK not in readme or END_MARK not in readme:
        print("Markers not found in README.md. Please add the markers to enable updates.", file=sys.stderr)
        sys.exit(1)

    pattern = re.compile(
        re.escape(START_MARK) + r"(.*?)" + re.escape(END_MARK),
        re.DOTALL
    )
    updated = pattern.sub(
        START_MARK + "\n" + new_content + "\n" + END_MARK,
        readme
    )

    if updated != readme:
        with open(README_PATH, "w", encoding="utf-8") as f:
            f.write(updated)
        print("README.md updated.")
    else:
        print("README.md already up to date.")

def main():
    devto = fetch_devto_posts()
    cosine = fetch_cosine_author_posts()
    all_posts = devto + cosine

    def sort_key(p):
        return p["date"] or datetime.min.replace(tzinfo=timezone.utc)

    all_posts.sort(key=sort_key, reverse=True)
    latest = all_posts[:MAX_POSTS]

    md = render_markdown_grid(latest)
    update_readme_section(md)

if __name__ == "__main__":
    main()
