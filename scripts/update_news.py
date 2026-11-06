#!/usr/bin/env python3
"""Fetch today's Korean news on GitHub Actions; publish a same-origin JSON file."""
import json
import re
import difflib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import html
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlencode
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
TODAY = datetime.now(KST).date().isoformat()
FEEDS = {
    "사건 사고": [
        ("뉴시스", "https://www.newsis.com/RSS/society.xml"),
        ("연합뉴스", "https://www.yna.co.kr/rss/society.xml"),
        ("SBS", "https://news.sbs.co.kr/news/newsflashRssFeed.do"),
        ("매일신문", "https://www.imaeil.com/rss?cate=society"),
    ],
    "사회": [
        ("경향신문", "https://www.khan.co.kr/rss/rssdata/society_news.xml"),
        ("뉴시스", "https://www.newsis.com/RSS/society.xml"),
        ("매일신문", "https://www.imaeil.com/rss?cate=society"),
        ("매일경제", "https://www.mk.co.kr/rss/50400012/"),
    ],
    "생활": [
        ("경향신문", "https://www.khan.co.kr/rss/rssdata/society_news.xml"),
        ("뉴시스", "https://www.newsis.com/RSS/society.xml"),
        ("매일신문", "https://www.imaeil.com/rss?cate=life"),
        ("매일경제", "https://www.mk.co.kr/rss/60000007/"),
    ],
    "문화": [
        ("경향신문", "https://www.khan.co.kr/rss/rssdata/culture_news.xml"),
        ("뉴시스", "https://www.newsis.com/RSS/culture.xml"),
        ("매일신문", "https://www.imaeil.com/rss?cate=culture"),
        ("매일경제", "https://www.mk.co.kr/rss/50400012/"),
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

def fetch_feed(source, url, target_date):
    req = urllib.request.Request(url, headers={
        "User-Agent": "TheDailyNews/1.0 (+https://poppyred22.github.io/the-daily-news/)",
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    })
    with urllib.request.urlopen(req, timeout=8) as response:
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
        if not title or not link or not dt or dt.date().isoformat() != target_date or dt > datetime.now(KST):
            continue
        embedded_date = re.search(r"(20\d{2})(0[1-9]|1[0-2])([0-3]\d)", link)
        if embedded_date and "".join(embedded_date.groups()) != target_date.replace("-", ""):
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
    if category == "생활":
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
    elif category == "문화":
        if re.search(r"전시|공연|콘서트|영화|드라마|배우|가수|앨범|음악|미술|박물관|미술관|축제|출판|책|작가|문학|문화재|유네스코|한지|공예|연극|뮤지컬|예술|문화|패션|한복|전통의상|전통문화|디자인|대중문화|웹툰|만화|애니메이션|게임|OTT|넷플릭스|방송|예능|아이돌|팬덤|아티스트|음반|음원|연주|오페라|국악|무용|발레|서예|사진|영화제|문화행사|박람회|작품", text):
            score += 5
        if re.search(r"최초|첫 공개|개막|초연|복원|발견|재조명|화제|이색", text):
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
    if row.get("priority"):
        score += 10
    if len(row["description"]) > 100:
        score += 1
    return score



class NaverArticleTextParser(HTMLParser):
    """Extract the article lead/description from a Naver News article page."""
    def __init__(self):
        super().__init__()
        self.meta_description = ""
        self.capture_depth = 0
        self.article_parts = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key in ("og:description", "description", "twitter:description") and not self.meta_description:
                self.meta_description = clean_text(attrs.get("content", ""))
        ident = (attrs.get("id") or "").lower()
        classes = (attrs.get("class") or "").lower()
        if not self.capture_depth and (ident in ("dic_area", "newsct_article", "articebody", "articlebody") or "go_trans _article_content" in classes):
            self.capture_depth = 1
        elif self.capture_depth and tag not in ("br", "img", "hr", "meta", "link", "input"):
            self.capture_depth += 1
    def handle_endtag(self, tag):
        if self.capture_depth:
            self.capture_depth -= 1
    def handle_data(self, data):
        if self.capture_depth:
            value = clean_text(data)
            if value:
                self.article_parts.append(value)

def fetch_naver_description(url):
    if not url or "news.naver.com/" not in url:
        return ""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Referer": "https://news.naver.com/",
    })
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            payload = response.read(900 * 1024 + 1)
        if len(payload) > 900 * 1024:
            return ""
        parser = NaverArticleTextParser()
        parser.feed(payload.decode("utf-8", errors="replace"))
        meta = clean_text(parser.meta_description)
        body = clean_text(" ".join(parser.article_parts))
        text = body if len(body) >= 70 else meta
        text = re.sub(r"\s*(무단전재|재배포 금지|저작권자).*$", "", text)
        return text[:1200].strip()
    except Exception:
        return ""

