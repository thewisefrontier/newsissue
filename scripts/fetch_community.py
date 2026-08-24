"""
scripts/fetch_community.py
----------------------------
"뉴스 앤 이슈" 채널에 각종 커뮤니티의 베스트 게시글을 실시간 푸시한다(2026-08-25
사용자 요청 — "속보처럼 텔레그램에 바로 올리는 방식으로").

fetch_news.py(뉴스 RSS)와는 소스 종류·판정 방식이 완전히 달라 별도 스크립트로
분리했다. 다만 사고방식은 같다: 게시판이 이미 "베스트"로 골라준 목록을 그대로
따라가며, 이미 보낸 글(URL 기준)만 걸러내고 새 글은 즉시 보낸다 — 뉴스의 [속보]와
동일하게 "지금 막 올라온 걸 바로 알린다"가 목적이라 순위·점수 집계는 하지 않는다.

**소스 선정 기준(2026-08-24/25 조사)**: 무차별로 아무 커뮤니티나 넣지 않았다.
1) robots.txt의 `User-agent: *` 규칙(우리 스크립트에 실제로 적용되는 규칙)이
   목록 페이지를 막지 않을 것. `ClaudeBot`/`anthropic-ai`/`Claude-Web` 같은
   개별 봇 이름 차단은 그 이름으로 자기소개하는 크롤러에만 적용되는 규칙이라
   우리 스크립트(정직한 커스텀 UA, Gemini도 같이 쓰는 범용 수집기)에는 해당되지
   않는다 — `User-agent: *` 규칙만 우리에게 실제로 적용된다.
2) 로그인·JS 렌더·Cloudflare 봇 챌린지가 없을 것(인스티즈는 robots.txt 요청부터
   Cloudflare JS 챌린지 페이지가 와서 제외 — 우회하지 않는다).
3) 게시판 자체가 "베스트/개념글/HOT"으로 이미 걸러준 목록일 것 — 그래야 우리가
   별도 스코어링 없이 "새 글 = 베스트 새 글"로 바로 취급할 수 있다. 클리앙
   모두의공원·인벤 오픈이슈·뽐뿌 자유게시판처럼 전체 최신글 목록만 있고 베스트
   필터가 없는 곳은 이번 1차 범위에서 뺐다(자체 스코어링이 필요해 범위가 커짐 —
   추후 검토).
4) 목록 페이지만 요청한다 — 개별 글 페이지는 절대 크롤링하지 않는다(링크로만
   노출). 요약도 만들지 않는다 — 커뮤니티 글은 뉴스와 달리 본문 검증이 중요하지
   않고, 크롤링 범위를 목록에만 묶어두는 게 예의에도 맞다.

**1차 소스 4곳**(전부 위 기준 확인 완료):
- 루리웹 베스트(`/best/all`) — robots.txt에 걸리는 쿼리 패턴(`orderby=` 등) 없이
  기본 URL 그대로 실시간 베스트가 나온다.
- 더쿠 HOT(`/hot`) — robots.txt가 404(규칙 없음), 목록 페이지 정상 응답.
- 디시인사이드 주식갤(`neostock`)·부동산갤(`immovables`) 개념글 — robots.txt의
  갤러리별 차단 14곳에 안 들어있다(부동산갤은 옛날 개별 글 2건만 차단, 목록은
  허용). `exception_mode=recommend`로 개념글(추천 컷 통과글)만 가져온다.

제외한 곳: 클리앙·인벤·뽐뿌(베스트 필터 없음, 추후 검토), 웃긴대학(robots.txt가
`User-agent: *`에도 `/board/best/`를 명시적으로 막음 — 봇 이름과 무관하게 모두에게
적용되는 규칙), 인스티즈(Cloudflare 봇 챌린지, 우회 안 함), DC인사이드의 다른
갤러리들(로봇 배제 목록에 있거나 이번엔 검토 안 함).

**첫 실행 부트스트랩**: 상태 파일이 비어 있는 첫 실행에 그 시점 베스트 목록
전체를 "새 글"로 보면 한 번에 수십 건이 쏟아진다. 그래서 소스별로 이번이 첫
수집이면(상태 파일에 해당 소스 기록이 전무하면) 발송하지 않고 현재 목록을
"이미 본 것"으로만 기록한다 — 다음 실행부터 진짜 신규 글만 실시간으로 나간다.

실행: python scripts/fetch_community.py
"""

