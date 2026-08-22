"""
scripts/fetch_news.py
----------------------
"뉴스 앤 이슈" 채널용 속보/단독/종합 뉴스 자동 수집·발송.

소스: 구글 뉴스 검색 RSS ("속보", "단독", "종합" 키워드) — 사실상 전체 한국 언론사를 커버.
필터: 제목에 실제 [속보]/[단독]/[종합] 대괄호 태그가 붙은 기사만 통과시킨다.
      (구글 검색은 "단독선두", "종합특검" 같은 일반 단어·복합어도 섞어 주므로
       태그 정규식으로 재검증 필요 — "종합"은 특히 실측상 100건 중 2건만 진짜 태그)
중복 제거(2단계, 2026-08-17 재설계): 같은 사건을 여러 매체가 거의 동시에, 서로 다른
      구체 사실(득표율·발언 인용 등)을 섞어 보도하는 경우가 많다.
      1단계 — 제목만으로 값싸게 거른다(크롤링 없음). 문구까지 거의 같은 명백한 중복만
        잡는다(DUP_SKIP_THRESHOLD, 문자 3-gram). NewsFinal auto_dedup.py 구조를
        로컬로 이식한 것.
      2단계 — 1단계를 통과한 후보만 본문을 크롤링·Gemini 요약한 뒤, 그 요약끼리
        비교한다(is_summary_duplicate). 제목만으로는 "같은 사건, 다른 표현"과
        "같은 사건, 다른 후속 사실"을 구분하지 못한다 — 실측(2026-08-17): 문화일보·
        서울신문이 거의 동일 보도한 "잠실 호텔 탄창 발견"은 제목 유사도 13%로 안
        걸렸지만 요약끼리는 48.5%로 뚜렷이 잡혔다. 반대로 "김민석 인선" vs "김민석
        당선"처럼 완전히 다른 사건은 요약 유사도가 0%로 나와, 제목 대신 요약 비교로
        바꾸면서 개수 캡(주제당 N건) 없이도 진짜 중복만 정확히 거를 수 있게 됐다
        (캡 방식은 폐기 — 중요한 후속 속보가 개수 제한에 걸려 묻히는 부작용이 있었다).
        시황/지수 속보(코스피·코스닥 등락, 사이드카 등)는 문구가 고정 템플릿이라
        추가 보정이 필요했다 — is_duplicate/is_summary_duplicate 옆 주석 참고.
      비교 대상 시간창(DEDUP_WINDOW_HOURS_BY_CATEGORY)도 카테고리별로 다르다 —
        속보는 시황처럼 몇 분 단위로 실제 값이 바뀌는 경우가 있어 짧게(2시간),
        단독/종합은 몇 시간~하루 간격으로 재등장하는 재탕을 잡아야 해서 길게
        (12시간) 둔다 — prune_state 옆 주석 참고.
      (Supabase/Gemini 통합 재작성 단계는 여기선 안 씀 — 우리는 자체 기사를 쓰는 게 아니라
       원문 링크를 그대로 전달하는 큐레이션이라 "통합"이 아니라 "생략"이 맞는 대응이다)
링크: 구글 뉴스 링크는 news.google.com을 거치는 리다이렉트라 googlenewsdecoder로
      실제 언론사 URL을 먼저 알아내 그쪽을 보여준다.
신선도(2026-08-21): 구글 뉴스 검색 결과는 발행 시각 순이 아니라 관련도 순이라,
      오래전에 발행된 기사가 뒤늦게 우리 폴링 결과에 걸릴 수 있다. "속보"는 "지금
      막 일어난 일"이라는 프레이밍이 핵심이라 이미 오래된 기사를 속보로 보내면
      사실과 다른 인상을 준다 — fetch_candidates 옆 주석 참고.
본문 요약: 정규식으로 "첫 문단"을 고르는 방식은 매체마다 다른 위젯 텍스트(읽어주기
      서비스, 댓글 안내 등)를 본문으로 오인해 반복적으로 실패했다(다음뉴스·연합뉴스TV·
      KBS 3건 실사고). 규칙 기반 대신 크롤링한 원문(노이즈 섞여도 무방)을 통째로 Gemini에
      넘겨 핵심만 2문장으로 뽑게 한다 — LLM은 광고/위젯 텍스트를 의미로 걸러내므로 정규식
      보다 안정적이다. 이제 모든 카테고리(속보 포함)에서 요약을 만든다 — 중복판정에
      쓰일 뿐 아니라 채널에도 함께 보여준다. 요약 실패 시(키 없음/쿼터 초과/Gemini의
      "본문을 확인할 수 없다"류 메타응답 등) 조용히 건너뛰고 제목/출처/링크만 보낸다
      (발송 자체는 막지 않는다).
결정 이력 로깅(2026-08-20): 중복판정 원인을 나중에 재구성할 수 있도록 Cloudflare
      D1에 스테이지-2 판정(발송/중복스킵/도메인제외)을 전부 남긴다(log_decision).
      실패해도(네트워크 등) 발송 흐름을 막지 않는 best-effort.

실행: python scripts/fetch_news.py
"""

import os
import re
import sys
import json
import html
import time
import calendar
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

# 최신 → 구버전 순 폴백 (NewsFinal gemini_summarizer.py와 동일한 검증된 순서).
# 단독은 물량이 적고 심층 취재물이라 상위 모델까지 다 시도할 값어치가 있다.
# 속보·종합은 물량이 많고 빠른 처리가 우선이라(실측: 3.7/3.6/3.5는 503·타임아웃이
# 잦아 여기서 시간만 잡아먹음) 처음부터 라이트 모델로 바로 간다(2026-08-17 사용자
# 결정) — 상위 모델 RPD도 단독에 집중해서 아낄 수 있다.
GEMINI_MODELS_FULL = ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash",
                       "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
