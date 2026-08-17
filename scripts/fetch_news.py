"""
scripts/fetch_news.py
----------------------
"뉴스 앤 이슈" 채널용 속보/단독 뉴스 자동 수집·발송.

소스: 구글 뉴스 검색 RSS ("속보", "단독" 키워드) — 사실상 전체 한국 언론사를 커버.
필터: 제목에 실제 [속보]/[단독] 대괄호 태그가 붙은 기사만 통과시킨다.
      (구글 검색은 "단독선두" 같은 일반 단어도 섞어 주므로 태그 정규식으로 재검증 필요)
중복 제거: 같은 사건을 여러 매체가 거의 동시에 보도하는 경우가 많아,
      NewsFinal의 auto_dedup.py 구조(유사도 임계값 2단계)를 로컬로 이식했다.
      - 최근 발송 목록과 제목 유사도 DUP_SKIP_THRESHOLD 이상 → 완전 중복으로 보고 발송 생략
      - DUP_REVIEW_THRESHOLD~SKIP 미만 → 애매한 경우, 발송은 하되 콘솔/리뷰 로그에 남겨 나중에 임계값 조정 근거로 삼는다
      (Supabase/Gemini 통합 재작성 단계는 여기선 안 씀 — 우리는 자체 기사를 쓰는 게 아니라
       원문 링크를 그대로 전달하는 큐레이션이라 "통합"이 아니라 "생략"이 맞는 대응이다)
링크: 구글 뉴스 링크는 news.google.com을 거치는 리다이렉트라 googlenewsdecoder로
      실제 언론사 URL을 먼저 알아내 그쪽을 보여준다. v.daum.net으로 귀결되는 기사는
      반복적으로 문제(위젯 텍스트 오발송, 사용자가 링크 자체를 원치 않음)가 있어 아예
      발송에서 제외한다(EXCLUDE_LINK_DOMAINS).
본문 미리보기는 뺐다(2026-08-17): 기사 페이지를 긁어 첫 문단을 보여주는 방식을 시도했으나
      매체마다 "기사 읽어주기 서비스" 안내, 댓글 로그인 안내, 포털 소개문 같은 위젯 텍스트가
      본문과 같은 <p> 구조로 섞여 있어 오발송이 반복됐다(다음뉴스·연합뉴스TV·KBS 3건 실사고).
      NewsFinal은 이런 크롤링 노이즈를 Gemini로 한 번 걸러서 보여주는데, 우리는 그 필터링
      단계가 없어 구조적으로 더 취약하다 — 더 나은 방법(LLM 요약 등) 나오기 전까진 제목/출처/
      링크만 보낸다.

실행: python scripts/fetch_news.py
"""

import os
import re
import sys
import json
import html
import time
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import feedparser
import requests
from dotenv import load_dotenv
from googlenewsdecoder import gnewsdecoder

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(timezone.utc).astimezone(KST)


TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")

STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "state.json")

# ── 소스 ──────────────────────────────────────────────────────────────
GOOGLE_NEWS_QUERIES = {
    "속보": "https://news.google.com/rss/search?q=%22%EC%86%8D%EB%B3%B4%22&hl=ko&gl=KR&ceid=KR:ko",
    "단독": "https://news.google.com/rss/search?q=%22%EB%8B%A8%EB%8F%85%22&hl=ko&gl=KR&ceid=KR:ko",
}

TAG_LABEL_EMOJI = {"속보": "🚨", "단독": "🔍"}

# 대괄호 태그만 인정 — "[속보}" 같은 짝 안 맞는 오타도 허용하되, 일반 단어("단독선두" 등)는 걸러낸다.
TAG_RE = {
    "속보": re.compile(r"[\[\【]\s*속보\s*[\]\】\}]"),
    "단독": re.compile(r"[\[\【]\s*단독\s*[\]\】\}]"),
}

