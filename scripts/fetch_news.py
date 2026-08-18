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
        로컬로 이식한 것. ⚠️ 같은 배치(이번 실행) 안에서 뽑힌 후보끼리도 반드시
        recent_for_dedup에 누적하며 비교해야 한다 — 안 그러면 "과거 발송 이력"하고만
        비교돼서 같은 배치 안의 완전 동일 제목도 못 잡는다(실사고 2026-08-17: 중앙일보·
        조선비즈가 100% 동일 제목 "종합특검, '관저 이전 의혹' 김건희·윤한홍 기소"로
        나란히 발송됨).
      2단계 — 1단계를 통과한 후보만 본문을 크롤링·Gemini 요약한 뒤, 그 요약끼리
        비교한다(is_summary_duplicate). 제목만으로는 "같은 사건, 다른 표현"과
        "같은 사건, 다른 후속 사실"을 구분하지 못한다 — 실측(2026-08-17): 문화일보·
        서울신문이 거의 동일 보도한 "잠실 호텔 탄창 발견"은 제목 유사도 13%로 안
        걸렸지만 요약끼리는 48.5%로 뚜렷이 잡혔다. 반대로 "김민석 인선" vs "김민석
        당선"처럼 완전히 다른 사건은 요약 유사도가 0%로 나와, 제목 대신 요약 비교로
        바꾸면서 개수 캡(주제당 N건) 없이도 진짜 중복만 정확히 거를 수 있게 됐다
        (캡 방식은 폐기 — 중요한 후속 속보가 개수 제한에 걸려 묻히는 부작용이 있었다).
        비교 지표는 Jaccard가 아니라 오버랩 계수(교집합/작은쪽 집합 크기)를 쓴다 —
        실사고(2026-08-18): 경향신문(사직 배경·인사 절차까지 상세)과 조선일보(발언
        요지만 간략)처럼 두 매체 요약 분량이 크게 다르면, 짧은 쪽에 없는 단어까지
        분모(합집합)에 들어가는 Jaccard는 진짜 중복인데도 13.2%/10.0%로 낮게 나와
        임계값을 통과시켰다. 오버랩 계수는 같은 데이터에서 38.5%/32.5%로 뚜렷이
        잡힌다(word_overlap/char_trigram_overlap 참고).
      (Supabase/Gemini 통합 재작성 단계는 여기선 안 씀 — 우리는 자체 기사를 쓰는 게 아니라
       원문 링크를 그대로 전달하는 큐레이션이라 "통합"이 아니라 "생략"이 맞는 대응이다)
링크: 구글 뉴스 링크는 news.google.com을 거치는 리다이렉트라 googlenewsdecoder로
      실제 언론사 URL을 먼저 알아내 그쪽을 보여준다.
본문 요약: 정규식으로 "첫 문단"을 고르는 방식은 매체마다 다른 위젯 텍스트(읽어주기
      서비스, 댓글 안내 등)를 본문으로 오인해 반복적으로 실패했다(다음뉴스·연합뉴스TV·
      KBS 3건 실사고). 규칙 기반 대신 크롤링한 원문(노이즈 섞여도 무방)을 통째로 Gemini에
      넘겨 핵심만 2문장으로 뽑게 한다 — LLM은 광고/위젯 텍스트를 의미로 걸러내므로 정규식
      보다 안정적이다. 이제 모든 카테고리(속보 포함)에서 요약을 만든다 — 중복판정에
      쓰일 뿐 아니라 채널에도 함께 보여준다. 요약 실패 시(키 없음/쿼터 초과/Gemini의
      "본문을 확인할 수 없다"류 메타응답 등) 조용히 건너뛰고 제목/출처/링크만 보낸다
      (발송 자체는 막지 않는다). 메타응답 판정은 _REFUSAL_MARKS 참고 — 문구를
      하나씩 추가하는 화이트리스트 방식이라 완벽하지 않다(실사고 2026-08-18:
      "본문 내용이 없어 요약할 수 없다"가 "본문이 없" 마크와 문자열 불일치로 새서
      그대로 발송됨 → "요약할 수 없" 같은 상위 패턴을 추가해 보강). 그와 별개로
      _STUB_ARTICLE_MARKS도 있다 — Gemini가 거부한 게 아니라 원문 자체가 "자세한
      뉴스는 곧 전해질 예정이다" 같은 속보 자리채움 문구뿐이라 충실히 그대로
      요약해버린 경우를 잡는다(실사고 2026-08-19, 뉴스핌).