GEMINI_MODELS_LITE = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
GEMINI_MODELS_BY_CATEGORY = {"단독": GEMINI_MODELS_FULL}  # 그 외 카테고리는 LITE


def _models_for_category(category: str) -> list:
    return GEMINI_MODELS_BY_CATEGORY.get(category, GEMINI_MODELS_LITE)


CHANNEL_TAG = "뉴스앤이슈"
CHANNEL_URL = "https://t.me/news_issue"

STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "state.json")

# ── 소스 ──────────────────────────────────────────────────────────────
GOOGLE_NEWS_QUERIES = {
    "속보": "https://news.google.com/rss/search?q=%22%EC%86%8D%EB%B3%B4%22&hl=ko&gl=KR&ceid=KR:ko",
    "단독": "https://news.google.com/rss/search?q=%22%EB%8B%A8%EB%8F%85%22&hl=ko&gl=KR&ceid=KR:ko",
    "종합": "https://news.google.com/rss/search?q=%22%EC%A2%85%ED%95%A9%22&hl=ko&gl=KR&ceid=KR:ko",
}

TAG_LABEL_EMOJI = {"속보": "🚨", "단독": "🔍", "종합": "📋"}

# 대괄호 태그만 인정 — "[속보}" 같은 짝 안 맞는 오타도 허용하되, 일반 단어("단독선두",
# "종합특검", "종합소득세" 등)는 걸러낸다. 실측(2026-08-17): "종합" 검색 결과 100건 중
# "[종합]" 태그가 정확히 붙은 건 2건뿐, 나머지는 "종합특검" 같은 복합어라 이 정규식이
# 필수(느슨하게 하면 관련 없는 기사가 대량 유입됨).
TAG_RE = {
    "속보": re.compile(r"[\[\【]\s*속보\s*[\]\】\}]"),
    "단독": re.compile(r"[\[\【]\s*단독\s*[\]\】\}]"),
    "종합": re.compile(r"[\[\【]\s*종합\s*[\]\】\}]"),
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
# 6시간은 너무 길다는 사용자 판단으로 2시간으로 축소(2026-08-20) — "코스피 4%대"처럼
# stage-2에서 걸러진(=한 번도 발송 안 된) 유령 항목이 6시간 내내 비교 대상에 남아있던
# 부작용이 있었다. 그런데 그 문제의 진짜 원인은 창 길이가 아니라 숫자충돌가드
# 부재였고, 그건 그 다음 날(2026-08-20) 별도로 고쳤다. 반면 2시간으로 줄인 채로
# 두니 "한화·리플 전격 제휴" 같은 단독 기사가 17.5시간 뒤 재발행(?)돼 완전히
# 동일한 내용으로 두 번 발송되는 새 문제가 생겼다(실사고 2026-08-22) — 단독/종합은
# 시황처럼 몇 분 단위로 실제 값이 바뀌는 게 아니라서 창을 길게 둬도 안전하고,
# 오히려 길게 둬야 이런 재탕을 잡는다. 그래서 카테고리별로 분리했다: 시황이
# 자주 나오는 속보는 2시간 유지, 단독/종합은 12시간으로 늘림(2026-08-22 사용자 결정).
DEDUP_WINDOW_HOURS_BY_CATEGORY = {
    "속보": 2,
    "단독": 12,
    "종합": 12,
}


def _dedup_window_hours(category: str) -> int:
    return DEDUP_WINDOW_HOURS_BY_CATEGORY.get(category, 2)

# ── 본문 요약 기반 중복판정(2026-08-17, 주제 포화 캡을 대체) ────────────
# 처음엔 "제목 캡"(주제당 최근 N건까지만)으로 "김민석 당대표 선출" 18건 폭주를
# 막았는데, 사용자가 두 가지 문제를 지적했다:
#   1) "잠실 5성 호텔 탄창 발견"을 문화일보·서울신문이 거의 동일하게 보도했는데
#      제목 단어 자카드가 13.3%로 기준(15%)에 살짝 못 미쳐 캡을 2→1로 낮춰도
#      여전히 둘 다 통과할 뻔했다 — 제목만으로는 근본적으로 한계가 있다.
#   2) 캡은 "몇 건째냐"만 볼 뿐 실제로 중복인지 안 보므로, 정말 새로운 사실이
#      담긴 중요한 후속 속보까지 개수 제한에 걸려 묻힐 위험이 있다.
# 그래서 제목 대신 "본문을 크롤링해 Gemini로 뽑은 요약"끼리 비교하는 방식으로
# 바꿨다. 실측(2026-08-17): 같은 사건 다른 매체 요약끼리는 단어 자카드 48.5%·
# 문자 3-gram 32%인 반면, 같은 인물의 완전히 다른 사건(김민석 인선 vs 당선)
# 요약끼리는 0%/0% — 제목보다 훨씬 뚜렷하게 갈린다. 이제 캡이 아니라 "실제로
# 내용이 겹치는지"로 판단하므로 중요한 후속 속보를 개수 제한으로 놓칠 일이 없다.
#
# 실사고(2026-08-18): 경향신문·조선일보가 같은 "정성호 법무장관 사의" 기사를
# 냈는데, 경향신문 요약은 사직 배경·청와대 인사 절차까지 상세히 담았고 조선일보
# 요약은 발언 요지만 짧게 담아 분량 차이가 컸다. 이 경우 Jaccard(교집합/합집합)는
# 짧은 쪽에 없는 단어까지 분모에 다 들어가 13.2%(단어)/10.0%(문자)로 실제보다
# 낮게 나와 임계값(25/18)을 통과 못 하고 새는 걸 확인. 반면 교집합/두 집합 중
# 작은 쪽 크기로 나누는 오버랩 계수는 같은 데이터에서 38.5%(단어)/32.5%(문자)로
# 뚜렷하게 잡힌다 — 요약 하나가 다른 하나를 사실상 포함하는 이런 비대칭 분량
# 상황에 특화된 지표라 Jaccard 대신 이걸로 교체. 오탐 검증: 같은 날 같은 전당대회의
# "당대표 선출" vs "최고위원 선출"(서로 다른 사건)은 오버랩 계수로도 6.7%(단어)/
# 15.8%(문자)에 그쳐 아래 임계값에 안전하게 못 미친다(진짜 중복과 10%p 이상 여유).
SUMMARY_WORD_OVERLAP_THRESHOLD = 30  # 이상이면 중복(단어 단위, 작은 쪽 집합 기준 포함비율)
SUMMARY_CHAR_TRIGRAM_OVERLAP_THRESHOLD = 25  # 이상이면 중복(문자 단위) — 둘 중 하나만 넘어도 중복 처리
TOPIC_STOPWORDS = {"속보", "단독", "종합", "오늘", "발표", "관련", "최근", "이후", "현재"}

# 실사고(2026-08-18): 원래 속보/단독/종합을 합쳐 MAX_SUMMARIZE_PER_RUN=20 /
# MAX_SEND_PER_RUN=15로 캡을 걸었었다. GOOGLE_NEWS_QUERIES가 속보→단독→종합
# 순으로 도는데, 밤 시간대 정치 속보가 몰리면(실측: 이 시각 속보 태그 매칭
# 103건, 단독 84건) 속보만으로 전체 캡을 다 채워버려 단독이 통째로 밀려났다.
# 21:18 이후 두 차례 실행이 다 "성공"으로 끝났는데도 채널엔 아무것도 안
# 올라온 게 이 때문(구글 뉴스엔 "국정원", "한미협의" 단독이 떠 있었는데 캡에
# 밀려 후보 목록에 들지도 못함). 카테고리별로 캡을 나누는 방안도 검토했지만
# 사용자가 "개수 캡을 걸지 마"라고 확정 — 캡 자체를 없앤다. 텔레그램은 같은
# 채팅방에 초당 1건 정도로만 안전하게 보낼 수 있어 SEND_INTERVAL_SEC 간격
# 발송은 유지한다 — 발송 건수가 많아지면 실행 시간이 늘어날 뿐, 유실되지 않는다.
SEND_INTERVAL_SEC = 1.5      # 발송 간 대기

# 모든 카테고리에서 요약을 만든다(2026-08-17) — 중복판정에 요약이 필요해졌고,
# 사용자도 속보에 요약이 붙는 걸 확인하고 괜찮다고 했다. 비용은 늘지만
# (RPD 여유는 키 2개로 확보) 정확한 중복 제거가 우선이라는 판단.

# v.daum.net은 반복적으로 문제가 됨(위젯 텍스트 오발송 실사고 + 사용자가 링크
# 자체를 원치 않음, 2026-08-17). 원문이 이 도메인으로 귀결되면 아예 발송하지 않는다.
EXCLUDE_LINK_DOMAINS = {"v.daum.net", "news.v.daum.net"}

# 실사고(2026-08-20): 삼성전자 100조 주주환원 기사가 오탐으로 중복 스킵됐는데,
# GitHub Actions 로그는 c['title']만 찍혀서 실제 비교된 요약과 매칭 대상을
# 재구성할 수 없었다(90일 뒤엔 로그 자체도 사라짐). Cloudflare D1에 스테이지-2
# 판정을 전부 남겨서 나중에 SQL로 바로 조회할 수 있게 한다 — 스키마는
# schema.sql 참고. 실패해도(네트워크 문제 등) 발송 자체는 막지 않는다
# (best-effort, save_state와 무관하게 독립적으로 동작).
CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID", "")
CF_D1_DATABASE_ID = os.getenv("CF_D1_DATABASE_ID", "")
CF_API_TOKEN = os.getenv("CF_API_TOKEN", "")


def log_decision(guid: str, category: str, title: str, link: str, decision: str,
                  compare_text: str = "", matched_text: str = "",
                  word_score: float = None, char_score: float = None, summary: str = ""):
    """중복판정/발송 결과를 D1에 기록한다. best-effort — 실패해도 발송 흐름을 막지 않는다."""
    if not (CF_ACCOUNT_ID and CF_D1_DATABASE_ID and CF_API_TOKEN):
        return
    try:
        requests.post(
            f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{CF_D1_DATABASE_ID}/query",
            headers={"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"},
            json={
                "sql": ("INSERT INTO decisions "
                        "(run_at, guid, category, title, link, decision, compare_text, "
                        "matched_text, word_score, char_score, summary) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"),
                "params": [now_kst().isoformat(), guid, category, title, link, decision,
                           compare_text, matched_text, word_score, char_score, summary],
            },
            timeout=8,
        )
    except Exception as e:
        print(f"  ⚠️ D1 기록 실패(무시하고 계속): {e}")


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
    # 가장 긴 카테고리 창(현재 단독/종합 12시간) 기준으로 *4배 — guid 중복 방지는
    # 어떤 카테고리든 그보다 짧게 잘리면 안 되므로 최댓값을 쓴다.
    max_window = max(DEDUP_WINDOW_HOURS_BY_CATEGORY.values())
    cutoff = now_kst() - timedelta(hours=max_window * 4)  # guid 중복 방지는 좀 더 길게
    state["sent"] = [s for s in state["sent"] if s["sent_at"] >= cutoff.isoformat()]


def load_gemini_exhausted(state: dict) -> dict:
    """오늘 이미 RPD가 소진된 것으로 확인된 (모델, 키) 조합을 state에서 복원한다.

    실사고(2026-08-17): 상위 모델(3.7/3.6/3.5) RPD가 20으로 매우 낮아 사실상 하루
    초반에 바로 소진되는데, cron마다 파이썬 프로세스가 새로 뜨면서 "오늘 이미
    소진됐다"는 기억이 실행마다 사라져 매번 헛되이 상위 모델부터 재시도하고
    있었다(실측: 최근 단독 13건 중 5건이 요약 없이 나감). 하루 단위로 state.json에
    저장해두고 재사용한다 — RPD는 자정(태평양시) 리셋이지만 정확한 경계까지 맞출
    필요는 없어 KST 날짜가 바뀌면 초기화하는 것으로 근사한다.
    """
    g = state.get("gemini_exhausted") or {}
    today = now_kst().strftime("%Y-%m-%d")
    if g.get("date") != today:
        return {}
    return {m: set(idxs) for m, idxs in g.get("keys", {}).items()}


def save_gemini_exhausted(state: dict, exhausted_keys: dict):
    state["gemini_exhausted"] = {
        "date": now_kst().strftime("%Y-%m-%d"),
        "keys": {m: sorted(s) for m, s in exhausted_keys.items() if s},
    }


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


# 실사고(2026-08-21): "코스닥 매도 사이드카…지수 800.86"(머니투데이)과 "코스닥
# '매도 사이드카' 발동…4% 급락 800선"(뉴스웍스)이 같은 날 같은 사건(한국거래소
# 매도 사이드카 발동 1건)인데도 중복 처리 안 되고 둘 다 발송됨. 원인 중 하나:
# 크롤링된 본문에서 한쪽은 공식 용어("프로그램 매도호가 일시효력정지")를,
# 다른 쪽은 통용 용어("매도 사이드카")를 써서 단어/문자 단위 비교에서 겹치는
# 게 거의 없었다(word 14.3%/char 22.2%, 둘 다 임계값 미만). 사이드카는 이
# 채널에서 반복적으로 나오는 고정 이벤트 템플릿이라 공식-통용 용어 쌍을
# 미리 맞춰준다 — 서킷브레이커(매매거래정지)는 사이드카와 다른 별개 조치라
# 여기 섞지 않는다.
_MARKET_TERM_ALIASES = [
    ("매도호가 일시효력정지", "매도 사이드카"),
    ("매수호가 일시효력정지", "매수 사이드카"),
    ("프로그램매도호가효력정지", "매도 사이드카"),
    ("프로그램매수호가효력정지", "매수 사이드카"),
]


def _normalize_market_terms(text: str) -> str:
    for formal, common in _MARKET_TERM_ALIASES:
        text = text.replace(formal, common)
    return text


def _trigrams(s: str) -> set:
    s = re.sub(r"\s+", "", _normalize_market_terms(s or ""))
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


# 실사고(2026-08-20): "코스피 4%대 급등…매수 사이드카 발동"과 "코스피 6%대
# 급등…매수 사이드카 발동"이 문구 템플릿이 같다는 이유로 72.7% 제목 유사도로
# 중복 처리됐다. 사용자 지적: "코스피 같은 증시 기사는 매일이 다른 기사인데
# 이걸 중복처리 해버렸다" — 시황/지수 속보는 문구는 거의 고정 템플릿이고 핵심
# 수치(%, 지수, 환율 등)만 실시간으로 바뀌는데, 그 수치야말로 진짜 "새 정보"라
# 문구 유사도만으로 중복 판정하면 안 된다. 두 텍스트에 다 숫자가 있고 그 숫자
# 집합이 완전히 겹치지 않으면(공통 숫자가 하나도 없으면) 문구가 비슷해도 다른
# 속보로 본다 — is_duplicate(제목)·is_summary_duplicate(요약) 둘 다에 적용.
#
# 퍼센트만 뽑는다(지수·건수 등 다른 숫자는 제외) — 실사고(2026-08-21): 위 사이드카
# 사례에서 한쪽 텍스트엔 지수값("800.86")만, 다른 쪽엔 등락률("4%")만 있어 두
# 텍스트가 같은 사건을 다른 지표로 보도한 것뿐인데도 "공통 숫자 없음"으로 오판돼
# 진짜 중복이 그냥 통과됐다. 지수·건수 같은 숫자는 매체마다 어떤 지표를 앞세우는지
# 취향 차이가 커서 "겹치는 숫자가 없다"는 사실 자체가 신뢰할 만한 신호가 아니다.
# 반면 %는 코스피/코스닥 등락률처럼 같은 지표를 놓고 시점별로 실제 값이 달라지는
# 시황 속보의 핵심 차별화 지점이라 이것만 비교 대상으로 남긴다.
_NUMBER_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?%")


def _extract_key_numbers(text: str) -> set:
    """퍼센트 수치만 뽑는다(시황 기사 실시간 갱신 구분용)."""
    return set(_NUMBER_RE.findall(text or ""))


def _numbers_conflict(a: str, b: str) -> bool:
    """두 텍스트 다 숫자가 있는데 겹치는 숫자가 하나도 없으면 True — 같은 문구
    템플릿이라도 실제로는 다른 시점/수치를 보도하는 별개 속보라는 뜻."""
    na, nb = _extract_key_numbers(a), _extract_key_numbers(b)
    return bool(na) and bool(nb) and not (na & nb)


def is_duplicate(title: str, category: str, recent: list):
    """최근 발송분과 제목 유사도 비교. (is_dup, score, matched_title) 반환."""
    best_score, best_title = 0.0, ""
    for item in recent:
        if item.get("category") != category:
            continue
        score = title_similarity(title, item["title"])
        if score > best_score:
            best_score, best_title = score, item["title"]
    is_dup = best_score >= DUP_SKIP_THRESHOLD
    if is_dup and _numbers_conflict(title, best_title):
        is_dup = False
    return is_dup, round(best_score, 1), best_title


def _keywords(title: str) -> set:
    """제목에서 태그·불용어를 뺀 2글자 이상 단어 토큰 집합. 주제 포화 판정용."""
    t = re.sub(r"[\[\【][^\]\】]*[\]\】]", "", _normalize_market_terms(title or ""))
    toks = re.findall(r"[가-힣A-Za-z0-9]{2,}", t)
    return {w for w in toks if w not in TOPIC_STOPWORDS}


def _overlap_coeff(a: set, b: set) -> float:
    """오버랩 계수(0~100) = 교집합 / 두 집합 중 작은 쪽 크기.

    Jaccard(교집합/합집합)는 두 텍스트 분량이 비슷할 때만 잘 맞는다. 한쪽 요약이
    다른 쪽을 사실상 포함하되 훨씬 상세한 경우(요약 모델·카테고리별 분량 차이로
    흔함) Jaccard는 짧은 쪽에 없는 단어까지 분모(합집합)에 넣어 점수를 실제보다
    낮춘다 — is_summary_duplicate 참고.
    """
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b)) * 100


