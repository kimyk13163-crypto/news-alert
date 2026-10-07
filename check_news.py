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
    except Exception:
        return 0


def title_key(title):
    # 같은 기사가 네이버/구글 양쪽에 나오면 한 번만 알리기 위한 키
    title = re.sub(r"\s+-\s+[^-]+$", "", clean_text(title))  # 구글 제목 끝 '- 언론사' 제거
    return "t:" + re.sub(r"[^0-9A-Za-z가-힣]", "", title)


def clean_text(text):
    text = re.sub(r"<[^>]+>", "", text)  # <b> 같은 태그 제거
    return html.unescape(text).strip()


def slack_escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def short_time(pub_date):
    try:
        return parsedate_to_datetime(pub_date).strftime("%m/%d %H:%M")
    except Exception:
        return ""


def format_message(keyword, articles):
    lines = [f":newspaper: *[{slack_escape(keyword)}]* 새 기사 {len(articles)}건"]
    for a in articles[:10]:
        title = slack_escape(clean_text(a["title"]))
        when = short_time(a.get("pubDate", ""))
        src = a.get("source", "")
        lines.append(f"• `{src}` <{a['link']}|{title}>  _{when}_")
    if len(articles) > 10:
        lines.append(f"…외 {len(articles) - 10}건")
    return "\n".join(lines)


def send_slack(webhook_url, text):
    data = json.dumps({"text": text, "unfurl_links": False}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url, data=data, headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(req, timeout=15)


def main():
    client_id = get_env("NAVER_CLIENT_ID", required=False)
    client_secret = get_env("NAVER_CLIENT_SECRET", required=False)
    use_naver = bool(client_id and client_secret)
    if not use_naver:
        print("[안내] 네이버 API 키가 없어 구글 뉴스만 확인합니다.")
    webhook = get_env("SLACK_WEBHOOK_URL")

    keywords = load_keywords()
    state = load_state()
    seen = set(state["links"])
    known_keywords = set(state["keywords"])

    started = []    # 이번에 새로 감시를 시작한 키워드
    succeeded = set()

    for keyword in keywords:
        items = []
        if use_naver:
            try:
                naver = search_news(keyword, client_id, client_secret)
                if ONLY_NAVER_NEWS:
                    naver = [i for i in naver if "n.news.naver.com" in i["link"]]
                for i in naver:
                    i["source"] = "네이버"
                items += naver
                succeeded.add(keyword)
            except Exception as e:
                print(f"[경고] 네이버 '{keyword}' 검색 실패: {e}")
        try:
            items += search_google(keyword)
            succeeded.add(keyword)
        except Exception as e:
            print(f"[경고] 구글 '{keyword}' 검색 실패: {e}")
        if keyword not in succeeded:
            continue

        fresh = []
        for i in items:
            tkey = title_key(i["title"])
            if i["link"] in seen or tkey in seen:
                continue
            fresh.append(i)
            for k in (i["link"], tkey):
                state["links"].append(k)
                seen.add(k)
        fresh.sort(key=lambda i: sort_key(i.get("pubDate", "")), reverse=True)

        if keyword not in known_keywords:
            # 처음 등록된 키워드는 기존 기사를 알림 없이 기억만 함 (알림 폭탄 방지)
            started.append(keyword)
        elif fresh:
            send_slack(webhook, format_message(keyword, fresh))
            print(f"'{keyword}': 새 기사 {len(fresh)}건 알림")
        else:
            print(f"'{keyword}': 새 기사 없음")

    state["keywords"] = [
        k for k in keywords if k in known_keywords or k in succeeded
    ]
    save_state(state)

    if started:
        send_slack(
            webhook,
            ":white_check_mark: 감시 시작: "
            + ", ".join(slack_escape(k) for k in started)
            + "\n지금부터 새로 올라오는 기사를 알려드릴게요.",
        )


if __name__ == "__main__":
    main()