모델 라우팅(2026-08-17): 단독만 상위 모델(3.7→3.6→3.5→lite)까지 다 시도하고,
      속보·종합은 처음부터 라이트 모델로 직행한다. 물량이 많은 속보·종합이 자주
      실패하는 상위 모델(503/타임아웃)에서 시간을 허비하지 않게 하는 동시에,
      상위 모델 RPD를 물량 적은 단독에 집중해서 아낀다. (2026-08-18 재확인: 캡을
      없애 속보·종합 물량도 늘었지만, 이 라우팅 자체는 "단독만 상위 모델"로 유지하기로
      사용자가 재확인함 — 속보·종합까지 상위 모델을 태우면 다시 시간을 잡아먹는다.)
RPD 소진 상태 영속화(2026-08-17): 상위 모델(3.7/3.6/3.5) RPD가 실측 20/일로 매우
      낮아 하루 초반에 바로 소진되는데, GitHub Actions는 실행마다 파이썬 프로세스가
      새로 뜨는 환경이라 "오늘 이미 소진됐다"는 기억이 실행마다 사라져 매번 헛되이
      상위 모델부터 재시도하고 있었다(실측: 단독 13건 중 5건이 요약 없이 발송됨).
      state.json에 날짜와 함께 저장해두고 재사용한다(load_gemini_exhausted /
      save_gemini_exhausted) — 날짜가 바뀌면 초기화된다.
개수 캡 없음(2026-08-18): 원래 속보/단독/종합을 합쳐 MAX_SUMMARIZE_PER_RUN=20 /
      MAX_SEND_PER_RUN=15로 캡을 걸었었다. GOOGLE_NEWS_QUERIES가 속보→단독→종합
      순으로 도는데, 밤 시간대 정치 속보가 몰리면(실측: 어느 시점 속보 태그 매칭
      103건, 단독 84건) 속보만으로 전체 캡을 다 채워버려 단독이 통째로 밀려났다
      — 워크플로가 "성공"으로 끝났는데도 채널엔 아무것도 안 올라온 사고가 발생
      (구글 뉴스엔 "국정원", "한미협의" 단독이 떠 있었는데 캡에 밀려 후보 목록에
      들지도 못함). 카테고리별로 캡을 나누는 방안도 검토했지만, 사용자가 "하루에
      100개가 나올 수도 있고 2개가 나올 수도 있는데 개수로 캡을 걸면 많이 나오는
      날은 나오다 만다"며 캡 자체를 없애기로 결정. 이제 중복만 걸러내고 나머지는
      다 내보낸다 — 물량이 많은 날은 실행 시간이 늘어날 뿐 기사가 유실되지는
      않는다(GitHub Actions 타임아웃에 걸려도 그때까지 처리분은 캐시에 저장되고,
      못 보낸 나머지는 다음 실행에서 다시 후보로 떠서 이어 처리된다).

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
DEDUP_WINDOW_HOURS = 6      # 이 시간 안에 보낸 기사만 비교 대상으로 삼는다

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


def _keywords(title: str) -> set:
    """제목에서 태그·불용어를 뺀 2글자 이상 단어 토큰 집합. 주제 포화 판정용."""
    t = re.sub(r"[\[\【][^\]\】]*[\]\】]", "", title or "")
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
    return is_dup, round(best_wo, 1), round(best_co, 1), best_text


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
    res = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": not show_preview,
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

    dedup_cutoff = (now_kst() - timedelta(hours=DEDUP_WINDOW_HOURS)).isoformat()
    recent_for_dedup = [s for s in state["sent"] if s["sent_at"] >= dedup_cutoff]

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
            continue

        raw_text = crawl_article_text(c["real_link"])
        c["summary"] = summarize_with_gemini(c["title"], raw_text, category)
        compare_text = c["summary"] or c["title"]  # 요약 실패 시 제목으로라도 비교

        is_dup2, wj, ct, matched2 = is_summary_duplicate(compare_text, category, recent_for_dedup)
        if is_dup2:
            print(f"  ⏭️  본문 중복 생략(단어 {wj}%/문자 {ct}%): {c['title'][:40]} ≈ {matched2[:40]}")
            processed.append({"guid": c["guid"], "title": c["title"], "category": category,
                               "sent_at": now_kst().isoformat()})
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