def word_overlap(a: str, b: str) -> float:
    """단어 단위 오버랩 계수(0~100). 조사 변화에 강함."""
    return _overlap_coeff(_keywords(a), _keywords(b))


def char_trigram_overlap(a: str, b: str) -> float:
    """문자 3-gram 오버랩 계수(0~100)."""
    return _overlap_coeff(_trigrams(a), _trigrams(b))


def is_summary_duplicate(text: str, category: str, recent: list):
    """요약(또는 요약 실패 시 제목)을 최근 발송분과 비교. (is_dup, score, matched) 반환.

    제목 기반 is_duplicate()보다 훨씬 정확하다 — 실측(2026-08-17): 같은 사건을
    다르게 쓴 제목은 자카드 13~15%로 갈렸지만, 같은 사건의 요약끼리는 48.5%,
    완전히 다른 사건의 요약끼리는 0%로 훨씬 뚜렷하게 갈린다.

    Jaccard가 아니라 오버랩 계수를 쓴다 — 실사고(2026-08-18): 한쪽 요약(경향신문,
    사직 배경·인사 절차까지 상세)이 다른 쪽 요약(조선일보, 발언 요지만 간략)을
    사실상 포함하는데도 분량 차이 때문에 Jaccard로는 13.2%(단어)/10.0%(문자)에
    그쳐 새어나갔다. 같은 데이터를 오버랩 계수로 재보면 38.5%/32.5%로 뚜렷이
    잡힌다(_overlap_coeff 참고).
    """
    best_wo, best_co, best_text = 0.0, 0.0, ""
    for item in recent:
        if item.get("category") != category:
            continue
        other = item.get("summary") or item["title"]
        wo = word_overlap(text, other)
        co = char_trigram_overlap(text, other)
        if wo > best_wo or co > best_co:
            if wo >= best_wo:
                best_wo = wo
            if co >= best_co:
                best_co = co
            best_text = other
    is_dup = (best_wo >= SUMMARY_WORD_OVERLAP_THRESHOLD
              or best_co >= SUMMARY_CHAR_TRIGRAM_OVERLAP_THRESHOLD)
    # 실사고(2026-08-20): 시황 속보(코스피/환율 등)는 문구 템플릿이 고정이라
    # 요약끼리도 겹쳐 보이지만 핵심 수치가 다르면 다른 시점의 다른 속보다 —
    # is_duplicate와 동일한 숫자 충돌 가드를 여기도 적용한다.
    if is_dup and _numbers_conflict(text, best_text):
        is_dup = False
    return is_dup, round(best_wo, 1), round(best_co, 1), best_text


