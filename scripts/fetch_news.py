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
본문 미리보기: 구글 뉴스 링크는 news.google.com을 거치는 리다이렉트라 googlenewsdecoder로
      실제 언론사 URL을 먼저 알아낸 뒤, 그 페이지에서 첫 문단을 긁어 요약으로 붙인다.
      다음뉴스(v.daum.net) 등 JS로 렌더링되는 포털 미러 페이지는 본문이 안 잡히는데,
      이 경우 조용히 건너뛰고 제목/출처/링크만 보낸다(발송 자체를 막지는 않는다).

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

EXCERPT_MAX_CHARS = 160
CRAWL_TIMEOUT_SEC = 8
# 사진 캡션/저작권 표기 등 본문이 아닌 잡음. 이 문구가 나오면 그 문단부터는 버린다.
EXCERPT_STOP_MARKS = ("무단전재", "재배포 금지", "AI 학습", "저작권자", "Copyright ©")


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
# 본문 미리보기
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


def crawl_excerpt(url: str, title: str) -> str:
    """기사 URL에서 첫 문단을 긁어 짧은 미리보기로 반환. 실패하면 빈 문자열."""
    try:
        res = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=CRAWL_TIMEOUT_SEC,
        )
        if res.status_code != 200:
            return ""
        page = res.text
        page = re.sub(r"<script[^>]*>.*?</script>", "", page, flags=re.DOTALL | re.IGNORECASE)
        page = re.sub(r"<style[^>]*>.*?</style>", "", page, flags=re.DOTALL | re.IGNORECASE)

        art = re.search(r"<article[^>]*>(.*?)</article>", page, re.DOTALL | re.IGNORECASE)
        scope = art.group(1) if art else page

        title_core = re.sub(r"[\[\【]\s*(속보|단독)\s*[\]\】\}]", "", title).strip()

        for p in re.findall(r"<p[^>]*>(.*?)</p>", scope, re.DOTALL | re.IGNORECASE):
            t = re.sub(r"<[^>]+>", "", p).strip()
            t = re.sub(r"\s+", " ", t)
            if len(t) < 40:
                continue
            if any(mark in t for mark in EXCERPT_STOP_MARKS):
                continue
            if fuzz_ratio_cheap(t, title_core) > 0.6:
                continue  # 제목을 그대로 반복하는 문단(일부 매체 템플릿)은 건너뛴다
            if len(t) > EXCERPT_MAX_CHARS:
                t = t[:EXCERPT_MAX_CHARS].rsplit(" ", 1)[0] + "…"
            return t
        return ""
    except Exception:
        return ""


def fuzz_ratio_cheap(a: str, b: str) -> float:
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# =========================
# TELEGRAM
# =========================

def send_telegram(item: dict) -> dict:
    emoji = TAG_LABEL_EMOJI.get(item["category"], "📰")
    title_safe = html.escape(item["title"])
    source_safe = html.escape(item["source"] or "출처 미상")
    excerpt_block = f"\n\n{html.escape(item['excerpt'])}" if item.get("excerpt") else ""
    link = item.get("real_link") or item["link"]
    msg = (
        f"{emoji} {title_safe}"
        f"{excerpt_block}\n\n"
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
        item["excerpt"] = crawl_excerpt(item["real_link"], item["title"])

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
