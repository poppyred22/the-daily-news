#!/usr/bin/env python3
"""Fetch today's Korean news on GitHub Actions; publish a same-origin JSON file."""
import json
import re
import time
import html
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
TODAY = datetime.now(KST).date().isoformat()
FEEDS = {
    "사회": [
        ("경향신문", "https://www.khan.co.kr/rss/rssdata/society_news.xml"),
        ("뉴시스", "https://www.newsis.com/RSS/society.xml"),
    ],
    "생활 문화": [
        ("경향신문", "https://www.khan.co.kr/rss/rssdata/culture_news.xml"),
        ("뉴시스", "https://www.newsis.com/RSS/culture.xml"),
    ],
    "사건 사고": [
        ("연합뉴스", "https://www.yna.co.kr/rss/news.xml"),
        ("SBS", "https://news.sbs.co.kr/news/newsflashRssFeed.do"),
    ],
    "세계": [
        ("경향신문", "https://www.khan.co.kr/rss/rssdata/kh_world.xml"),
        ("뉴시스", "https://www.newsis.com/RSS/international.xml"),
    ],
    "IT 과학": [
        ("경향신문", "https://www.khan.co.kr/rss/rssdata/science_news.xml"),
        ("전자신문", "https://rss.etnews.com/Section901.xml"),
    ],
}

def local_name(tag):
    return tag.rsplit("}", 1)[-1].lower()

def child_text(node, names):
    for child in list(node):
        if local_name(child.tag) in names:
            if local_name(child.tag) == "link":
                href = child.attrib.get("href")
                if href:
                    return href.strip()
            return "".join(child.itertext()).strip()
    return ""

def parse_date(raw):
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(KST)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(KST)
    except Exception:
        return None

def clean_text(value):
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\\s+", " ", value).strip()

def fetch_feed(source, url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "TheDailyNews/1.0 (+https://poppyred22.github.io/the-daily-news/)",
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    })
    with urllib.request.urlopen(req, timeout=18) as response:
        payload = response.read(5 * 1024 * 1024 + 1)
    if len(payload) > 5 * 1024 * 1024:
        raise ValueError("feed too large")
    root = ET.fromstring(payload)
    rows = []
    for node in root.iter():
        if local_name(node.tag) not in ("item", "entry"):
            continue
        title = clean_text(child_text(node, {"title"}))
        link = child_text(node, {"link"})
        if not link:
            for child in list(node):
                if local_name(child.tag) == "link" and child.attrib.get("href"):
                    link = child.attrib["href"].strip()
                    break
        raw_date = child_text(node, {"pubdate", "published", "updated", "date", "dc:date"})
        if not raw_date:
            for child in list(node):
                if child.tag.lower().endswith("}date"):
                    raw_date = "".join(child.itertext()).strip()
                    break
        dt = parse_date(raw_date)
        if not title or not link or not dt or dt.date().isoformat() != TODAY:
            continue
        description = clean_text(child_text(node, {"description", "summary", "encoded", "content"}))
        rows.append({
            "title": title,
            "link": link,
            "pubDate": dt.isoformat(),
            "author": source,
            "description": description[:700],
        })
    return rows

def main():
    categories = {name: [] for name in FEEDS}
    seen = set()
    diagnostics = {}
    for category, sources in FEEDS.items():
        for source, url in sources:
            try:
                rows = fetch_feed(source, url)
                diagnostics[source + ":" + category] = {"ok": True, "today_items": len(rows)}
                for row in rows:
                    key = re.sub(r"\\W+", "", row["title"].lower())
                    link = row["link"].split("?", 1)[0].rstrip("/")
                    if key in seen or link in seen:
                        continue
                    seen.add(key)
                    seen.add(link)
                    row["category"] = category
                    categories[category].append(row)
            except Exception as exc:
                diagnostics[source + ":" + category] = {"ok": False, "error": str(exc)[:180]}
            time.sleep(0.15)
        categories[category].sort(key=lambda row: row["pubDate"], reverse=True)
        categories[category] = categories[category][:10]

    total = sum(len(rows) for rows in categories.values())
    result = {
        "date": TODAY,
        "generatedAt": datetime.now(KST).isoformat(),
        "total": total,
        "categories": categories,
    }
    with open("news.json", "w", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, separators=(",", ":"))
    print("NEWS_DATE=" + TODAY)
    print("NEWS_TOTAL=" + str(total))
    for name, rows in categories.items():
        print("CATEGORY " + name + "=" + str(len(rows)))
    for name, status in diagnostics.items():
        print("FEED " + name + "=" + json.dumps(status, ensure_ascii=False))
    if not any(item["ok"] for item in diagnostics.values()):
        raise SystemExit("All RSS feeds failed; refusing to publish an empty feed.")

if __name__ == "__main__":
    main()