# =========================
# FETCH
# =========================

# 실사고(2026-08-21): 연합뉴스TV "[속보] 코스피 1%대 하락…6,750선 출발"(장 시작
# 시점 기사, 실제 발행 00:06 UTC=09:06 KST)이 17:01 KST에야 채널에 발송됐다 —
# 발행 8시간 뒤에 "방금 속보"인 것처럼 나간 것(장이 이미 마감된 뒤였다). 구글
# 뉴스 검색 결과는 발행 시각 순이 아니라 관련도 순이라 오래된 기사가 뒤늦게
# 우리 폴링 결과에 들어올 수 있다(전에도 확인한 커버리지 특성). "속보"는 "지금
# 막 일어난 일"이라는 프레이밍 자체가 핵심이라, 이미 오래된 기사를 속보로 보내면
# 사실과 다른 인상을 준다. 발행 후 STALE_BREAKING_NEWS_HOURS를 넘은 [속보] 후보는
# 애초에 후보 목록에서 제외한다 — 단독/종합은 "방금 일어난 일"이라는 프레이밍이
# 없어 그대로 둔다(늦게라도 다루는 게 의미 있는 카테고리).
STALE_BREAKING_NEWS_HOURS = 2


def _article_age_hours(entry) -> float | None:
    """RSS published_parsed 기준 기사 나이(시간). 파싱 불가하면 None(필터링 안 함 — 판단 못 할
    땐 걸러내지 않는 쪽이 안전하다, 놓치는 것보다 낫다는 이 프로젝트의 원칙과 일관됨)."""
    published_parsed = entry.get("published_parsed")
    if not published_parsed:
        return None
    try:
        published_epoch = calendar.timegm(published_parsed)
    except (TypeError, ValueError, OverflowError):
        return None
    return (time.time() - published_epoch) / 3600


