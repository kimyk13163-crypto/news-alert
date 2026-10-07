"""
네이버 + 구글 뉴스 키워드 알림 봇
keywords.txt에 적힌 키워드(관심 회사)로 네이버 뉴스와 구글 뉴스를 검색해서, 새 기사가 있으면 슬랙으로 알려줍니다.
(별도 설치가 필요 없는 파이썬 기본 기능만 사용합니다)
"""
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
KEYWORDS_FILE = BASE_DIR / "keywords.txt"
STATE_FILE = BASE_DIR / "seen.json"

# ===== 설정 =====
# 네이버 뉴스(n.news.naver.com)에 올라온 기사만 받고 싶으면 True로 바꾸세요
ONLY_NAVER_NEWS = False
# 키워드 하나당 한 번에 확인할 최신 기사 수 (최대 100)
FETCH_COUNT = 30
# 기억해 둘 기사 링크 최대 개수 (중복 알림 방지용)
MAX_SEEN = 10000
# ================


def get_env(name, required=True):
    value = os.environ.get(name, "").strip()
    if not value and required:
        sys.exit(f"[오류] {name} 값이 없습니다. GitHub Secrets 설정을 확인하세요.")
    return value


def load_keywords():
    if not KEYWORDS_FILE.exists():
        sys.exit("[오류] keywords.txt 파일이 없습니다.")
    keywords = []
    for line in KEYWORDS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and line not in keywords:
            keywords.append(line)
    return keywords


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"keywords": [], "links": []}


def save_state(state):
    state["links"] = state["links"][-MAX_SEEN:]
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def search_news(keyword, client_id, client_secret):
    query = urllib.parse.quote(keyword)
    url = (
        "https://openapi.naver.com/v1/search/news.json"
        f"?query={query}&display={FETCH_COUNT}&sort=date"
    )
    req = urllib.request.Request(
        url,
        headers={
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
        },
    )
    with urllib.request.urlopen(req, timeout=15) as res:
        return json.loads(res.read().decode("utf-8")).get("items", [])


def search_google(keyword):
    # 구글 뉴스 RSS (API 키 불필요). 따옴표로 감싸 정확히 일치하는 기사만 검색
    query = urllib.parse.quote(f'"{keyword}"')
    url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as res:
        root = ET.fromstring(res.read())
    items = []
    for it in root.iter("item"):
        items.append({
            "title": it.findtext("title", ""),
            "link": it.findtext("link", ""),
            "pubDate": it.findtext("pubDate", ""),
            "source": "구글",
        })
    items.sort(key=lambda i: sort_key(i["pubDate"]), reverse=True)
    return items[:FETCH_COUNT]


def sort_key(pub_date):
    try:
        return parsedate_to_datetime(pub_date).timestamp()
