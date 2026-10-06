"""
네이버 뉴스 키워드 알림 봇
keywords.txt에 적힌 키워드로 네이버 뉴스를 검색해서, 새 기사가 있으면 슬랙으로 알려줍니다.
(별도 설치가 필요 없는 파이썬 기본 기능만 사용합니다)
"""
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
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
MAX_SEEN = 5000
# ================


def get_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
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
        lines.append(f"• <{a['link']}|{title}>  _{when}_")
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
    client_id = get_env("NAVER_CLIENT_ID")
    client_secret = get_env("NAVER_CLIENT_SECRET")
    webhook = get_env("SLACK_WEBHOOK_URL")

    keywords = load_keywords()
    state = load_state()
    seen = set(state["links"])
    known_keywords = set(state["keywords"])

    started = []    # 이번에 새로 감시를 시작한 키워드
    succeeded = set()

    for keyword in keywords:
        try:
            items = search_news(keyword, client_id, client_secret)
        except Exception as e:
            print(f"[경고] '{keyword}' 검색 실패: {e}")
            continue
        succeeded.add(keyword)

        if ONLY_NAVER_NEWS:
            items = [i for i in items if "n.news.naver.com" in i["link"]]

        fresh = [i for i in items if i["link"] not in seen]
        for i in reversed(fresh):
            state["links"].append(i["link"])
            seen.add(i["link"])

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
