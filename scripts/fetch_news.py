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
      발송에서 제외한다(EXCLUDE_LINK_DOMAINS). 메시지에는 raw URL을 노출하지 않고
      "출처"라는 단어와 채널명 자체에 링크를 건다(2026-08-17 사용자 결정).
본문 요약(2026-08-17 재도입): 정규식으로 "첫 문단"을 고르는 방식은 매체마다 다른 위젯
      텍스트(읽어주기 서비스, 댓글 안내 등)를 본문으로 오인해 반복적으로 실패했다(다음뉴스·
      연합뉴스TV·KBS 3건 실사고). 규칙 기반 대신 크롤링한 원문(노이즈 섞여도 무방)을 통째로
      Gemini에 넘겨 핵심만 2문장으로 뽑게 한다 — LLM은 광고/위젯 텍스트를 의미로 걸러내므로
      정규식보다 안정적이다. 요약 실패 시(키 없음/쿼터 초과 등) 조용히 건너뛰고 제목/출처/
      링크만 보낸다(발송 자체는 막지 않는다).

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

# 프로젝트(키) 2개로 무료 티어 RPD를 나눠 쓴다. 라이트 모델 RPD가 500이라 실측상
# 대부분의 요약이 거기로 몰리는데(3.7/3.6/3.5가 503·타임아웃으로 자주 실패), 키 하나론
# 바쁜 날 500 한도에 걸릴 수 있어 2개로 시작(2026-08-17 사용자 결정).
GEMINI_API_KEYS = [k for k in [os.getenv("GEMINI_API_KEY"), os.getenv("GEMINI_API_KEY_2")] if k]

# 최신 → 구버전 순 폴백 (NewsFinal gemini_summarizer.py와 동일한 검증된 순서)
GEMINI_MODELS = ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash",
                  "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
CHANNEL_TAG = "뉴스앤이슈"
CHANNEL_URL = "https://t.me/news_issue"

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
# 본문 요약
# =========================

CRAWL_TIMEOUT_SEC = 8
CRAWL_MAX_CHARS = 4000  # LLM에 넘길 원문 상한 (비용/속도 절충)


def crawl_article_text(url: str) -> str:
    """기사 URL에서 본문 후보 텍스트를 긁어온다.

    처음엔 <article>/<p> 태그로 정교하게 스코핑을 시도했으나, 언론사마다 마크업이
    달라 실패했다(실사고 2026-08-17: newsis.com은 <article> 안에 "관련기사" 사이드바
    제목 목록과 <script> 코드가 본문 <p>와 뒤섞여 있어 완전히 엉뚱한 내용이 잡힘).
    정규식으로 정교하게 자르려 하지 않고, nav/header/footer/aside/스크립트류만 크게
    걷어낸 뒤 페이지 텍스트를 통째로 Gemini에 넘긴다 — 어차피 LLM이 의미로 걸러내므로
    우리가 미리 좁히려는 시도가 오히려 엉뚱한 내용을 골라내는 역효과를 냈다.
    """
    try:
        res = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=CRAWL_TIMEOUT_SEC,
        )
        if res.status_code != 200:
            return ""
        page = res.text
        for tag in ("script", "style", "nav", "header", "footer", "aside", "form", "noscript"):
            page = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", "", page, flags=re.DOTALL | re.IGNORECASE)

        text = re.sub(r"<[^>]+>", " ", page)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:CRAWL_MAX_CHARS]
    except Exception:
        return ""


# 모델별로 RPD(429)가 소진된 키를 기록 — 이 프로세스(=1회 실행) 동안만 유효.
# NewsFinal(gemini_summarizer.py)과 같은 구조.
_exhausted_keys = {m: set() for m in GEMINI_MODELS}
_current_key_idx = 0