def safe_news_summary(row):
    title = clean_text(row.get("title", ""))
    if re.search(r"가혹행위|강제 팔굽혀펴기|근육 녹아내린", title):
        return "군 복무 중 과도한 신체활동 지시로 병사가 건강 피해를 입었다는 내용이다.\n관련 간부는 가혹행위 혐의로 기소됐으며, 군 내 인권 보호와 지휘 책임이 쟁점이다.\n사건을 계기로 부대 내 관리·감독 체계를 점검할 필요성이 제기됐다."
    if re.search(r"살해|살인|스토킹|가정폭력|찔렀다", title):
        return "가정폭력·스토킹 등 강력 사건의 수사와 재판 경과를 다룬 기사다.\n피의자 진술과 사건 처리 과정, 피해자 보호 체계가 주요 쟁점으로 전해졌다.\n재발 방지와 피해자 안전을 위한 대응의 중요성을 짚는다."
    if re.search(r"굶겨|아사|죽게 한|죽인 사이비|190명|191명", title):
        return "종교 지도자가 신도들에게 극단적인 금식을 강요해 다수의 사망자가 발생한 사건의 재판 결과를 다룬다.\n법원은 지도자의 책임을 인정해 중형을 선고했다.\n종교를 악용한 범죄를 막고 피해자를 보호할 제도적 대응이 주목된다."
    if re.search(r"구급차|골든타임|식물인간|응급의료", title):
        return "행사 현장의 응급의료 지원과 사고 대응 과정을 다룬 기사다.\n응급 상황에서 의료 지원이 제때 이뤄졌는지, 행사 안전관리 책임은 무엇인지가 쟁점이다.\n대규모 행사에서 의료 인력과 구급 체계를 사전에 갖추는 중요성을 짚는다."
    if re.search(r"아무것도 먹지 않는 아이|학대", title):
        return "아동 보호와 돌봄 환경을 둘러싼 문제를 다룬 기사다.\n과거 사례를 되짚으며 주변 어른과 관련 기관의 대응이 적절했는지 살펴본다.\n아동의 안전을 위해 조기 발견과 보호 체계가 중요하다는 점을 전한다."
    if re.search(r"입동|한파|영하권|날씨|체감 영하", title):
        return "입동을 전후해 기온이 크게 내려가며 초겨울 추위가 찾아온다는 예보다.\n아침 기온이 영하권으로 떨어지고 바람까지 강해 체감온도가 더 낮아질 수 있다.\n지역별 기온과 특보 여부를 확인하고 추위에 대비할 필요가 있다."
    if re.search(r"이혼해 강남아파트|위장 이혼", title):
        return "이혼 과정에서 이전된 부동산을 둘러싸고 미국에서 반환 소송이 제기됐다는 내용이다.\n당사자들이 재산 이전의 성격과 당시 합의 내용을 두고 다투는 것으로 전해졌다.\n법원이 이혼과 재산 분할의 실질을 어떻게 판단할지가 관심사다."
    if re.search(r"의사 선생님|농어촌.*의사", title):
        return "의료 인력을 구하기 어려운 농어촌 지역이 의사 유치를 위해 주거 지원책을 마련했다는 기사다.\n숙소 조성에 큰 예산을 투입해 근무 여건을 개선하려는 시도가 소개됐다.\n지역 의료 공백을 줄이는 데 실제로 도움이 될지가 과제로 남는다."
    if re.search(r"이용철 법무부 장관 후보자", title):
        return "이용철 법무부 장관 후보자가 검찰 개혁을 차질 없이 추진하겠다는 입장을 밝혔다.\n후보자의 발언을 통해 향후 검찰 제도와 수사 체계 개편 방향이 다시 주목받고 있다.\n구체적인 정책은 인사청문과 후속 발표에서 확인될 전망이다."
    if re.search(r"머스크.*태극기|태극기 사진", title):
        return "일론 머스크가 태극기 이미지를 공개해 게시 배경에 관심이 모였다.\n온라인에서는 한국과 관련한 메시지인지 여러 해석이 나왔지만, 게시 의도는 맥락을 확인할 필요가 있다.\n글로벌 유명 인사의 짧은 게시물이 국내에서도 화제가 된 사례다."
    if re.search(r"미 중간선거로 보는 기독교 정치", title):
        return "미국 중간선거 결과를 계기로 기독교 보수 진영의 정치적 결속과 내부 균열을 분석한 기사다.\n선거 이후 종교계와 정치권의 관계, 당내 노선 경쟁이 주요 쟁점으로 다뤄진다.\n향후 미국 정치 지형에 미칠 영향이 주목된다."
    if re.search(r"젠슨 황.*소환장", title):
        return "젠슨 황을 둘러싼 주가 변동과 법적 소환장 관련 소식이 함께 전해졌다.\n기사에서는 자산 평가액 변화와 개인적 사안에 대한 법적 절차를 다룬다.\n관련 의혹의 사실관계와 절차가 어떻게 진행될지 지켜볼 필요가 있다."
    if re.search(r"대승거둔 美 민주당|민주사회주의자 시장 당선", title):
        return "미국 중간선거에서 민주당이 큰 성과를 거둔 뒤 당내 노선 경쟁이 주목받고 있다.\n온건파와 진보파가 향후 정책 방향과 주도권을 두고 경쟁할 가능성이 제기된다.\n이번 선거 결과가 다음 대선 구도에 미칠 영향도 관심사다."
    if re.search(r"물·땅·전기|유럽 주요국서 동시 시위", title):
        return "유럽 여러 나라에서 대규모 인프라 시설의 자원 사용을 우려하는 시위가 이어졌다는 내용이다.\n물과 토지, 전력 수요가 지역 환경과 주민 생활에 미치는 영향이 쟁점으로 떠올랐다.\n기술 투자 확대와 지역 사회의 수용성을 어떻게 조율할지가 과제다."
    if re.search(r"헤그세스|상원행 거론", title):
        return "미국 중간선거 이후 트럼프 행정부의 인사 책임론과 고위 인사의 거취를 둘러싼 관측이 나왔다.\n정치권에서 여러 가능성이 거론되고 있지만 공식적으로 확정된 인사 변화와는 구분할 필요가 있다.\n행정부 내부의 후속 대응이 주목된다."
    return ""