import os
import re
import sys
import json
import html
import time
import hashlib
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(timezone.utc).astimezone(KST)


TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")

CHANNEL_TAG = "뉴스앤이슈"
CHANNEL_URL = "https://t.me/news_issue"

# fetch_news.py의 STATE_FILE(data/state.json)과 완전히 분리한다 — 카테고리별
# 창(DEDUP_WINDOW_HOURS_BY_CATEGORY) 등 뉴스 전용 로직과 뒤섞이면 서로 건드리기
# 어려워진다. 커뮤니티 쪽은 URL 완전일치 하나로만 중복을 판정하므로 구조가 훨씬
# 단순해 별도 상태 파일이 자연스럽다.
STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "community_state.json")

# 정직한 커스텀 UA — 브라우저를 사칭하지 않는다. 이름에 봇 목적과 저장소를 밝혀서
# 사이트 운영자가 트래픽 출처를 바로 알 수 있게 한다.
USER_AGENT = "newsissue-community-bot/1.0 (+https://github.com/thewisefrontier/newsissue)"

REQUEST_TIMEOUT_SEC = 10
# 소스 사이 예의상 지연 — 순차 요청, 동시 요청 안 함.
SOURCE_DELAY_SEC = 1.5
SEND_INTERVAL_SEC = 1.5
TELEGRAM_MAX_RETRIES = 3

# 상태 파일이 무한정 커지지 않도록 오래된 guid는 정리한다. 커뮤니티 베스트글은
# 뉴스보다 훨씬 빨리 순환하므로(대부분 하루 안에 목록에서 밀려남) 3일이면
# 재수집 방지 목적으로 충분하다.
RETENTION_HOURS = 72


def _strip_tags(fragment: str) -> str:
    """태그 제거 + 엔티티 해제 + 공백 정규화. fetch_news.py의 crawl_article_text와
    같은 패턴(정규식 HTML 처리, 외부 파서 의존성 없음)을 커뮤니티 파서에도 맞춘다."""
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _to_int(text: str) -> int | None:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


# =========================
# 소스별 파서
# =========================
# 각 파서는 (title, url, views, comments) 튜플의 리스트를 반환한다. 얻을 수 없는
# 지표는 None — 임의로 만들어내지 않는다(추정값을 넣으면 나중에 진짜 값과
# 헷갈린다).

_RULIWEB_ROW_RE = re.compile(
    r'<tr class="table_body[^"]*">(?P<row>.*?)</tr>', re.DOTALL)
_RULIWEB_LINK_RE = re.compile(
    r'<a class="subject_link[^"]*"\s+href="(?P<href>/best/board/[^"]+)">(?P<inner>.*?)</a>', re.DOTALL)
# 상위 4위(`best_top_row`)는 <strong class="text_over">, 5위 이하(`mode_list`)는
# <span class="text_over">로 마크업이 다르다 — 실측으로 확인.
_RULIWEB_TITLE_RE = re.compile(r'<(?:strong|span) class="text_over">(?P<title>.*?)</(?:strong|span)>', re.DOTALL)
_RULIWEB_HIT_RE = re.compile(r'<td class="hit">\s*([\d,]+)\s*</td>')
_RULIWEB_REPLY_RE = re.compile(r'\((\d+)\)')


def parse_ruliweb(html_text: str) -> list:
    """루리웹 베스트. `table_body` 클래스는 사이드바의 다른 위젯(장터 핫딜 등)에도
    쓰이므로 `/best/board/` 링크만 받는다. 제목은 앵커 전체가 아니라 순위 배지
    숫자를 뺀 text_over 요소만 취한다 — 안 그러면 상위 4위는 "1 제목"처럼 순위
    숫자가 제목에 섞인다."""
    items = []
    for m in _RULIWEB_ROW_RE.finditer(html_text):
        row = m.group("row")
        link_m = _RULIWEB_LINK_RE.search(row)
        if not link_m:
            continue
        title_m = _RULIWEB_TITLE_RE.search(link_m.group("inner"))
        title = _strip_tags(title_m.group("title")) if title_m else ""
        reply_m = _RULIWEB_REPLY_RE.search(link_m.group("inner"))
        hit_m = _RULIWEB_HIT_RE.search(row)
        if not title:
            continue
        url = "https://bbs.ruliweb.com" + link_m.group("href")
        items.append((
            title,
            url,
            _to_int(hit_m.group(1)) if hit_m else None,
            int(reply_m.group(1)) if reply_m else None,
        ))
    return items