def fetch_candidates(category: str, url: str) -> list:
    feed = feedparser.parse(url, request_headers={"User-Agent": "Mozilla/5.0"})
    out = []
    for entry in feed.entries:
        raw_title = entry.get("title", "")
        if not TAG_RE[category].search(raw_title):
            continue  # 태그 없는 일반 단어 매칭("단독선두" 등) 배제
        if category == "속보":
            age = _article_age_hours(entry)
            if age is not None and age > STALE_BREAKING_NEWS_HOURS:
                print(f"  🕰️ 오래된 속보 제외({age:.1f}시간 전 발행): {raw_title[:40]}")
                continue
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

# 모델 5개 × 키 2개 = 최대 10번 시도, 요청당 타임아웃까지 겹치면 기사 1건에 몇 분씩
# 걸릴 수 있다(실사고 2026-08-17: 전체 실행이 8분 넘게 걸려 사용자가 직접 중단함).
# 기사 1건당 요약에 쓸 총 시간 예산을 두고, 넘으면 나머지 모델/키는 시도하지 않고
# 빈 요약으로 넘어간다(발송 자체는 막지 않는다).
GEMINI_TIMEOUT_SEC = 10       # 요청 1회당 타임아웃(기존 20초 → 단축, 느린 요청 빨리 포기)
GEMINI_TIME_BUDGET_SEC = 25   # 기사 1건의 요약 시도 전체에 쓰는 시간 상한


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