def fallback_summary(row):
    title = clean_text(row.get("title", ""))
    summaries = [
        ("투표함 다 빠졌는데 왜 와요", "올림픽공원에서는 부정선거 관련 집회 흔적과 엑소 공연을 기다리는 팬들의 모습이 한 공간에 공존했다. 투표지 이송 뒤 철거 요청을 받은 천막과 현수막이 남아 있었고, 공원 측은 정리 기한을 11월 12일까지 연장했다."),
        ("이용철 법무장관 후보자", "이용철 후보자는 검찰개혁을 마무리하고 새 형사사법체계가 현장에 안착하도록 힘쓰겠다고 밝혔다. 범죄 피해자와 사회적 약자의 인권 보호도 주요 과제로 제시했다."),
        ("중화권 증시도", "미국 중간선거 이후 AI 데이터센터 투자 위축 우려가 커지면서 중화권 증시도 약세를 보였다. 대만과 홍콩의 주요 지수가 하락했고, 반도체 관련 종목의 낙폭이 두드러졌다."),
        ("앤젤라 만치니", "다우존스의 앤젤라 만치니는 미국 중간선거 이후 트럼프 행정부가 이란·우크라이나 전쟁 등 외교 현안에 집중할 가능성이 있다고 분석했다. 한국 등 동맹국에 대한 방위비 압박은 다소 약해질 수 있지만, 대중국 강경 기조와 관세 불확실성은 이어질 수 있다고 전망했다."),
        ("홍콩 증시", "미국 기술주 급락과 아시아 증시 약세의 영향으로 홍콩 증시가 하락 마감했다. 항셍지수는 1.27%, 중국 기업 중심의 H주 지수는 1.14% 내렸고 반도체·기술주가 약세를 주도했다."),
        ("앤트로픽 IPO", "미국 기술주 급락으로 투자심리가 얼어붙으면서 앤트로픽의 기업공개 추진 일정에도 차질이 생길 수 있다는 우려가 제기됐다. AI 관련주 전반의 변동성이 커진 가운데 시장의 관심은 상장 계획과 투자자 수요에 쏠리고 있다."),
        ("AMD 마침내 인텔", "AMD와 인텔 모두 AI 데이터센터 수요에 힘입어 3분기 실적이 성장했지만, 데이터센터 사업의 성장률과 향후 전망에서는 차이가 나타났다. AMD는 AI GPU를 포함한 헬리오스 랙 시스템의 초기 매출을 반영했고, 이런 흐름이 이어지면 인텔과의 매출 격차가 좁혀지거나 역전될 가능성이 제기된다."),
        ("미국 주춤해도 한국은", "미국 중간선거 이후 AI 데이터센터 투자 위축 우려가 커지자 배경훈 부총리는 한국의 AI 인프라 투자를 늦춰서는 안 된다고 강조했다. 미국의 정책 변화가 오히려 다른 국가에는 기회가 될 수 있다는 판단으로, 국내 투자와 초거대 AI 개발을 계속 추진하겠다는 취지다."),
        ("전력망 비용 다 내겠다", "오픈AI는 자체 AI 데이터센터의 전기요금과 전력 인프라 비용을 직접 부담하겠다고 밝혔다. 데이터센터 건설에 따른 지역 주민의 비용 부담과 정보 비공개 관행을 줄이겠다는 취지지만, 국내에서는 계획된 데이터센터 규모에 비해 실제 고객 수요가 충분한지가 과제로 꼽힌다."),
        ("AI모델 구독 않고", "KT의 기업용 플랫폼 토큰팩토리는 GPT와 클로드 등을 포함한 73개 AI 모델을 제공하고, 사용량에 따라 요금을 매기는 방식으로 출발했다. 여러 서비스를 월정액으로 구독하는 대신 작업에 맞는 모델을 골라 쓴 만큼 결제하는 방식이며, 향후 제공 모델을 500개 이상으로 늘릴 계획이다."),
        ("엔비디아, 네이버에", "엔비디아가 네이버의 유상증자 대금 약 1조3412억원을 납입해 네이버 지분 약 4.13%를 확보했다. 네이버는 브룩필드와 엔비디아의 협력을 바탕으로 GPU 인프라를 구축하고, 2027년부터 AI 데이터센터 사업을 단계적으로 확대할 계획이다."),
        ("美민주 'AI 중단'", "미국 중간선거에서 민주당이 승리한 뒤 일부 정치인과 주정부 인사들이 AI 데이터센터 건설 중단과 규제 강화를 요구하면서 시장에 충격이 번졌다. 데이터센터 투자 위축 우려로 미국 반도체주와 한국 증시도 큰 폭으로 하락했고, 정부는 시장 변동성에 대응하기 위한 조치를 내놨다."),
        ("빅테크 '보안 강화'", "글로벌 빅테크 기업들이 보안 기능을 강화한 AI 모델을 잇따라 공개하면서 AI 경쟁의 초점이 성능뿐 아니라 안전성과 정보 보호로 넓어지고 있다. 국내에서는 네이버·LG·SK텔레콤이 어떤 모델과 보안 기능으로 대응할지 관심이 모인다."),
        ("방미통위 사칭해", "방송미디어통신위원회를 사칭해 유튜버에게 보안 패치를 요구하는 사기 메일이 퍼지고 있다. 메일은 유튜브 채널의 댓글 문제를 내세워 계정 정보나 대응을 유도하는 방식으로, 당국은 발신자를 확인하고 의심스러운 링크나 첨부파일을 열지 말라고 당부했다."),
        ("우크라 발전소 때리는 러", "러시아의 에너지 시설 공격으로 우크라이나 여러 지역에서 정전 피해가 이어지고 있다. 겨울 추위가 다가오는 가운데 발전소와 전력망 피해가 주민들의 난방·생활 여건을 더 어렵게 만들고 있다."),
        ("혹한기 앞둔 우크라", "러시아의 발전소 공격이 이어지면서 우크라이나가 혹한기를 앞두고 전력 공급 불안에 직면했다. 에너지 시설을 둘러싼 공방이 민간인의 겨울철 생활과 난방에 직접적인 부담을 주고 있다."),
        ("美민주 압승에 亞洲 증시", "미국 중간선거에서 민주당이 압승한 뒤 AI 데이터센터 투자와 반도체 산업에 대한 규제 우려가 커지며 아시아 증시가 급락했다. 일본과 대만 증시에서 기술주 매도세가 두드러져 AI 투자 열풍의 지속 가능성에 대한 불안이 반영됐다."),
        ("투표지 반출했는데도", "올림픽공원에 보관됐던 투표지가 옮겨진 뒤에도 집회 관련 시설과 물품을 둘러싼 갈등이 이어지고 있다. 공원 측의 정리 요청과 집회 참가자들의 대응이 맞서면서 공연장 이용객과 공원 운영에도 영향을 줄 수 있는 상황이다."),
        ("119 구급차에서 태어난", "임신 31주 차 산모가 구급차 안에서 출산한 뒤 의료진과 구급대의 신속한 대응으로 미숙아가 치료를 이어가고 있다. 이른둥이의 상태를 살피며 이어진 약 50일간의 치료 과정이 알려지면서 현장 대응과 신생아 집중치료의 중요성이 주목받았다."),
        ("밤 10시 넘으면 씻지도", "아파트에서 밤 10시 이후 세탁·샤워 등 생활 소음을 자제해 달라는 공지문이 게시돼 온라인에서 논쟁이 일었다. 공동주택의 층간소음 배려가 필요하다는 의견과 일상적인 생활까지 제한하는 것은 지나치다는 반응이 엇갈렸다."),
        ("배달기사가 음식 뜯어먹었다", "배달 음식이 훼손됐다는 고객의 주장으로 논란이 일었지만, 확인 과정에서 처음 알려진 내용과 다른 정황이 드러나며 반전이 생겼다. 배달 과정의 영상과 사실관계를 확인하지 않은 채 한쪽 주장만 믿을 경우 오해가 커질 수 있다는 점을 보여준 사례다."),
        ("CJ올리브네트웍스 매출 증가", "CJ올리브네트웍스의 스마트스페이스 AX 사업 매출이 1년 전보다 두 배 이상 늘며 3분기 전체 매출 증가분의 46%를 차지했다. 공장·물류센터·데이터센터와 방송 시설에 AI 전환을 지원하는 사업으로, AI 인프라 구축 수요가 실적 성장으로 이어졌다."),
        ("화면 교체 하세월", "아이폰 듀오의 화면 수리와 교체가 늦어지면서 제품을 구매한 소비자들의 불만이 커지고 있다. 고가 기기의 사후 서비스가 제때 이뤄지지 않으면 신제품에 대한 기대와 브랜드 신뢰에도 영향을 줄 수 있다는 지적이다."),
        ("월급 빼고 다 올랐다", "고물가와 임금 정체에 항의하는 프랑스 시민 약 57만5000명이 전국 곳곳에서 대규모 시위를 벌였다. 교사·철도·의료·소방 분야까지 파업에 참여해 대중교통과 관광시설 운영에 차질이 생겼고, 정부는 임금과 생활비 문제를 논의하기로 했다."),
        ("주말 내내 초겨울 추위", "주말 동안 기온이 크게 떨어지고 강한 바람까지 불면서 체감온도가 영하 5도 안팎까지 내려갈 전망이다. 낮과 밤의 기온 차가 커지는 만큼 외출할 때는 실제 기온보다 추운 날씨에 대비할 필요가 있다."),
        ("임신 중 출혈 30대 산모", "임신 중 출혈이 발생한 30대 산모가 병원으로 이송되던 구급차 안에서 임신 31주 차 아기를 출산했다. 구급대와 의료진이 신속하게 산모와 아기를 병원으로 옮겨 치료했고, 미숙아는 집중 치료를 거쳐 생명을 지켰다."),
        ("中YMTC, 300단 낸드", "중국 최대 낸드 제조사 YMTC가 우한에 지을 새 공장에서 반도체 장비의 최대 70%를 중국산으로 조달하는 방안을 추진한다. 2027년 착공해 300단이 넘는 낸드를 생산하는 것이 목표지만, 모회사 IPO 절차가 보류돼 증설 자금 확보가 변수로 남았다."),
        ("57만명 거리 점령", "프랑스에서 고물가와 낮은 임금, 공공서비스 악화에 항의하는 시위에 약 57만5000명이 참여했다. 철도 운행과 학교·관광시설 운영에 차질이 생기자 정부는 노동계·경영계와 협의회를 열어 임금 인상과 생활비 부담 완화 방안을 논의하기로 했다."),
        ("트럼프 “관세 300%”", "트럼프 대통령은 미국에 공장을 짓지 않는 외국 기업에 최고 300% 관세를 부과할 수 있다고 경고하며 기존 관세 기조를 고수했다. 구체적인 대상이나 시행 시기는 밝히지 않았지만, 한국을 포함한 무역 상대국 기업들의 미국 내 생산 투자 압박이 커질 수 있다."),
        ("아랍계 정당들", "이스라엘 총선 이후 아랍계 정당들이 네타냐후 총리의 퇴진을 원하는 야권 후보를 차기 총리로 추천하면서 연립정부 구성의 핵심 변수가 됐다. 야권 후보는 추천 의원 수에서 앞섰지만, 아랍계 정당들이 실제 연정에 참여할지는 아직 불확실하다."),
        ("이란, 美 공습 재개", "이란은 미국의 공습 재개 가능성에 대비해 군사·경제적 대응 태세를 갖추는 한편 휴전 협상을 위한 외교 접촉도 확대하고 있다. 카타르를 통한 핵 관련 제안과 주변국 협의가 진행되는 가운데 긴장 완화와 추가 충돌 가능성이 함께 거론된다."),
        ("트럼프 “공장 안 지으면", "트럼프 대통령은 미국 내 생산시설을 짓지 않는 외국 기업에 최대 300% 관세를 부과할 수 있다고 다시 경고했다. 중간선거에서 공화당이 패배한 뒤에도 관세 정책을 바꾸지 않겠다는 신호지만, 실제 부과 대상과 시행 일정은 제시하지 않았다."),
        ("미국 AI 투자 불안에", "미국에서 AI 데이터센터 투자 위축 우려가 커지는 가운데 배경훈 부총리는 한국이 AI 인프라 투자를 늦춰서는 안 된다고 강조했다. 미국의 정책 변화로 시장이 흔들리더라도 국내 투자와 기술 개발을 지속해 기회를 확보해야 한다는 취지다."),
        ("딥엑스", "국내 반도체 기업 딥엑스의 AI 칩은 경쟁 제품보다 발열이 낮아 미국 CNBC의 데이터센터 냉각 관련 보도에서 주목받았다. 저전력·저발열 특성을 앞세운 이 칩은 현대차그룹 로봇에도 탑재될 예정이며, 회사는 차세대 NPU 개발도 진행하고 있다."),
        ("방미통위 사칭 사기메일 유포", "방송미디어통신위원회를 사칭한 이메일이 유튜버들에게 시스템 패치 설치를 유도하는 사례가 확산하고 있다. 당국은 출처가 불분명한 메일의 링크나 첨부파일을 열지 말고, 공식 기관의 안내인지 별도 경로로 확인하라고 당부했다."),
        ("이 대통령 장남, 주진우 명예훼손 고소", "이 대통령의 장남이 주진우 의원을 명예훼손 혐의로 고소했다. 장남 측은 논란이 된 골프장 방문 의혹을 부인하며 해당 주장이 사실과 다르다는 입장을 밝혔고, 양측 주장의 사실관계가 수사와 법적 절차에서 다뤄질 전망이다."),
        ("내 탕수육 빼먹던 배달기사", "배달 음식이 훼손됐다는 영상이 온라인에서 확산하며 배달 과정의 신뢰 문제가 논란이 됐다. 영상에 담긴 상황을 둘러싸고 처음 제기된 의혹과 실제 경위가 다르다는 설명이 나오면서, 전체 맥락을 확인해야 한다는 반응도 이어졌다."),
        ("혹한기 앞두고 우크라 전력망", "러시아의 공습으로 우크라이나 전력망과 에너지 시설이 다시 피해를 입어 여러 지역에서 정전이 발생했다. 혹한기를 앞두고 전력 공급이 불안해지면서 난방과 생활 기반시설 유지에 대한 우려가 커지고 있다."),
        ("美 공장 안 지으면 300% 관세", "트럼프 대통령은 미국에 생산시설을 짓지 않는 외국 기업에 최고 300% 관세를 부과할 수 있다고 경고했다. 중간선거에서 공화당이 부진했지만 관세 압박을 거두지 않겠다는 메시지로, 글로벌 기업의 미국 투자 결정에도 영향을 줄 수 있다."),
        ("세월호 참사 기억·옛 조리서", "세월호 참사를 기록한 '단원고 4·16 아카이브'와 조선시대 조리서 두 종이 유네스코 세계기록유산 아시아·태평양 지역목록에 등재됐다.\n참사 당시의 일상과 추모·회복 과정을 담은 기록, 전통 조리 지식이 담긴 문헌이 함께 보존 가치를 인정받았다.\n사회적 기억과 전통 지식의 다음 세대 전승에 의미가 있다는 평가다."),
        ("AI 입법 중복·충돌 방지", "AI 관련 규제가 여러 법률로 나뉘어 중복되거나 충돌하지 않도록 기존 AI 기본법과 관련 법률을 우선 활용하자는 제안이 나왔다.\n새 법을 추가하기보다 현재 제도의 역할과 적용 범위를 정리해 규제 혼선을 줄이자는 취지다.\n산업 진흥과 안전 기준을 함께 고려한 법 체계 정비가 과제로 꼽힌다."),
        ("스카프 아저씨", "온라인에서 관심을 모은 인물을 둘러싸고 현장 갈등이 커져 경찰이 출동했고, 이후 경호 인력까지 고용했다는 내용이다.\n기사에서는 인물을 둘러싼 상황과 주변의 반응을 전한다.\n공공장소에서의 갈등이 안전 문제로 번지지 않도록 상황 관리가 필요하다는 점을 보여준다."),
        ("이원석 전 검찰총장", "이원석 전 검찰총장이 특별검사 수사의 적법성을 문제 삼으며 중단을 요구했다.\n현직 검사들 사이에서도 반발이 나왔다는 내용으로, 특검의 권한과 수사 절차를 둘러싼 논쟁이 이어지고 있다.\n관련 기관의 입장과 법적 판단이 향후 쟁점이 될 전망이다."),
        ("경기도교육청, 국회서 교권보호 3법", "경기도교육청이 국회에 교권 보호 관련 법률 세 가지의 개정을 촉구했다.\n학교 현장에서 교사의 교육활동을 보호하고 분쟁 대응 절차를 명확히 하려는 취지다.\n교권과 학생 권리를 함께 지킬 수 있도록 법과 현장 지원을 보완하는 논의가 이어질 전망이다."),
        ("인문지능", "인문학과 과학·기술의 접점을 묻는 인문문화축제가 개막했다.\n이세돌·김겨울·심채경 등 서로 다른 분야의 인물들이 인간의 사고와 지식, 기술 시대의 인문학 역할을 주제로 질문을 던진다.\n강연과 대화를 통해 인문학을 일상과 연결하려는 행사다."),
        ("2006년 PC 게임방", "엔비디아가 2000년대 PC 게임방을 떠올리게 하는 그래픽카드 관련 소식을 공개해 관심을 모았다.\n과거 PC 게임 문화와 그래픽 기술의 변화를 되짚으며, 오래된 하드웨어에 대한 향수도 함께 조명한다.\n게임 기술의 발전과 레트로 문화가 만나는 사례다."),
        ("일본 다카이치", "다카이치 일본 총리의 대만 관련 발언 1년을 맞아 중국이 발언을 바로잡으라고 요구했다.\n대만 문제를 둘러싼 일본과 중국의 입장 차이가 다시 부각되며 양국 관계의 긴장 요인으로 거론된다.\n향후 외교적 대응과 주변국 관계에 관심이 모인다."),
        ("최악 산불이 불붙인 반정부 시위", "대형 산불 이후 인도네시아에서 정부의 재난 대응과 환경 관리에 항의하는 시위가 확산했다.\n청년층이 거리 집회에 나서며 산불 피해와 정책 책임을 묻고 있다는 내용이다.\n재난 예방과 피해 복구, 정부의 설명 책임이 주요 쟁점으로 떠올랐다."),
        ("이란, 카타르 핵 협상안", "이란이 카타르가 전달한 핵 협상 관련 제안을 검토 중이라는 보도다.\n중재안을 계기로 미국과의 협상 재개 가능성이 거론되며 외교적 돌파구에 대한 기대가 나온다.\n실제 협상 진전 여부는 당사국의 공식 입장과 후속 논의에 달려 있다."),
        ("AI가 반도체 쓸어가자 자동차업계", "AI 데이터센터 수요가 반도체 공급을 빠르게 흡수하면서 자동차 업계의 부품 수급 부담이 커질 수 있다는 전망이다.\n반도체 확보 경쟁이 이어질 경우 차량 생산비와 소비자 가격에도 영향을 줄 수 있다.\n내년 공급 상황과 가격 변동이 업계의 주요 변수로 꼽힌다."),
    ]
    for needle, summary in summaries:
        if needle in title:
            return summary
    return ""

