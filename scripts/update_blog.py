import os
import re
import sys
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime
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
    "User-Agent": "GitHubAction/UpdateRecentPosts (+https://github.com/EleftheriaBatsou)"
})

def fetch_devto_posts():
    url = f"https://dev.to/api/articles?username={DEVTO_USERNAME}"
    resp = session.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    posts = []
    for item in data:
        # Use published_at, cover_image, title, url
        published = item.get("published_at") or item.get("created_at")
        try:
            dt = dateparser.parse(published)
        except Exception:
            dt = None
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
    # Collect article links from index
    links = []
    for a in soup.select("a[href^='/blog/']"):
        href = a.get("href", "")
        # Filter only canonical blog post paths (avoid tag pages etc.)
        if href.count("/") >= 2:
            full = f"https://cosine.sh{href}" if href.startswith("/") else href
            if full not in links:
                links.append(full)

    posts = []
    # Visit each link, extract author, title, date, cover image via og tags or JSON-LD
    for url in links:
        try:
            r = session.get(url, timeout=TIMEOUT)
            r.raise_for_status()
        except Exception:
            continue
        page = BeautifulSoup(r.text, "html.parser")

        # Author detection: byline text or meta
        author = None
        # Common patterns: rel=author, byline, meta name="author", JSON-LD
        # Try meta first
        meta_author = page.find("meta", attrs={"name": "author"})
        if meta_author and meta_author.get("content"):
            author = meta_author["content"].strip()
        if not author:
            # rel=author links
            rel_author = page.select_one("a[rel='author']")
            if rel_author:
                author = rel_author.get_text(strip=True)
        if not author:
            # byline text
            byline = page.find(string=re.compile(r"by\s+", re.I))
            if byline:
                # crude extraction
                m = re.search(r"by\s+(.+)", byline, re.I)
                if m:
                    author = m.group(1).strip()

        # Fallback: JSON-LD
        if not author:
            for ld in page.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(ld.string or "")
                    if isinstance(data, dict):
                        a = data.get("author")
                        if isinstance(a, dict):
                            author = a.get("name")
                        elif isinstance(a, list) and a:
                            entry = a[0]
                            if isinstance(entry, dict):
                                author = entry.get("name")
                except Exception:
                    pass

        if not author or AUTHOR_NAME.lower() not in author.lower():
            continue  # Not Eleftheria's post

        # Title
        title = None
        if page.title and page.title.string:
            title = page.title.string.strip()
        if not title:
            og_title = page.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                title = og_title["content"].strip()
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
            # time tag
            time_el = page.find("time")
            if time_el:
                # try datetime attr first
                datetime_attr = time_el.get("datetime")
                text = time_el.get_text(strip=True)
                candidate = datetime_attr or text
                try:
                    dt = dateparser.parse(candidate)
                except Exception:
                    date_str = candidate or ""
        if not dt:
            # JSON-LD
            for ld in page.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(ld.string or "")
                    if isinstance(data, dict) and data.get("datePublished"):
                        candidate = data["datePublished"]
                        dt = dateparser.parse(candidate)
                        break
                except Exception:
                    pass
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
            # try first image in article
            img = page.select_one("article img") or page.find("img")
            if img and img.get("src"):
                src = img["src"]
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
        lines.append("")
    lines.append("")
    # Add a small note
    lines.append("_Auto-updated daily from dev.to and cosine.sh/blog_")
    lines.append("")
    return "\n".join(lines)

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

    # Sort by date desc; fall back to current time if missing
    def sort_key(p):
        return p["date"] or datetime.min

    all_posts.sort(key=sort_key, reverse=True)
    latest = all_posts[:MAX_POSTS]

    md = render_markdown(latest)
    update_readme_section(md)

if __name__ == "__main__":
    main()