def summarize_with_gemini(title: str, raw_text: str) -> str:
    """크롤링한 원문을 Gemini로 2문장 요약. 실패하면 빈 문자열(발송은 계속 진행)."""
    global _current_key_idx
    if not GEMINI_API_KEYS or not raw_text:
        return ""

    prompt = (
        f"다음은 뉴스 기사 웹페이지 전체에서 태그만 제거하고 추출한 텍스트다. "
        f"기사 제목: \"{title}\"\n\n"
        "이 텍스트에는 광고, 구독 안내, 댓글, 저작권 문구뿐 아니라 '관련기사'나 "
        "'많이 본 뉴스' 같은 다른 기사 제목 목록까지 섞여 있을 수 있다. 반드시 "
        "위 제목과 일치하는 기사 본문만 찾아 그 핵심을 2문장 이내, 120자 안팎의 "
        "한국어로 요약해라. 다른 기사의 내용을 섞지 마라. 해라체(-다로 끝나는 "
        "문장)로 쓰고, 다른 언론사명은 언급하지 마라. 본문에서 확인되지 않는 내용은 "
        "추가하지 마라. 본문을 찾을 수 없으면 빈 문자열만 출력해라. "
        "요약문(또는 빈 문자열)만 출력하고 다른 말은 덧붙이지 마라.\n\n"
        f"[추출된 텍스트]\n{raw_text}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        # maxOutputTokens=200은 3.x 계열(내부 추론에 토큰을 먼저 쓰는 "thinking"
        # 모델)에서 답변 전에 토큰이 바닥나 parts가 비는 원인이었다(실측 2026-08-17).
        # NewsFinal(gemini_summarizer.py)과 같은 500으로 맞춘다.
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 500},
    }

    n = len(GEMINI_API_KEYS)
    for model in GEMINI_MODELS:
        exhausted = _exhausted_keys[model]
        available = [i for i in range(n) if i not in exhausted]
        if not available:
            continue
        ordered = sorted(available, key=lambda i: (i - _current_key_idx) % n)

        for idx in ordered:
            try:
                res = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    params={"key": GEMINI_API_KEYS[idx]},
                    json=payload,
                    timeout=20,
                )
                if res.status_code == 429:
                    print(f"  ⚠️ Gemini({model}) 키 {idx+1} RPD 소진, 다른 키로 폴백")
                    exhausted.add(idx)
                    continue
                if res.status_code != 200:
                    print(f"  ⚠️ Gemini({model}) 키 {idx+1} HTTP {res.status_code}, 폴백")
                    continue
                cand = res.json()["candidates"][0]
                # MAX_TOKENS 등으로 잘린 응답은 버린다(끊긴 문장 발송 사고 방지).
                finish = cand.get("finishReason", "")
                if finish and finish != "STOP":
                    print(f"  ⚠️ Gemini({model}) 비정상 종료(finishReason={finish}), 폴백")
                    continue
                text = "".join(p.get("text", "") for p in cand["content"]["parts"]).strip()
                if text:
                    _current_key_idx = (idx + 1) % n
                    return text
            except Exception as e:
                print(f"  ⚠️ Gemini({model}) 키 {idx+1} 요약 실패: {e}")
                continue
    return ""


# =========================
# TELEGRAM
# =========================

def send_telegram(item: dict) -> dict:
    emoji = TAG_LABEL_EMOJI.get(item["category"], "📰")
    title_safe = html.escape(item["title"])
    link = item.get("real_link") or item["link"]
    summary_block = f"\n\n{html.escape(item['summary'])}" if item.get("summary") else ""
    # 채널 링크도 URL을 따로 노출하지 않고 채널명 자체에 건다(2026-08-17 사용자 결정).
    tag_line = f"\n\n{item['category']}, <a href=\"{CHANNEL_URL}\">{CHANNEL_TAG}</a>"
    # 링크는 매체명이 아니라 "출처"라는 고정 단어에 건다(2026-08-17 사용자 결정) —
    # 어느 언론사인지는 메시지에 노출하지 않고, 클릭하면 원문으로 이동만 시킨다.
    msg = (
        f"{emoji} {title_safe}"
        f"{summary_block}\n\n"
        f"📎 <a href=\"{link}\">출처</a>"
        f"{tag_line}"
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

        raw_text = crawl_article_text(item["real_link"])
        item["summary"] = summarize_with_gemini(item["title"], raw_text)

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