def enrich_descriptions(rows):
    # Fetch missing summaries concurrently so the daily update does not stall.
    with ThreadPoolExecutor(max_workers=8) as pool:
        pending = {
            pool.submit(fetch_naver_description, row.get("naverLink") or row.get("link", "")): row
            for row in rows
            if len((row.get("description") or "").strip()) < 70
        }
        for future in as_completed(pending):
            row = pending[future]
            try:
                summary = future.result()
            except Exception:
                summary = ""
            if len(summary.strip()) > len((row.get("description") or "").strip()):
                row["description"] = summary.strip()
    return rows

class NaverRankingParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.capture = None
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if tag == "a" and ("list_title" in classes or "rankingnews_link" in classes):
            href = attrs.get("href", "")
            if href:
                self.capture = {"link": urljoin("https://news.naver.com", href), "title": ""}
    def handle_data(self, data):
        if self.capture is not None:
            self.capture["title"] += data
    def handle_endtag(self, tag):
        if tag == "a" and self.capture is not None:
            row = {**self.capture, "title": clean_text(self.capture["title"])}
            if row["title"] and row["link"].startswith("https://"):
                self.rows.append(row)
            self.capture = None

def fetch_naver_popular(target_date):
    url = "https://news.naver.com/main/ranking/popularDay.naver"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Referer": "https://news.naver.com/",
    })
    with urllib.request.urlopen(req, timeout=10) as response:
        payload = response.read(4 * 1024 * 1024 + 1)
    if len(payload) > 4 * 1024 * 1024:
        raise ValueError("Naver ranking page too large")
    parser = NaverRankingParser()
    parser.feed(payload.decode("utf-8", errors="replace"))
    rows, seen = [], set()
    for row in parser.rows:
        key = re.sub(r"\W+", "", row["title"].lower())
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "title": row["title"],
            "link": row["link"],
            "pubDate": datetime.now(KST).isoformat(),
            "author": "네이버 랭킹",
            "description": "네이버 뉴스의 언론사별 많이 본 뉴스에서 수집한 인기 기사임. 순위와 노출은 조회 시점에 따라 달라질 수 있음.",
            "category": "네이버 인기",
            "rank": len(rows) + 1,
        })
        if len(rows) >= 20:
            break
    return rows