# ── 중복 판정 임계값 (NewsFinal auto_dedup.py의 2단계 유사도 구조를 이식) ──
# NewsFinal은 Postgres pg_trgm(문자 3-gram 유사도)을 쓴다. 한국어 제목은 조사 변화
# ("옥포동에서" vs "옥포동")로 단어 토큰 비교(rapidfuzz token_sort_ratio)가 헛돈다는 걸
# 실측으로 확인해(동일 사건인데 52% → REVIEW 문턱도 못 넘김) 문자 3-gram Jaccard로 교체했다.
# 임계값도 그에 맞춰 재보정: 무관한 제목 0%, 팩트만 겹치는 관련기사 0%,
# 문구까지 거의 같은 진짜 중복 40%대로 나뉘는 걸 실측 확인(fetch_news 개발 로그 참고).
# newsearch 프로젝트와 같은 원칙 유지: "같은 이슈"만 중복 처리, "연관성"만으로는 안 묶는다
# (오탐 리스크가 더 크다고 판단 — 겹쳐 보내는 게 아예 놓치는 것보다 낫다).
DUP_SKIP_THRESHOLD = 40     # 이상이면 완전 중복(문구까지 거의 동일) → 발송 생략
DUP_REVIEW_THRESHOLD = 25   # 이상~SKIP 미만이면 애매함 → 발송하되 리뷰 로그에 기록
DEDUP_WINDOW_HOURS = 6      # 이 시간 안에 보낸 기사만 비교 대상으로 삼는다

MAX_SEND_PER_RUN = 15       # 텔레그램 flood 방지용 1회 실행 상한
SEND_INTERVAL_SEC = 1.5     # 발송 간 대기

# v.daum.net은 반복적으로 문제가 됨(위젯 텍스트 오발송 실사고 + 사용자가 링크
# 자체를 원치 않음, 2026-08-17). 원문이 이 도메인으로 귀결되면 아예 발송하지 않는다.
EXCLUDE_LINK_DOMAINS = {"v.daum.net", "news.v.daum.net"}


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
    cutoff = now_kst() - timedelta(hours=DEDUP_WINDOW_HOURS * 4)  # guid 중복 방지는 좀 더 길게
    state["sent"] = [s for s in state["sent"] if s["sent_at"] >= cutoff.isoformat()]


# =========================
# UTIL
# =========================

def clean_title(raw: str, category: str) -> str:
    # [속보]/[단독] 태그는 일부러 안 지운다 — 메시지에서 한눈에 구분되게 원문 그대로 보여준다.
    t = re.sub(r"\s+", " ", raw or "").strip()
    # 구글이 title 끝에 " - 매체명"을 붙여주는 경우만 제거 (entry.source로 따로 뽑으므로 중복 방지)
    t = re.sub(r"\s*-\s*[^-]{1,20}$", "", t) if t.count(" - ") >= 1 else t
    return t.strip(" -")


def extract_source(entry) -> str:
    src = entry.get("source")
    if isinstance(src, dict) and src.get("title"):
        return src["title"]
    m = re.search(r"-\s*([^-]{1,20})$", entry.get("title", ""))
    return m.group(1).strip() if m else ""


def guid_of(entry) -> str:
    gid = entry.get("id") or entry.get("link") or entry.get("title", "")
    return hashlib.md5(gid.encode("utf-8")).hexdigest()


def _trigrams(s: str) -> set:
    s = re.sub(r"\s+", "", s or "")
    if len(s) < 3:
        return {s} if s else set()
    return {s[i:i + 3] for i in range(len(s) - 2)}


def title_similarity(a: str, b: str) -> float:
    """문자 3-gram Jaccard 유사도(0~100). Postgres pg_trgm의 로컬 대체 구현."""
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return (inter / union) * 100 if union else 0.0


def is_duplicate(title: str, category: str, recent: list):
    """최근 발송분과 제목 유사도 비교. (is_dup, score, matched_title) 반환."""
    best_score, best_title = 0.0, ""
    for item in recent:
        if item.get("category") != category:
            continue
        score = title_similarity(title, item["title"])
        if score > best_score:
            best_score, best_title = score, item["title"]
    return best_score >= DUP_SKIP_THRESHOLD, round(best_score, 1), best_title


# =========================
# FETCH
# =========================

def fetch_candidates(category: str, url: str) -> list:
    feed = feedparser.parse(url, request_headers={"User-Agent": "Mozilla/5.0"})
    out = []
    for entry in feed.entries:
        raw_title = entry.get("title", "")
        if not TAG_RE[category].search(raw_title):
            continue  # 태그 없는 일반 단어 매칭("단독선두" 등) 배제
        out.append({
            "guid": guid_of(entry),
            "title": clean_title(raw_title, category),
            "raw_title": raw_title,
            "link": entry.get("link", ""),
            "source": extract_source(entry),
            "category": category,
            "published": entry.get("published", ""),
        })
    return out


