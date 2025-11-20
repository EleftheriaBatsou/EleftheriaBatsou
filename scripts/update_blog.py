import os
import re
import sys
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from dateutil import parser as dateparser

README_PATH = "README.md"
START_MARK = "<!-- recent-blog-posts start -->"
END_MARK = "<!-- recent-blog-posts end -->"

DEVTO_USERNAME = "eleftheriabatsou"
COSINE_BLOG_INDEX = "https://cosine.sh/blog"
AUTHOR_NAME = "Eleftheria Batsou"
MAX_POSTS = 5
TIMEOUT = 20

session = requests.Session()
session.headers.update({
    # A more robust UA helps some CDNs
    "User-Agent": "Mozilla/5.0 (compatible; GitHubAction; +https://github.com/EleftheriaBatsou)",
    "Accept-Language": "en",
})

def normalize_date(dt):
    if not dt:
        return None
    # Make all datetimes timezone-aware in UTC
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def fetch_devto_posts():
    url = f"https://dev.to/api/articles?username={DEVTO_USERNAME}"
    resp = session.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
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
    return posts

def fetch_cosine_author_posts():
    # Fetch blog index
    resp = session.get(COSINE_BLOG_INDEX, timeout=TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Collect canonical blog post links
    links = set()
    for a in soup.select("a[href^='/blog/']"):
        href = a.get("href", "").strip()
        # Only include direct blog post paths (e.g., /blog/slug)
        if href and href.count("/") >= 2 and not href.rstrip("/").endswith("/blog"):
            full = f"https://cosine.sh{href}" if href.startswith("/") else href
            links.add(full)

    posts = []
    for url in sorted(links):
        try:
            r = session.get(url, timeout=TIMEOUT)
            r.raise_for_status()
        except Exception:
            continue

        page = BeautifulSoup(r.text, "html.parser")

        # Robust author detection
        author = None

        # 1) meta[name="author"]
        meta_author = page.find("meta", attrs={"name": "author"})
        if meta_author and meta_author.get("content"):
            author = meta_author["content"].strip()

        # 2) JSON-LD script(s)
        if not author:
            for ld in page.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(ld.string or "")
                except Exception:
                    continue
                # dict form
                if isinstance(data, dict):
                    a = data.get("author")
                    if isinstance(a, dict) and a.get("name"):
                        author = a["name"].strip()
                        break
                    if isinstance(a, list) and a:
                        entry = a[0]
                        if isinstance(entry, dict) and entry.get("name"):
                            author = entry["name"].strip()
                            break
                # list of things
                elif isinstance(data, list):
                    found = False
                    for item in data:
                        if isinstance(item, dict):
                            a = item.get("author")
                            if isinstance(a, dict) and a.get("name"):
                                author = a["name"].strip()
                                found = True
                                break
                            if isinstance(a, list) and a:
                                entry = a[0]
                                if isinstance(entry, dict) and entry.get("name"):
                                    author = entry["name"].strip()
                                    found = True
                                    break
                    if found:
                        break

        # 3) Fallback: exact match string anywhere in the page
        page_text = page.get_text(" ", strip=True)
        if not author and AUTHOR_NAME in page_text:
            author = AUTHOR_NAME

        if not author or AUTHOR_NAME.lower() not in author.lower():
            continue  # Not your post

        # Title
        title = None
        og_title = page.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            title = og_title["content"].strip()
        if not title and page.title and page.title.string:
            title = page.title.string.strip()
        if not title:
            h1 = page.find("h1")
            if h1:
                title = h1.get_text(strip=True)
        if not title:
            title = url

        # Date
        dt = None
        date_str = ""
        # Try meta article:published_time
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
                or (time_el.get_text(strip=True) if time_el else None)
            )
            if candidate:
                try:
                    dt = dateparser.parse(candidate)
                except Exception:
                    date_str = candidate

        # Fallback: scan text for "Month Day, Year"
        if not dt:
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

        posts.append({
            "source": "Cosine",
            "title": title,
            "url": url,
            "cover_image": cover,
            "date": dt,
            "date_str": date_str or (dt.strftime("%Y-%m-%d") if dt else ""),
        })

    return posts

def render_markdown(posts):
    # GitHub profile README supports images and links; keep it simple and robust.
    lines = []
    lines.append("")
    lines.append("### Recent Articles")
    lines.append("")
    for p in posts:
        cover_md = f"![cover]({p['cover_image']})" if p.get("cover_image") else ""
        date_md = p.get("date_str") or ""
        # Each item: image, title link, source, date
        lines.append(f"- {cover_md} ")
        lines.append(f"  ")
        lines.append(f"  [{p['title']}]({p['url']})")
        lines.append(f"  ")
        lines.append(f"  Source: {p['source']} • {date_md}")