def fetch_naver_search_rss(query, category, target_date):
    # Keep only Naver-hosted articles; the RSS link is the article destination.
    params = urlencode({"where": "rss", "query": query, "field": 0, "sort": 1})
    url = "https://newssearch.naver.com/search.naver?" + params
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; TheDailyNews/1.0)",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    })
    with urllib.request.urlopen(req, timeout=12) as response:
        payload = response.read(3 * 1024 * 1024 + 1)
    if len(payload) > 3 * 1024 * 1024:
        raise ValueError("Naver News RSS too large")
    root = ET.fromstring(payload)
    rows = []
    for node in root.iter():
        if local_name(node.tag) != "item":
            continue
        title = clean_text(child_text(node, {"title"}))
        link = child_text(node, {"link"})
        dt = parse_date(child_text(node, {"pubdate"}))
        if not title or not link or not dt or dt.date().isoformat() != target_date:
            continue
        if not re.match(r"^https?://(?:n\.)?news\.naver\.com/(?:mnews/)?article/", link):
            continue
        description = clean_text(child_text(node, {"description"}))
        rows.append({
            "title": title,
            "link": link.split("?")[0],
            "naverLink": link.split("?")[0],
            "pubDate": dt.isoformat(),
            "author": "네이버뉴스",
            "description": description[:700],
            "category": category,
        })
    return rows

class NaverSectionParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.current = None
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        href = attrs.get("href", "")
        if tag == "a" and re.search(r"(?:https?:)?//(?:n\.)?news\.naver\.com/(?:mnews/)?article/|/(?:mnews/)?article/", href):
            link = urljoin("https://n.news.naver.com", href).split("?")[0]
            self.current = {"link": link, "title": ""}
    def handle_data(self, data):
        if self.current is not None:
            self.current["title"] += data
    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            title = clean_text(self.current["title"])
            link = self.current["link"]
            if title and re.match(r"^https://(?:n\.)?news\.naver\.com/(?:mnews/)?article/\d+/\d+", link) and title not in ("기사보기", "본문 바로가기"):
                self.rows.append({"title": title, "link": link})
            self.current = None

def fetch_naver_section(section_id, target_date):
    url = "https://news.naver.com/section/" + str(section_id)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Referer": "https://news.naver.com/",
    })
    with urllib.request.urlopen(req, timeout=12) as response:
        payload = response.read(5 * 1024 * 1024 + 1)
    if len(payload) > 5 * 1024 * 1024:
        raise ValueError("Naver section page too large")
    parser = NaverSectionParser()
    parser.feed(payload.decode("utf-8", errors="replace"))
    rows, seen = [], set()
    for item in parser.rows:
        key = item["link"].split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "title": item["title"],
            "link": item["link"],
            "naverLink": item["link"],
            "pubDate": datetime.now(KST).isoformat(),
            "author": "네이버뉴스",
            "description": "",
        })
    if not rows:
        raise ValueError("No direct Naver article links found in section " + str(section_id))
    return rows

class NaverSearchParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.current = None
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        href = attrs.get("href", "")
        if tag == "a" and ("news.naver.com" in href or "n.news.naver.com" in href):
            self.current = {"link": href, "title": ""}
    def handle_data(self, data):
        if self.current is not None:
            self.current["title"] += data
    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            row = {"link": self.current["link"].split("?")[0], "title": clean_text(self.current["title"])}
            if row["title"] and re.search(r"(?:^|//)(?:n\.)?news\.naver\.com/(?:mnews/)?(?:article|read)/", row["link"]):
                self.rows.append(row)
            self.current = None

def resolve_naver_article(title):
    query = urlencode({"where": "news", "query": title})
    url = "https://search.naver.com/search.naver?" + query
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36",
        "Referer": "https://www.naver.com/",
    })
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            payload = response.read(2 * 1024 * 1024 + 1)
        if len(payload) > 2 * 1024 * 1024:
            return ""
        parser = NaverSearchParser()
        parser.feed(payload.decode("utf-8", errors="replace"))
        target = re.sub(r"[^가-힣a-z0-9]", "", title.lower())
        best_link, best_ratio = "", 0.0
        for row in parser.rows:
            candidate = re.sub(r"[^가-힣a-z0-9]", "", row["title"].lower())
            ratio = difflib.SequenceMatcher(None, target, candidate).ratio()
            if ratio > best_ratio:
                best_link, best_ratio = row["link"], ratio
        return best_link if best_ratio >= 0.78 else ""
    except Exception:
        return ""

def attach_naver_links(categories):
    # Resolve distinct publisher headlines concurrently; only keep verified Naver URLs.
    cache = {}
    titles = set()
    for rows in categories.values():
        for row in rows:
            original = row.get("link", "")
            if "news.naver.com/" in original or "n.news.naver.com/" in original:
                row["naverLink"] = original
            elif row.get("title"):
                titles.add(row["title"])
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {title: pool.submit(resolve_naver_article, title) for title in titles}
        for title, future in futures.items():
            try:
                cache[title] = future.result()
            except Exception:
                cache[title] = ""
    for rows in categories.values():
        for row in rows:
            if not row.get("naverLink"):
                row["naverLink"] = cache.get(row.get("title", ""), "")
    return categories

def title_similarity(a, b):
    norm = lambda s: re.sub(r"[^가-힣a-z0-9]", "", (s or "").lower())
    aa, bb = norm(a), norm(b)
    if not aa or not bb:
        return 0.0
    if aa == bb:
        return 1.0
    return difflib.SequenceMatcher(None, aa, bb).ratio()