# 모델별로 RPD(429)가 소진된 키를 기록. FULL/LITE 두 목록의 합집합을 키로 둔다.
# run() 시작 시 state.json에 저장된 "오늘 이미 소진됨" 기록으로 시딩된다(하루 단위
# 유지) — 실행마다 프로세스가 새로 뜨는 GitHub Actions 환경이라 이걸 안 하면 RPD가
# 낮은 상위 모델(20/일)을 매 실행 헛되이 재시도하게 된다.
_ALL_GEMINI_MODELS = sorted(set(GEMINI_MODELS_FULL) | set(GEMINI_MODELS_LITE))
_exhausted_keys = {m: set() for m in _ALL_GEMINI_MODELS}
_current_key_idx = 0


def summarize_with_gemini(title: str, raw_text: str, category: str = "") -> str:
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
        # 3.x 계열은 "thinking" 모델이라 maxOutputTokens 예산을 답변 전에 내부 추론이
        # 먼저 소비한다. 실측(2026-08-17): 500으로는 사고에 481~714토큰을 다 써버려
        # MAX_TOKENS로 잘림. 1500에서부터 정상 STOP 확인, 여유를 두어 2048로 설정.
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2048},
    }

    n = len(GEMINI_API_KEYS)
    start = time.monotonic()
    for model in _models_for_category(category):
        if time.monotonic() - start > GEMINI_TIME_BUDGET_SEC:
            print(f"  ⏱️ 요약 시간 예산({GEMINI_TIME_BUDGET_SEC}초) 초과, 남은 모델 건너뜀")
            break
        exhausted = _exhausted_keys[model]
        available = [i for i in range(n) if i not in exhausted]
        if not available:
            continue
        ordered = sorted(available, key=lambda i: (i - _current_key_idx) % n)

        for idx in ordered:
            if time.monotonic() - start > GEMINI_TIME_BUDGET_SEC:
                print(f"  ⏱️ 요약 시간 예산({GEMINI_TIME_BUDGET_SEC}초) 초과, 남은 키 건너뜀")
                return ""
            try:
                res = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    params={"key": GEMINI_API_KEYS[idx]},
                    json=payload,
                    timeout=GEMINI_TIMEOUT_SEC,
                )
                if res.status_code == 429:
                    print(f"  ⚠️ Gemini({model}) 키 {idx+1} RPD 소진, 다른 키로 폴백")
                    exhausted.add(idx)
                    continue
                if res.status_code != 200:
                    print(f"  ⚠️ Gemini({model}) 키 {idx+1} HTTP {res.status_code}, 폴백")
                    continue
                candidates = res.json().get("candidates") or []
                if not candidates:
                    print(f"  ⚠️ Gemini({model}) 키 {idx+1} candidates 없음(안전 차단 등), 폴백")
                    continue
                cand = candidates[0]
                # MAX_TOKENS 등으로 잘린 응답은 버린다(끊긴 문장 발송 사고 방지).
                finish = cand.get("finishReason", "")
                if finish and finish != "STOP":
                    print(f"  ⚠️ Gemini({model}) 비정상 종료(finishReason={finish}), 폴백")
                    continue
                parts = cand.get("content", {}).get("parts") or []
                text = "".join(p.get("text", "") for p in parts).strip()
                # 실사고(2026-08-17): "빈 문자열만 출력해라" 지시를 안 지키고
                # "본문을 확인할 수 없어 빈 문자열을 출력한다" 같은 설명 문장 자체를
                # 답으로 낸 사례가 있었다. 그런 메타 발언은 진짜 요약이 아니므로
                # 빈 값과 동일하게 취급하고 다음 모델로 폴백한다.
                if text and _looks_like_refusal(text):
                    print(f"  ⚠️ Gemini({model}) 본문 미확인 메타응답, 폴백: {text[:40]}")
                    continue
                if text:
                    _current_key_idx = (idx + 1) % n
                    return text
            except Exception as e:
                print(f"  ⚠️ Gemini({model}) 키 {idx+1} 요약 실패: {e}")
                continue
    return ""


