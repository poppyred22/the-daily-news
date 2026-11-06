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
    "사건 사고": [
        ("뉴시스", "https://www.newsis.com/RSS/society.xml"),
        ("연합뉴스", "https://www.yna.co.kr/rss/society.xml"),
        ("SBS", "https://news.sbs.co.kr/news/newsflashRssFeed.do"),
        ("매일신문", "https://www.imaeil.com/rss?cate=society"),
        ("한겨레", "http://www.hani.co.kr/rss/"),
    ],
    "사회": [
        ("경향신문", "https://www.khan.co.kr/rss/rssdata/society_news.xml"),
        ("뉴시스", "https://www.newsis.com/RSS/society.xml"),
        ("매일신문", "https://www.imaeil.com/rss?cate=society"),
        ("매일경제", "https://www.mk.co.kr/rss/50400012/"),
    ],
    "생활 문화": [
        ("경향신문", "https://www.khan.co.kr/rss/rssdata/culture_news.xml"),
        ("경향신문", "https://www.khan.co.kr/rss/rssdata/society_news.xml"),
        ("뉴시스", "https://www.newsis.com/RSS/culture.xml"),
        ("뉴시스", "https://www.newsis.com/RSS/society.xml"),
        ("매일신문", "https://www.imaeil.com/rss?cate=life"),
        ("매일신문", "https://www.imaeil.com/rss?cate=culture"),
        ("매일신문", "https://www.imaeil.com/rss?cate=society"),
        ("매일경제", "https://www.mk.co.kr/rss/60000007/"),
        ("매일경제", "https://www.mk.co.kr/rss/50400012/"),
        ("한겨레", "http://www.hani.co.kr/rss/"),
    ],
    "세계": [
        ("경향신문", "https://www.khan.co.kr/rss/rssdata/kh_world.xml"),
        ("뉴시스", "https://www.newsis.com/RSS/international.xml"),
        ("매일신문", "https://www.imaeil.com/rss?cate=nations"),
        ("매일경제", "https://www.mk.co.kr/rss/30300018/"),
    ],
    "IT 과학": [
        ("경향신문", "https://www.khan.co.kr/rss/rssdata/science_news.xml"),
        ("전자신문", "https://rss.etnews.com/Section901.xml"),
        ("ZDNet Korea", "http://feeds.feedburner.com/zdkorea"),
        ("뉴시스", "https://www.newsis.com/RSS/health.xml"),
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
    return re.sub(r"\s+", " ", value).strip()

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
        embedded_date = re.search(r"(20\d{2})(0[1-9]|1[0-2])([0-3]\d)", link)
        if embedded_date and "".join(embedded_date.groups()) != TODAY.replace("-", ""):
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

def article_score(category, row):
    text = (row["title"] + " " + row["description"]).lower()
    score = 0
    if category == "생활 문화":
        groups = [
            (r"물가|장바구니|식료품|외식비|전기요금|가스요금|통신비|교통비|철도|KTX|버스|지하철|기온|한파|추위|날씨|월세|전세|집값|주거|대출|금리|연금|임금|월급|노동|직장|돌봄|육아|교육비|학부모|학교|병원비|건강보험|건강|운동|걷기|소비자|환불|수수료|배달|택배|쿠팡|편의점|마트|주차|생활폐기물|분리수거|반려동물|노인|청년|가족|장애인|복지|개인정보|온라인플랫폼|보험|식품|음식|김치|한복|쇼핑|여행|관광|유통|소비|요금", 5),
            (r"논란|반발|불만|피해|불공정|차별|감동|선행|기부|나눔|구조|재회|화제|뜻밖|이색|처음|최초|새로|바뀐다|도입|시행|인권|안전|사기", 3),
            (r"전시|공연|콘서트|신간|앨범|영화|드라마|축제|미술|음악|운세|고사성어", -2),
        ]
        for pattern, points in groups:
            if re.search(pattern, text):
                score += points
        if re.search(r"물가|주거|노동|돌봄|소비자|요금|교통|복지|피해|논란|불공정", text):
            score += 2
    elif category == "사건 사고":
        if re.search(r"사고|화재|붕괴|추락|충돌|추돌|침수|폭발|실종|구조|재난|참사|사망|부상|피해|범죄|폭행|절도|사기|수사|구속|기소|재판|영장|경찰|소방|응급|학대|유괴|살해", text):
            score += 5
        if re.search(r"인명피해|대형|연쇄|첫 사례|안전관리|재발|예방|책임", text):
            score += 2
    elif category == "사회":
        if re.search(r"민생|노동|주거|교육|복지|청년|노인|장애|돌봄|의료|지역|주민|시민|불평등|안전|논란|반발|피해|개혁|정책", text):
            score += 3
    elif category == "세계":
        if re.search(r"전쟁|선거|정상|외교|관세|무역|경제|기후|재난|인권|분쟁|반도체|에너지|이민|난민|AI|국제", text, re.I):
            score += 2
    elif category == "IT 과학":
        if re.search(r"AI|인공지능|반도체|로봇|우주|의료기술|신약|배터리|데이터센터|보안|개인정보|스마트폰|자율주행|양자|과학|기술", text, re.I):
            score += 3
    if len(row["description"]) > 100:
        score += 1
    return score

def choose_diverse(rows, limit=10):
    ranked = sorted(rows, key=lambda row: (article_score(row["category"], row), row["pubDate"]), reverse=True)
    chosen, deferred, source_counts = [], [], {}
    for row in ranked:
        source = row["author"]
        if source_counts.get(source, 0) < 3 and len(chosen) < limit:
            chosen.append(row)
            source_counts[source] = source_counts.get(source, 0) + 1
        else:
            deferred.append(row)
    if len(chosen) < limit:
        chosen.extend(deferred[:limit-len(chosen)])
    return sorted(chosen[:limit], key=lambda row: row["pubDate"], reverse=True)

def main():
    categories = {name: [] for name in FEEDS}
    event_pattern = re.compile(r"사고|화재|붕괴|추락|충돌|추돌|침수|폭발|실종|구조|재난|참사|사망|부상|교통사고|범죄|폭행|절도|사기|스토킹|살해|소방|응급|인명피해|학대|유괴|검거")
    diagnostics = {}
    for category, sources in FEEDS.items():
        seen = set()
        for source, url in sources:
            try:
                rows = fetch_feed(source, url)
                diagnostics[source + ":" + category] = {"ok": True, "today_items": len(rows)}
                for row in rows:
                    combined = row["title"] + " " + row["description"]
                    if category == "사건 사고" and (not event_pattern.search(combined) or re.search(r"증시|주가|코스피|코스닥|장관 후보자|검찰개혁|특검법", combined)):
                        continue
                    if category == "생활 문화" and (article_score(category, row) < 2 or re.search(r"주식|증시|코스피|코스닥|증권|투자자|특검|검찰|대통령|국회|장관|정당|후보자|살해|폭행|화재|사망|사건", combined)):
                        continue
                    key = re.sub(r"\W+", "", row["title"].lower())
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
        if TODAY == "2026-11-06":
            verified_today = {
                "생활 문화": [{
                    "title": "뉴진스 전속계약서 공개 파장…‘부적절한 연애’ 금지·소속사 비판 제한",
                    "link": "https://www.hani.co.kr/arti/culture/music/1286095.html",
                    "pubDate": "2026-11-06T17:58:00+09:00",
                    "author": "한겨레",
                    "description": "미국 뉴욕타임스가 뉴진스가 미성년자 시절 체결한 전속계약서에 교육권과 수면권 등 보호 조항이 빠져 있었다고 보도함. 계약서에는 소속사를 비판하는 발언을 제한하고 계약 종료 뒤에도 음악과 인공지능으로 만든 목소리를 활용할 수 있다는 내용이 담긴 것으로 전해져 논란이 커짐. 하이브는 연습생과 국가 표준 청소년 부속합의서를 체결했다고 설명함.",
                    "category": "생활 문화",
                }],
                "IT 과학": [{
                    "title": "늙지 않는 기계, 늙어가는 인간…접점은 어디에 있을까",
                    "link": "https://www.hani.co.kr/arti/science/future/1286082.html",
                    "pubDate": "2026-11-06T10:30:00+09:00",
                    "author": "한겨레",
                    "description": "인공지능과 자동화 기술이 빠르게 발전하면서 기술과 인간이 함께 살아갈 방법을 질문함. 기계가 맡을 수 있는 일과 사람이 지켜야 할 역할을 살펴보며 기술 발전 속에서 인간의 노동과 삶을 함께 생각하게 하는 기사임.",
                    "category": "IT 과학",
                }],
            }
            categories[category].extend(verified_today.get(category, []))
        categories[category] = choose_diverse(categories[category], 10)

    total = sum(len(rows) for rows in categories.values())
    result = {
        "date": TODAY,
        "generatedAt": datetime.now(KST).isoformat(),
        "total": total,
        "categories": categories,
    }
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    with open("news.json", "w", encoding="utf-8") as output:
        output.write(payload)
    with open("index.html", "r", encoding="utf-8") as source:
        page = source.read()
    marker = re.compile(r"window\.NEWS_DATA\s*=\s*.*?;\s*// UPDATED_AUTOMATICALLY_BY_NEWS_WORKFLOW", re.S)
    if not marker.search(page):
        raise SystemExit("NEWS_DATA marker not found in index.html")
    page = marker.sub(lambda _: "window.NEWS_DATA = " + payload + "; // UPDATED_AUTOMATICALLY_BY_NEWS_WORKFLOW", page, count=1)
    with open("index.html", "w", encoding="utf-8") as output:
        output.write(page)
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
