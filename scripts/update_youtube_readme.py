import os
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import List, Dict

PLAYLIST_ID = os.getenv("PLAYLIST_ID", "").strip()
MAX_ITEMS = int(os.getenv("MAX_ITEMS", "4"))
README_PATH = os.getenv("README_PATH", "README.md")
START_MARK = os.getenv("START_MARK", "<!-- YOUTUBE:GRID_START -->")
END_MARK = os.getenv("END_MARK", "<!-- YOUTUBE:GRID_END -->")

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015"
}

def fetch_feed(url: str) -> str:
    with urllib.request.urlopen(url) as resp:
        return resp.read().decode("utf-8")

def parse_entries(feed_xml: str) -> List[Dict]:
    root = ET.fromstring(feed_xml)
    entries = []
    for entry in root.findall("atom:entry", NS):
        title_el = entry.find("atom:title", NS)
        vid_el = entry.find("yt:videoId", NS)
        pub_el = entry.find("atom:published", NS)
        link_el = entry.find("atom:link[@rel='alternate']", NS) or entry.find("atom:link", NS)

        title = title_el.text if title_el is not None else ""
        video_id = vid_el.text if vid_el is not None else ""
        published = pub_el.text if pub_el is not None else ""
        url = f"https://www.youtube.com/watch?v={video_id}" if video_id else (link_el.attrib.get("href", "") if link_el is not None else "")

        entries.append({
            "title": title,
            "video_id": video_id,
            "published": published,
            "url": url
        })
    return entries

def iso_to_dt(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)

def render_thumbnail_url(video_id: str) -> str:
    # Use high-quality default thumbnail
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

def render_html_grid(items: List[Dict]) -> str:
    # 2x2 table grid to avoid CSS that GitHub might strip. Click through opens in new tab.
    # Note: GitHub does not allow inline playback of YouTube iframes; we show thumbnails + titles.
    rows = []
    # Ensure 4 items (or fewer if not available)
    display = items[:MAX_ITEMS]
    # Split into chunks of 2
    for i in range(0, len(display), 2):
        chunk = display[i:i+2]
        tds = []
        for e in chunk:
            vid = e["video_id"]
            thumb = render_thumbnail_url(vid) if vid else ""
            url = e["url"]
            title = e["title"].strip()
            date = iso_to_dt(e["published"]).date().isoformat()
            cell = (
                f"<td align=\"center\" valign=\"top\" width=\"50%\">"
                f"  <a href=\"{url}\" target=\"_blank\" rel=\"noopener noreferrer\">"
                f"    <img src=\"{thumb}\" alt=\"{title}\" style=\"width:100%; max-width:320px; border-radius:8px;\" />"
                f"  </a>"
                f"  <br/>"
                f"  <a href=\"{url}\" target=\"_blank\" rel=\"noopener noreferrer\"><strong>{title}</strong></a>"
                f"  <br/><em>{date}</em>"
                f"</td>"
            )
            tds.append(cell)
        # Pad row if odd number
        while len(tds) < 2:
            tds.append("<td width=\"50%\"></td>")
        rows.append("<tr>" + "".join(tds) + "</tr>")
    table = (
        "<table>"
        + "".join(rows) +
        "</table>"
    )
    return table

def update_readme_section(readme_text: str, new_block: str) -> str:
    # If markers exist, replace block
    if START_MARK in readme_text and END_MARK in readme_text:
        before = readme_text.split(START_MARK)[0]
        after = readme_text.split(END_MARK)[1]
        return f"{before}{START_MARK}\n{new_block}\n{END_MARK}{after}"

    # Else, insert above "Recent Blog Posts" section if present
    insert_header = "#### Recent Blog Posts"
    idx = readme_text.find(insert_header)
    if idx != -1:
        before = readme_text[:idx]
        after = readme_text[idx:]
        section_title = "#### Latest YouTube Videos\n"
        block = f"{section_title}{START_MARK}\n{new_block}\n{END_MARK}\n"
        return before + block + after

    # Fallback: append at end
    sep = "" if readme_text.endswith("\n") else "\n"
    section = f"\n#### Latest YouTube Videos\n{START_MARK}\n{new_block}\n{END_MARK}\n"
    return f"{readme_text}{sep}{section}"

def main():
    if not PLAYLIST_ID:
        print("Error: Provide PLAYLIST_ID.", file=sys.stderr)
        sys.exit(1)

    pl_url = f"https://www.youtube.com/feeds/videos.xml?playlist_id={PLAYLIST_ID}"
    feed_xml = fetch_feed(pl_url)

    entries = parse_entries(feed_xml)
    entries.sort(key=lambda e: iso_to_dt(e["published"]), reverse=True)

    html_grid = render_html_grid(entries)

    with open(README_PATH, "r", encoding="utf-8") as f:
        readme = f.read()

    updated = update_readme_section(readme, html_grid)

    if updated != readme:
        with open(README_PATH, "w", encoding="utf-8") as f:
            f.write(updated)
        print("README updated.")
    else:
        print("No changes required.")

if __name__ == "__main__":
    main()