# =========================
# 링크 해석
# =========================

def resolve_real_url(google_link: str) -> str:
    """구글 뉴스 리다이렉트 링크 → 실제 언론사 URL. 실패하면 원래 링크 그대로."""
    try:
        res = gnewsdecoder(google_link, interval=1)
        if res.get("status") and res.get("decoded_url"):
            return res["decoded_url"]
    except Exception as e:
        print(f"  ⚠️ 링크 해석 실패: {e}")
    return google_link


# =========================
# TELEGRAM
# =========================

def send_telegram(item: dict) -> dict:
    emoji = TAG_LABEL_EMOJI.get(item["category"], "📰")
    title_safe = html.escape(item["title"])
    source_safe = html.escape(item["source"] or "출처 미상")
    link = item.get("real_link") or item["link"]
    msg = (
        f"{emoji} {title_safe}\n\n"
        f"📎 {source_safe}\n"
        f"🔗 {link}"
    )
    res = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=15,
    )
    return res.json()


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

    dedup_cutoff = (now_kst() - timedelta(hours=DEDUP_WINDOW_HOURS)).isoformat()
    recent_for_dedup = [s for s in state["sent"] if s["sent_at"] >= dedup_cutoff]

    to_send = []
    review_log = []

    for category, url in GOOGLE_NEWS_QUERIES.items():
        candidates = fetch_candidates(category, url)
        print(f"[{category}] 후보 {len(candidates)}건 (태그 필터 통과분)")

        for c in candidates:
            if c["guid"] in sent_guids:
                continue  # 이미 보낸 기사(같은 기사 재수집)

            is_dup, score, matched = is_duplicate(c["title"], category, recent_for_dedup)
            if is_dup:
                print(f"  ⏭️  중복 생략 ({score}%): {c['title'][:40]} ≈ {matched[:40]}")
                continue

            if DUP_REVIEW_THRESHOLD <= score < DUP_SKIP_THRESHOLD:
                review_log.append({"title": c["title"], "matched": matched, "score": score})

            to_send.append(c)
            # 이번 실행 안에서 뽑은 기사끼리도 서로 중복 비교 대상에 넣는다
            recent_for_dedup.append({"title": c["title"], "category": category,
                                      "sent_at": now_kst().isoformat()})

    to_send = to_send[:MAX_SEND_PER_RUN]
    print(f"발송 대상 {len(to_send)}건 (상한 {MAX_SEND_PER_RUN}건)")

    sent_count = 0
    for item in to_send:
        item["real_link"] = resolve_real_url(item["link"])

        domain = urlparse(item["real_link"]).netloc.replace("www.", "")
        if domain in EXCLUDE_LINK_DOMAINS:
            # 발송은 생략하지만 guid는 기록해서 다음 실행에 다시 후보로 안 올라오게 한다.
            state["sent"].append({
                "guid": item["guid"], "title": item["title"], "category": item["category"],
                "sent_at": now_kst().isoformat(),
            })
            print(f"  🚫 제외 도메인({domain}): {item['title'][:40]}")
            continue

        res = send_telegram(item)
        if res.get("ok"):
            sent_count += 1
            state["sent"].append({
                "guid": item["guid"], "title": item["title"], "category": item["category"],
                "sent_at": now_kst().isoformat(),
            })
            print(f"  ✅ [{item['category']}] {item['title'][:50]}")
        else:
            print(f"  ❌ 발송 실패: {res}")
        time.sleep(SEND_INTERVAL_SEC)

    save_state(state)

    if review_log:
        print(f"\n⚠️ 애매한 유사도({DUP_REVIEW_THRESHOLD}~{DUP_SKIP_THRESHOLD}%) 발송분 {len(review_log)}건 — 임계값 조정 참고용")
        for r in review_log:
            print(f"   {r['score']}% : {r['title'][:35]} ≈ {r['matched'][:35]}")

    print(f"\n[완료] 발송 {sent_count}건 / 전체 후보 처리 완료")


if __name__ == "__main__":
    run()