# 실사고(2026-08-18): "기사 본문 내용이 없어 요약할 수 없다."가 그대로 발송됨.
# "본문이 없"과는 "본문 내용이 없"처럼 사이에 다른 단어가 끼어 문자열 불일치.
# 개별 문구를 계속 추가하는 대신 "요약할 수 없"를 넣었다 — 진짜 요약문에는
# 나올 수 없는 표현이라 오탐 없이 이런 변형들을 폭넓게 잡는다.
_REFUSAL_MARKS = (
    "확인할 수 없", "찾을 수 없", "추출할 수 없", "포함되어 있지 않",
    "본문이 없", "제공되지 않", "판단할 수 없", "알 수 없",
    "요약할 수 없",
)

# 실사고(2026-08-19): 뉴스핌 속보 "北 섬멸적 보복" 기사의 본문 전체가
# "자세한 뉴스는 곧 전해질 예정이다." 한 줄뿐이었다. 이건 Gemini의 거부가
# 아니라 원문 자체가 아직 본기사가 안 나온 속보 자리채움 문구를 그대로
# 충실히 요약한 것 — 그래서 _REFUSAL_MARKS로는 못 잡는다. 이런 자리채움
# 문구가 본문의 전부/대부분이면 "요약"이라 부를 실질 정보가 없으므로 같은
# 방식(다음 모델로 폴백 → 다 실패하면 요약 없이 발송)으로 처리한다.
_STUB_ARTICLE_MARKS = (
    "곧 전해질 예정", "자세한 내용은 이어집니다", "속보로 전해드립니다",
    "계속 이어집니다",
)


def _looks_like_refusal(text: str) -> bool:
    """Gemini가 빈 문자열 대신 낸 '본문을 못 찾았다'류 메타 응답, 또는 원문 자체가
    아직 본기사 없는 속보 자리채움 문구인지 판정."""
    return any(mark in text for mark in _REFUSAL_MARKS) or any(mark in text for mark in _STUB_ARTICLE_MARKS)


# =========================
# TELEGRAM
# =========================