_THEQOO_ROW_RE = re.compile(r'<tr>(?P<row>(?:(?!</tr>).)*?)</tr>', re.DOTALL)
_THEQOO_TITLE_RE = re.compile(
    r'<td class="title">\s*<a href="(?P<href>/hot/\d+)"[^>]*>(?P<inner>.*?)</a>', re.DOTALL)
_THEQOO_REPLY_RE = re.compile(r'class="replyNum"[^>]*>(\d+)</a>')
_THEQOO_VIEWS_RE = re.compile(r'<td class="m_no">([\d,]+)</td>')


def parse_theqoo(html_text: str) -> list:
    """더쿠 HOT. 공지 행은 `<tr class="notice ...">`라 태그 없는 `<tr>`만 매칭하는
    이 정규식에서 자연히 제외된다."""
    items = []
    for m in _THEQOO_ROW_RE.finditer(html_text):
        row = m.group("row")
        title_m = _THEQOO_TITLE_RE.search(row)
        if not title_m:
            continue
        title = _strip_tags(title_m.group("inner"))
        if not title:
            continue
        reply_m = _THEQOO_REPLY_RE.search(row)
        views_m = _THEQOO_VIEWS_RE.search(row)
        url = "https://theqoo.net" + title_m.group("href")
        items.append((
            title,
            url,
            _to_int(views_m.group(1)) if views_m else None,
            int(reply_m.group(1)) if reply_m else None,
        ))
    return items


_DC_ROW_RE = re.compile(
    r'<tr class="ub-content us-post"[^>]*data-no="(?P<no>\d+)"[^>]*>(?P<row>.*?)</tr>', re.DOTALL)
_DC_TITLE_CELL_RE = re.compile(r'<td class="gall_tit[^"]*">(?P<cell>.*?)</td>', re.DOTALL)
_DC_LINK_RE = re.compile(r'<a\s+href="(?P<href>/board/view/\?[^"]+)"[^>]*>(?P<inner>.*?)</a>', re.DOTALL)
_DC_REPLY_RE = re.compile(r'class="reply_num">\[?(\d+)\]?<')
_DC_COUNT_RE = re.compile(r'<td class="gall_count">([\d,]+|-)</td>')


def parse_dcinside(html_text: str) -> list:
    """디시인사이드 갤러리 개념글(exception_mode=recommend). 공지·광고 행은
    `data-no` 속성이 없어(광고는 `<tr class="ub-content ">`만, 공지는 다른 구조)
    `data-no="\\d+"`를 요구하는 이 정규식에서 자연히 제외된다."""
    items = []
    for m in _DC_ROW_RE.finditer(html_text):
        row = m.group("row")
        cell_m = _DC_TITLE_CELL_RE.search(row)
        if not cell_m:
            continue
        cell = cell_m.group("cell")
        link_m = _DC_LINK_RE.search(cell)
        if not link_m:
            continue
        title = _strip_tags(link_m.group("inner"))
        if not title:
            continue
        reply_m = _DC_REPLY_RE.search(cell)
        count_m = _DC_COUNT_RE.search(row)
        url = "https://gall.dcinside.com" + html.unescape(link_m.group("href"))
        items.append((
            title,
            url,
            _to_int(count_m.group(1)) if count_m else None,
            int(reply_m.group(1)) if reply_m else None,
        ))
    return items


SOURCES = [
    {
        "id": "ruliweb",
        "name": "루리웹 베스트",
        "emoji": "🔵",
        "url": "https://bbs.ruliweb.com/best/all",
        "parse": parse_ruliweb,
    },
    {
        "id": "theqoo",
        "name": "더쿠 HOT",
        "emoji": "🟣",
        "url": "https://theqoo.net/hot",
        "parse": parse_theqoo,
    },
    {
        "id": "dc_neostock",
        "name": "디시 주식갤 개념글",
        "emoji": "🟢",
        "url": "https://gall.dcinside.com/board/lists/?id=neostock&exception_mode=recommend",
        "parse": parse_dcinside,
    },
    {
        "id": "dc_immovables",
        "name": "디시 부동산갤 개념글",
        "emoji": "🟢",
        "url": "https://gall.dcinside.com/board/lists/?id=immovables&exception_mode=recommend",
        "parse": parse_dcinside,
    },
]