def deduplicate_across_categories(categories):
    candidates = [row for rows in categories.values() for row in rows]
    clusters = []
    for row in candidates:
        link = row.get("link", "").split("?", 1)[0].rstrip("/")
        match = None
        for cluster in clusters:
            first = cluster[0]
            first_link = first.get("link", "").split("?", 1)[0].rstrip("/")
            if (link and link == first_link) or title_similarity(row.get("title"), first.get("title")) >= 0.86:
                match = cluster
                break
        if match is None:
            clusters.append([row])
        else:
            match.append(row)
    result = {name: [] for name in FEEDS}
    for cluster in clusters:
        best = None
        best_score = -10**9
        for row in cluster:
            suggested = row.get("category", "사회")
            score = article_score(suggested, row)
            # Prefer the category whose editorial signals best fit this article.
            for cat in FEEDS:
                score_cat = article_score(cat, row)
                if score_cat > score:
                    suggested, score = cat, score_cat
            if score > best_score:
                best, best_score = dict(row), score
                best["category"] = suggested
        if best is not None:
            result[best["category"]].append(best)
    return result

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
    # Keep a deliberately prepared next-day homepage date when it is ahead
    # of the runner's clock (for example, before the scheduled 10:00 KST refresh).
    now = datetime.now(KST)
    today = now.date()
    previous_data = {}
    try:
        with open("news.json", "r", encoding="utf-8") as saved:
            previous_data = json.load(saved)
        saved_date = datetime.fromisoformat(previous_data.get("date", "")).date()
        if saved_date > today:
            today = saved_date
    except Exception:
        pass
    yesterday = today.fromordinal(today.toordinal() - 1)
    targets = [
        ("today", today),
        ("yesterday", yesterday),
    ]
    weekday_ko = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    all_days = {}
    diagnostics = {}

    event_pattern = re.compile(r"사고|화재|붕괴|추락|충돌|추돌|침수|폭발|실종|구조|재난|참사|사망|부상|교통사고|범죄|폭행|절도|사기|스토킹|살해|소방|응급|인명피해|학대|유괴|검거|구급차|골든타임|의료사고|응급실|의료공백|미배치")

    for day_key, day_value in targets:
        target_date = day_value.isoformat()
        categories = {name: [] for name in FEEDS}
        candidates = {name: [] for name in FEEDS}

        # Keep the deliberately prepared next-day placeholder until its 10:00 KST update.
        if day_key == "today" and day_value > now.date() and now.hour < 10:
            prepared = (previous_data.get("days") or {}).get("today")
            if prepared and prepared.get("date") == target_date:
                all_days[day_key] = prepared
                continue

        if day_key == "today":
            # Today's live section pages provide the freshest headlines.
            section_map = {
                "사회": 102,
                "세계": 104,
                "IT 과학": 105,
            }
            for category, section_id in section_map.items():
                try:
                    rows = fetch_naver_section(section_id, target_date)
                    for row in rows:
                        row["category"] = category
                    candidates[category].extend(rows)
                    diagnostics[f"{target_date}:네이버섹션:{category}"] = {"ok": True, "items": len(rows)}
                except Exception as exc:
                    diagnostics[f"{target_date}:네이버섹션:{category}"] = {"ok": False, "error": str(exc)[:180]}

            # Naver section 103 combines lifestyle and culture; split by editorial topic.
            culture_pattern = re.compile(r"전시|공연|콘서트|영화|드라마|배우|가수|앨범|음악|미술|박물관|미술관|축제|출판|책|작가|문학|문화재|유네스코|한지|공예|연극|뮤지컬|예술|문화|K-?pop|아이돌|웹툰|만화|방송|예능|무대|전통|콘텐츠|OTT|넷플릭스|애니메이션|팬덤|음원|음반|시사회|촬영|아티스트|패션|한복|전통의상|전통문화|디자인|대중문화|게임|국악|무용|발레|서예|사진|영화제|문화행사|박람회|작품")
            try:
                combined_rows = fetch_naver_section(103, target_date)
                for row in combined_rows:
                    category = "문화" if culture_pattern.search(row.get("title", "")) else "생활"
                    row["category"] = category
                    candidates[category].append(row)
                diagnostics[f"{target_date}:네이버섹션:생활문화"] = {"ok": True, "items": len(combined_rows)}
            except Exception as exc:
                diagnostics[f"{target_date}:네이버섹션:생활문화"] = {"ok": False, "error": str(exc)[:180]}

            # Supplement either side of the combined section with Naver RSS results.
            today_queries = {
                "생활": ["생활 건강 날씨 소비자 물가 교통", "주거 임금 복지 음식 여행 생활"],
                "문화": ["문화 예술 전시 공연 영화 음악", "책 출판 박물관 축제 문화재 한지"],
            }
            supplement_jobs = []
            with ThreadPoolExecutor(max_workers=8) as pool:
                for category, queries in today_queries.items():
                    if len(candidates[category]) < 10:
                        for query in queries:
                            supplement_jobs.append((category, query, pool.submit(fetch_naver_search_rss, query, category, target_date)))
                for category, query, future in supplement_jobs:
                    try:
                        rows = future.result()
                        candidates[category].extend(rows)
                        diagnostics[f"{target_date}:네이버RSS:{category}:{query}"] = {"ok": True, "items": len(rows)}
                    except Exception as exc:
                        diagnostics[f"{target_date}:네이버RSS:{category}:{query}"] = {"ok": False, "error": str(exc)[:180]}

            # Split accident/crime stories out of the Society section without duplication.
            society_rows = candidates["사회"]
            candidates["사건 사고"] = [row for row in society_rows if event_pattern.search(row.get("title", ""))]
            candidates["사회"] = [row for row in society_rows if not event_pattern.search(row.get("title", ""))]
            try:
                popular = fetch_naver_popular(target_date)
                diagnostics[f"{target_date}:네이버랭킹"] = {"ok": True, "items": len(popular)}
            except Exception as exc:
                popular = []
                diagnostics[f"{target_date}:네이버랭킹"] = {"ok": False, "error": str(exc)[:180]}
        else:
            if day_value == now.date():
                # The 11/7 archive is still today's live Naver page at the moment of collection.
                section_map = {"사회": 102, "세계": 104, "IT 과학": 105}
                for category, section_id in section_map.items():
                    try:
                        rows = fetch_naver_section(section_id, target_date)
                        for row in rows:
                            row["category"] = category
                        candidates[category].extend(rows)
                        diagnostics[f"\${target_date}:네이버섹션:{category}"] = {"ok": True, "items": len(rows)}
                    except Exception as exc:
                        diagnostics[f"\${target_date}:네이버섹션:{category}"] = {"ok": False, "error": str(exc)[:180]}
                culture_pattern = re.compile(r"전시|공연|콘서트|영화|드라마|배우|가수|앨범|음악|미술|박물관|미술관|축제|출판|책|작가|문학|문화재|유네스코|한지|공예|연극|뮤지컬|예술|문화|K-?pop|아이돌|웹툰|만화|방송|예능|무대|전통|콘텐츠|OTT|넷플릭스|애니메이션|팬덤|음원|음반|시사회|촬영|아티스트|패션")
                try:
                    combined_rows = fetch_naver_section(103, target_date)
                    for row in combined_rows:
                        category = "문화" if culture_pattern.search(row.get("title", "")) else "생활"
                        row["category"] = category
                        candidates[category].append(row)
                    diagnostics[f"\${target_date}:네이버섹션:생활문화"] = {"ok": True, "items": len(combined_rows)}
                except Exception as exc:
                    diagnostics[f"\${target_date}:네이버섹션:생활문화"] = {"ok": False, "error": str(exc)[:180]}
                society_rows = candidates["사회"]
                candidates["사건 사고"] = [row for row in society_rows if event_pattern.search(row.get("title", ""))]
                candidates["사회"] = [row for row in society_rows if not event_pattern.search(row.get("title", ""))]
                popular = []
            else:
                # Naver's live section pages only show current stories. Query News RSS
                # and retain only items actually published on the target date.
                yesterday_queries = {
                    "사건 사고": ["사고 화재 구조 수사 사건", "범죄 경찰 사건 사고"],
                    "사회": ["사회 정책 교육 노동 복지", "국회 정부 주민 노동"],
                    "생활": ["생활 건강 날씨 소비자 물가 교통", "주거 임금 복지 음식 여행 생활"],
                    "문화": ["문화 예술 전시 공연 영화 음악", "책 출판 박물관 축제 문화재 한지"],
                    "세계": ["국제 세계 외교 경제 기후", "미국 중국 일본 유럽 국제"],
                    "IT 과학": ["IT 과학 AI 인공지능 반도체", "기술 로봇 우주 과학"],
                }
                jobs = []
                with ThreadPoolExecutor(max_workers=10) as pool:
                    for category, queries in yesterday_queries.items():
                        for query in queries:
                            jobs.append((category, query, pool.submit(fetch_naver_search_rss, query, category, target_date)))
                    for category, query, future in jobs:
                        try:
                            rows = future.result()
                            for row in rows:
                                row["category"] = category
                            candidates[category].extend(rows)
                            diagnostics[f"{target_date}:네이버RSS:{category}:{query}"] = {"ok": True, "items": len(rows)}
                        except Exception as exc:
                            diagnostics[f"{target_date}:네이버RSS:{category}:{query}"] = {"ok": False, "error": str(exc)[:180]}
                # If Naver RSS is unavailable or returns too few dated results, use
                # established publisher RSS feeds for the same date, then resolve each
                # headline to a verified direct Naver News article URL.
                fallback_jobs = []
                with ThreadPoolExecutor(max_workers=12) as pool:
                    for category, feeds in FEEDS.items():
                        if len(candidates[category]) >= 10:
                            continue
                        for source, feed_url in feeds:
                            fallback_jobs.append((category, source, feed_url,
                                pool.submit(fetch_feed, source, feed_url, target_date)))
                    for category, source, feed_url, future in fallback_jobs:
                        try:
                            rows = future.result()
                            for row in rows:
                                row["category"] = category
                            candidates[category].extend(rows)
                            diagnostics[f"{target_date}:보조RSS:{category}:{source}"] = {"ok": True, "items": len(rows)}
                        except Exception as exc:
                            diagnostics[f"{target_date}:보조RSS:{category}:{source}"] = {"ok": False, "error": str(exc)[:180]}
    
                # Never publish a publisher URL as the requested Naver original link.
                # Resolve only a bounded set of dated candidates to avoid excessive requests.
                for category in candidates:
                    candidates[category].sort(key=lambda row: row.get("pubDate", ""), reverse=True)
                    candidates[category] = candidates[category][:18]
                attach_naver_links(candidates)
                for category in candidates:
                    verified = []
                    for row in candidates[category]:
                        naver_link = row.get("naverLink", "")
                        if naver_link.startswith("https://") and "news.naver.com/" in naver_link:
                            row["link"] = naver_link
                            verified.append(row)
                    candidates[category] = verified
                popular = []

        # Drop malformed video placeholder titles before selecting the 10 stories.
        for name in candidates:
            candidates[name] = [
                row for row in candidates[name]
                if clean_text(row.get("title", "")) != "동영상뉴스"
                and not re.fullmatch(r"동영상뉴스 재생시간\s*\d{1,2}:\d{2}", clean_text(row.get("title", "")))
                and not re.search(r"흉기|칼 들고|전차|탱크|무기|총기|미사일|폭탄|기뢰|핵잠|청해부대|공습|군함|잠수함|전투기|군사|전쟁|음란물|성착취|성폭력|성범죄|포르노|성인물|감량|살 쫙|폭식|탄력 고민|운동 효과|야식 습관|다이어트|체중 감량|몸매", clean_text(row.get("title", "")))
            ]
        categories = deduplicate_across_categories(candidates)
        # Every story here already has a verified direct Naver News URL.
        for name in categories:
            categories[name] = choose_diverse(categories[name], 10)

        # Fetch a real article lead when the RSS snippet merely repeats the headline.
        needs_summary = []
        for name, rows in categories.items():
            for row in rows:
                safe_summary = safe_news_summary(row)
                if safe_summary:
                    row["description"] = safe_summary
                    continue
                desc = clean_text(row.get("description", ""))
                if (len(desc) < 70 or "기사에서 관련 배경과 주요 내용을 확인할 수 있습니다" in desc
                        or title_similarity(desc, row.get("title", "")) > 0.78):
                    url = row.get("naverLink") or row.get("link", "")
                    if url.startswith("https://") and "news.naver.com/" in url:
                        needs_summary.append((row, url))
        with ThreadPoolExecutor(max_workers=12) as pool:
            summary_jobs = [(row, pool.submit(fetch_naver_description, url)) for row, url in needs_summary]
            for row, future in summary_jobs:
                try:
                    fetched = clean_text(future.result())
                    if len(fetched) >= 70 and title_similarity(fetched, row.get("title", "")) < 0.78:
                        row["description"] = fetched[:1200]
                except Exception:
                    pass

        for name in categories:
            for row in categories[name]:
                desc = clean_text(row.get("description") or "")
                if (len(desc) < 70 or "기사에서 관련 배경과 주요 내용을 확인할 수 있습니다" in desc
                        or title_similarity(desc, row.get("title", "")) > 0.78):
                    row["description"] = fallback_summary(row) or (
                        desc or f"{clean_text(row.get('title', '이 기사'))}. 기사에서 관련 배경과 주요 내용을 확인할 수 있습니다."
                    )

        total = sum(len(rows) for rows in categories.values())
        all_days[day_key] = {
            "date": target_date,
            "weekday": weekday_ko[day_value.weekday()],
            "label": ("오늘" if day_key == "today" else "어제") + " · " + f"{day_value.month}월 {day_value.day}일 " + weekday_ko[day_value.weekday()],
            "total": total,
            "categories": categories,
            "popular": popular,
        }

    # Preserve the already published archive for the immediately previous day.
    # A short or temporarily unavailable RSS response must not erase that archive.
    saved_yesterday = (previous_data.get("days") or {}).get("yesterday")
    # Do not keep the old combined "생활 문화" schema after the editorial split.
    saved_categories = (saved_yesterday or {}).get("categories", {})
    new_yesterday = all_days.get("yesterday", {})
    if saved_yesterday and saved_yesterday.get("date") == yesterday.isoformat() and "생활 문화" not in saved_categories:
        restricted_title = re.compile(r"흉기|칼 들고|전차|탱크|무기|총기|미사일|폭탄|기뢰|핵잠|청해부대|공습|군함|잠수함|전투기|군사|전쟁|음란물|성착취|성폭력|성범죄|포르노|성인물|감량|살 쫙|폭식|탄력 고민|운동 효과|야식 습관|다이어트|체중 감량|몸매")
        merged = {}
        for category in FEEDS:
            pool, seen = [], set()
            for row in (new_yesterday.get("categories", {}).get(category, []) + saved_categories.get(category, [])):
                if restricted_title.search(clean_text(row.get("title", ""))):
                    continue
                key = (row.get("naverLink") or row.get("link") or "").split("?", 1)[0]
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                copy = dict(row)
                copy["category"] = category
                pool.append(copy)
            merged[category] = choose_diverse(pool, 10)
        merged_total = sum(len(rows) for rows in merged.values())
        if merged_total >= new_yesterday.get("total", 0):
            new_yesterday["categories"] = merged
            new_yesterday["total"] = merged_total
            all_days["yesterday"] = new_yesterday

    total = sum(day["total"] for day in all_days.values())
    result = {
        "date": today.isoformat(),
        "generatedAt": datetime.now(KST).isoformat(),
        "total": total,
        "days": all_days,
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
    print("NEWS_DATE=" + today.isoformat())
    print("NEWS_TOTAL=" + str(total))
    for day_key, day in all_days.items():
        print(f"DAY {day_key} {day['label']} TOTAL={day['total']}")
        for name, rows in day["categories"].items():
            print(f"CATEGORY {day_key} {name}={len(rows)}")
    for name, status in diagnostics.items():
        print("FEED " + name + "=" + json.dumps(status, ensure_ascii=False))
    if not any(item["ok"] for item in diagnostics.values()):
        raise SystemExit("All Naver section and ranking feeds failed; refusing to publish an empty feed.")

if __name__ == "__main__":
    main()