def send_telegram(item: dict) -> dict:
    emoji = TAG_LABEL_EMOJI.get(item["category"], "📰")
    title_safe = html.escape(item["title"])
    link = item.get("real_link") or item["link"]
    # 제목도 기사 링크로 건다(2026-08-17 사용자 결정) — 텔레그램 헤드라인 채널의
    # 일반적인 형태로, 제목 자체를 눌러도 원문으로 이동한다.
    title_linked = f'<a href="{link}">{title_safe}</a>'
    summary_block = f"\n\n{html.escape(item['summary'])}" if item.get("summary") else ""
    # 출처/채널 링크 둘 다 raw URL 노출 없이 단어에 걸고, 한 줄에 "|"로 붙인다.
    # 카테고리(속보/단독)는 이미 제목 태그에 있어 여기서 반복하지 않는다(2026-08-17 사용자 결정).
    footer_line = (
        f"\n\n📎 <a href=\"{link}\">출처</a> | "
        f"<a href=\"{CHANNEL_URL}\">{CHANNEL_TAG}</a>"
    )
    msg = (
        f"{emoji} {title_linked}"
        f"{summary_block}"
        f"{footer_line}"
    )
    # 속보는 빠른 팩트 전달이 목적이라 링크 미리보기(썸네일 사진)를 안 보여준다.
    # 단독은 심층 취재물이라 미리보기가 유용해 그대로 둔다(2026-08-17 사용자 결정).
    show_preview = item["category"] not in ("속보",)
    # 미리보기를 보여줄 땐 큰 사진 대신 작은 썸네일로(2026-08-21 사용자 요청) —
    # 구식 disable_web_page_preview 대신 신식 link_preview_options를 쓴다.
    # Bot API는 object 파라미터를 JSON 문자열로 받는다.
    link_preview_options = (
        {"prefer_small_media": True} if show_preview else {"is_disabled": True}
    )
    res = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
            "link_preview_options": json.dumps(link_preview_options),
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

    # 오늘 이미 RPD 소진된 것으로 확인된 모델/키는 이번 실행에서 아예 건너뛴다.
    seeded = load_gemini_exhausted(state)
    for model, idxs in seeded.items():
        if model in _exhausted_keys:
            _exhausted_keys[model].update(idxs)
    if seeded:
        print(f"[Gemini] 오늘 이미 소진된 것으로 기록된 모델: "
              f"{ {m: sorted(s) for m, s in seeded.items()} }")
    sent_guids = {s["guid"] for s in state["sent"]}

    # 카테고리별로 창 길이가 다르므로(DEDUP_WINDOW_HOURS_BY_CATEGORY), 항목 자신의
    # 카테고리 기준 컷오프를 각각 적용한다 — 하나의 전역 컷오프로는 표현 못 함.
    recent_for_dedup = [
        s for s in state["sent"]
        if s["sent_at"] >= (now_kst() - timedelta(hours=_dedup_window_hours(s.get("category", "")))).isoformat()
    ]

    # ── 1단계: 제목만으로 값싸게 걸러낸다(크롤링·Gemini 호출 없음) ──
    # 여기서 걸러지는 건 문구까지 거의 동일한 명백한 중복뿐이다(DUP_SKIP_THRESHOLD).
    # 실사고(2026-08-17): 이 루프가 recent_for_dedup에 추가를 안 해서, 같은 배치 안의
    # 후보끼리는 서로 비교가 안 되고 "과거 발송 이력"하고만 비교됐다. 그 결과 중앙일보·
    # 조선비즈가 완전히 동일한 제목("종합특검, '관저 이전 의혹' 김건희·윤한홍 기소")으로
    # 올라온 것도 안 걸러짐 — 같은 실행 안에서 뽑힌 후보끼리도 반드시 서로 비교해야 한다.
    # 개수 캡을 두지 않는다(2026-08-18 사용자 결정) — 뉴스 물량은 날마다 들쭉날쭉해서
    # 하루 100건 나오는 날도 2건만 나오는 날도 있는데, 개수로 캡을 걸면 많이 나오는
    # 날 진짜 새 기사가 캡에 밀려 사라진다(바로 위 실사고). 중복만 걸러내고 나머지는
    # 다 내보낸다 — 실행 시간이 늘어날 뿐 유실은 없다.
    stage1 = []
    review_log = []
    for category, url in GOOGLE_NEWS_QUERIES.items():
        candidates = fetch_candidates(category, url)
        print(f"[{category}] 후보 {len(candidates)}건 (태그 필터 통과분)")

        for c in candidates:
            if c["guid"] in sent_guids:
                continue  # 이미 처리한 기사(같은 기사 재수집)

            is_dup, score, matched = is_duplicate(c["title"], category, recent_for_dedup)
            if is_dup:
                print(f"  ⏭️  제목 중복 생략 ({score}%): {c['title'][:40]} ≈ {matched[:40]}")
                continue

            if DUP_REVIEW_THRESHOLD <= score < DUP_SKIP_THRESHOLD:
                review_log.append({"title": c["title"], "matched": matched, "score": score})

            stage1.append(c)
            # 이번 배치 안의 다른 후보와도 비교되도록 즉시 추가한다(위 실사고의 원인).
            recent_for_dedup.append({"title": c["title"], "category": category,
                                      "sent_at": now_kst().isoformat()})

    print(f"본문 확인 대상 {len(stage1)}건")

    # ── 2단계: 본문을 크롤링·요약해 진짜 중복인지 정확하게 판정한다 ──
    to_send = []
    processed = []  # 발송은 안 됐지만 재수집 방지를 위해 처리된 것으로 기록할 항목

    for c in stage1:
        category = c["category"]
        c["real_link"] = resolve_real_url(c["link"])

        domain = urlparse(c["real_link"]).netloc.replace("www.", "")
        if domain in EXCLUDE_LINK_DOMAINS:
            print(f"  🚫 제외 도메인({domain}): {c['title'][:40]}")
            processed.append({"guid": c["guid"], "title": c["title"], "category": category,
                               "sent_at": now_kst().isoformat()})
            log_decision(c["guid"], category, c["title"], c["real_link"], "excluded_domain")
            continue

        raw_text = crawl_article_text(c["real_link"])
        c["summary"] = summarize_with_gemini(c["title"], raw_text, category)
        compare_text = c["summary"] or c["title"]  # 요약 실패 시 제목으로라도 비교

        is_dup2, wj, ct, matched2 = is_summary_duplicate(compare_text, category, recent_for_dedup)
        if is_dup2:
            # 실사고(2026-08-20): 삼성전자 100조 주주환원 기사가 중복 판정을 받았는데,
            # 로그엔 항상 c['title']만 찍혀서 실제 비교에 쓰인 텍스트(요약 성공 시엔
            # summary, 실패 시엔 title)를 알 수 없어 원인 재구성이 불가능했다.
            # 실제로 비교된 compare_text와 매칭 상대(matched2)를 그대로 찍어서
            # 다음엔 바로 원인을 알 수 있게 한다. 요약 성공/실패 여부도 표시.
            # D1에도 전체(비절단) 텍스트를 남겨서 로그 90일 만료·40/80자 절단
            # 문제 없이 언제든 SQL로 조회할 수 있게 한다.
            label = "요약" if c["summary"] else "제목(요약실패)"
            print(f"  ⏭️  본문 중복 생략(단어 {wj}%/문자 {ct}%) [{label}]: "
                  f"{compare_text[:80]} ≈ {matched2[:80]}")
            processed.append({"guid": c["guid"], "title": c["title"], "category": category,
                               "sent_at": now_kst().isoformat()})
            log_decision(c["guid"], category, c["title"], c["real_link"], "dup_summary",
                         compare_text=compare_text, matched_text=matched2,
                         word_score=wj, char_score=ct)
            continue

        to_send.append(c)
        # 이번 실행 안에서 뽑은 기사끼리도 서로 비교 대상에 넣는다(요약 캐시 포함).
        recent_for_dedup.append({"title": c["title"], "summary": c["summary"], "category": category,
                                  "sent_at": now_kst().isoformat()})

    print(f"발송 대상 {len(to_send)}건")

    state["sent"].extend(processed)

    sent_count = 0
    for item in to_send:
        res = send_telegram(item)
        if res.get("ok"):
            sent_count += 1
            state["sent"].append({
                "guid": item["guid"], "title": item["title"], "summary": item.get("summary", ""),
                "category": item["category"], "sent_at": now_kst().isoformat(),
            })
            print(f"  ✅ [{item['category']}] {item['title'][:50]}")
            log_decision(item["guid"], item["category"], item["title"],
                         item.get("real_link") or item["link"], "sent",
                         compare_text=item.get("summary", ""), summary=item.get("summary", ""))
        else:
            print(f"  ❌ 발송 실패: {res}")
        time.sleep(SEND_INTERVAL_SEC)

    save_gemini_exhausted(state, _exhausted_keys)
    save_state(state)

    if review_log:
        print(f"\n⚠️ 애매한 제목 유사도({DUP_REVIEW_THRESHOLD}~{DUP_SKIP_THRESHOLD}%) 통과분 {len(review_log)}건 — 임계값 조정 참고용")
        for r in review_log:
            print(f"   {r['score']}% : {r['title'][:35]} ≈ {r['matched'][:35]}")

    print(f"\n[완료] 발송 {sent_count}건 / 전체 후보 처리 완료")


if __name__ == "__main__":
    run()