def fetch_source(source: dict) -> list:
    """목록 페이지 하나만 요청한다 — 개별 글은 절대 요청하지 않는다."""
    try:
        res = requests.get(
            source["url"],
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SEC,
        )
        if res.status_code != 200:
            print(f"  ⚠️ {source['name']} HTTP {res.status_code}")
            return []
        return source["parse"](res.text)
    except Exception as e:
        print(f"  ⚠️ {source['name']} 수집 실패: {e}")
        return []


# =========================
# STATE
# =========================

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"sent": []}


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def prune_state(state: dict):
    cutoff = now_kst() - timedelta(hours=RETENTION_HOURS)
    state["sent"] = [s for s in state["sent"] if s["sent_at"] >= cutoff.isoformat()]


# =========================
# TELEGRAM
# =========================

def send_telegram(source: dict, title: str, url: str, views, comments) -> dict:
    title_safe = html.escape(title)
    title_linked = f'<a href="{url}">{title_safe}</a>'
    metrics = []
    if views is not None:
        metrics.append(f"👀 {views:,}")
    if comments is not None:
        metrics.append(f"💬 {comments:,}")
    metrics_block = f"\n\n{' · '.join(metrics)}" if metrics else ""
    footer_line = (
        f"\n\n📎 <a href=\"{url}\">원문</a> | "
        f"<a href=\"{CHANNEL_URL}\">{CHANNEL_TAG}</a>"
    )
    msg = f"{source['emoji']} [{source['name']}] {title_linked}{metrics_block}{footer_line}"
    data = {
        "chat_id": CHAT_ID,
        "text": msg,
        "parse_mode": "HTML",
        "link_preview_options": json.dumps({"is_disabled": True}),
    }
    # fetch_news.py의 send_telegram과 동일한 429 재시도 로직 — 텔레그램 플러드
    # 컨트롤이 API 응답과 실제 발송을 분리 처리하는 문제가 여기도 똑같이 적용될
    # 수 있어 처음부터 반영한다(뉴스 쪽 2026-08-23 실사고 참고).
    result = {}
    for attempt in range(TELEGRAM_MAX_RETRIES):
        res = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data=data,
            timeout=15,
        )
        result = res.json()
        if result.get("ok"):
            return result
        retry_after = (result.get("parameters") or {}).get("retry_after")
        if retry_after and attempt < TELEGRAM_MAX_RETRIES - 1:
            print(f"  ⏳ 429(재시도 {attempt+1}/{TELEGRAM_MAX_RETRIES}), {retry_after}초 대기: {title[:40]}")
            time.sleep(retry_after)
            continue
        return result
    return result


# =========================
# MAIN
# =========================

def run():
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("[ERROR] TELEGRAM_TOKEN 또는 CHAT_ID가 .env에 없습니다.")
        return

    state = load_state()
    prune_state(state)
    sent_guids = {s["guid"] for s in state["sent"]}
    # 소스별로 "이번이 첫 수집인가"를 판단하기 위해 이미 알고 있는 소스 id를 모은다.
    known_source_ids = {s.get("source") for s in state["sent"]}

    new_entries = []
    sent_count = 0

    for source in SOURCES:
        items = fetch_source(source)
        print(f"[{source['name']}] {len(items)}건 수집")

        is_bootstrap = source["id"] not in known_source_ids
        if is_bootstrap and items:
            print(f"  🌱 {source['name']} 첫 수집 — 발송 없이 기준선만 기록")

        for title, url, views, comments in items:
            guid = hashlib.md5(url.encode("utf-8")).hexdigest()
            if guid in sent_guids:
                continue

            if is_bootstrap:
                new_entries.append({"guid": guid, "source": source["id"], "sent_at": now_kst().isoformat()})
                sent_guids.add(guid)
                continue

            res = send_telegram(source, title, url, views, comments)
            if res.get("ok"):
                sent_count += 1
                new_entries.append({"guid": guid, "source": source["id"], "sent_at": now_kst().isoformat()})
                sent_guids.add(guid)
                print(f"  ✅ {title[:50]}")
            else:
                print(f"  ❌ 발송 실패: {res}")
            time.sleep(SEND_INTERVAL_SEC)

        time.sleep(SOURCE_DELAY_SEC)

    state["sent"].extend(new_entries)
    save_state(state)

    print(f"\n[완료] 발송 {sent_count}건 / 신규 기록 {len(new_entries)}건")


if __name__ == "__main__":
    run()
