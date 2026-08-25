"""
scripts/fetch_community.py
----------------------------
"뉴스 앤 이슈" 채널에 각종 커뮤니티의 베스트 게시글을 푸시한다(2026-08-25 사용자
요청 — "속보처럼 텔레그램에 바로 올리는 방식으로"로 시작했다가, 같은 날 "커뮤글의
경우는 사실 속도가 문제가 아니란말야"는 지적으로 방향이 바뀌었다 — 아래
"체크 주기와 다이제스트" 항목 참고).

fetch_news.py(뉴스 RSS)와는 소스 종류·판정 방식이 완전히 달라 별도 스크립트로
분리했다. 다만 사고방식은 비슷하다: 게시판이 이미 "베스트"로 골라준 목록을 그대로
따라가며, 이미 보낸 글(URL 기준)만 걸러낸다 — 다만 뉴스의 [속보]와 달리 "먼저
알리는 것"이 가치가 아니므로, 새 글을 즉시 개별 발송하지 않고 체크 주기마다 모아
다이제스트 하나로 보낸다.

**체크 주기와 다이제스트(2026-08-25, 방향 전환)**: 처음엔 뉴스처럼 워크플로가
트리거될 때마다(10분마다) 문턱값을 넘는 새 글을 그때그때 개별 메시지로 즉시
발송했다. 사용자가 "커뮤글의 경우는 사실 속도가 문제가 아니란말야. 뉴스랑은 좀
달라"라고 지적 — 커뮤 베스트글은 이미 한창 화제였던 글이라 몇 분~1시간 늦게
보내도 손해가 없다. "발송 간격을 늘리고 모아서 보내는 걸로"로 확정해 두 가지를
같이 바꿨다: (1) `COMMUNITY_CHECK_INTERVAL_MIN`(기본 60분) 동안은 이 스크립트가
실제로는 아무 것도 안 하고 바로 리턴한다(`run()`의 `last_run_at` 가드 — 워크플로
자체는 여전히 10분마다 돌지만 네트워크 요청 없이 즉시 스킵). (2) 그 체크 주기
동안 모인 후보를 개별 메시지 대신 `build_digest_chunks()`로 묶어 다이제스트
한 통(또는 길면 여러 통)으로 보낸다 — `send_digest_chunk()` 참고. 부수 효과로,
개별 발송을 여러 번 연달아 보낼 때 텔레그램 자체 미리보기 생성이 못 따라가던
문제(SEND_INTERVAL_SEC 옆 주석 참고)도 자연히 없어졌다 — 애초에 연속 발송을
안 하니까.

**소스 선정 기준(2026-08-24/25 조사)**: 무차별로 아무 커뮤니티나 넣지 않았다.
1) robots.txt의 `User-agent: *` 규칙(우리 스크립트에 실제로 적용되는 규칙)이
   목록 페이지를 막지 않을 것. `ClaudeBot`/`anthropic-ai`/`Claude-Web` 같은
   개별 봇 이름 차단은 그 이름으로 자기소개하는 크롤러에만 적용되는 규칙이라
   우리 스크립트(정직한 커스텀 UA, Gemini도 같이 쓰는 범용 수집기)에는 해당되지
   않는다 — `User-agent: *` 규칙만 우리에게 실제로 적용된다.
2) 로그인·JS 렌더·Cloudflare 봇 챌린지가 없을 것(인스티즈는 robots.txt 요청부터
   Cloudflare JS 챌린지 페이지가 와서 제외 — 우회하지 않는다).
3) 게시판 자체가 "베스트/개념글/HOT"으로 이미 걸러준 목록이면 소스 1로 분류한다.
   클리앙 모두의공원·인벤 오픈이슈·뽐뿌 자유게시판처럼 전체 최신글 목록만 있고
   베스트 필터가 없는 곳은(소스 2) 직접 문턱값(추천수 또는 조회수)을 넘는 새
   글만 보낸다 — 아래 "소스 2" 항목 참고. (2026-08-25까지) 소스 1은 이미
   "베스트"로 걸러진 목록이니 문턱값 없이 전부 보냈으나, 뉴스보다 훨씬 자주·
   많이 발송돼 채널이 커뮤니티로 도배되는 문제가 생겨("지금 보니 뉴스는 별로
   안나오고, 커뮤만 가득인데") 소스 1에도 문턱값을 추가했다 — 아래 SOURCES
   주석 참고.
4) (2026-08-25까지의 방침) 목록 페이지만 요청하고 개별 글은 크롤링하지 않았다.
   이후 사용자 지적으로 뒤집혔다 — "글만 있거나 영상으로 된 건 텔레그램 자동
   미리보기가 비어 있다"(og:image/description이 없는 글은 미리보기 카드가
   텅 빔). 그래서 지금은 발송 직전(문턱값까지 통과한 진짜 후보)에 한해서만
   개별 글을 크롤링해 요약을 붙인다 — 아래 "본문 요약과 콘텐츠 필터" 항목 참고.
   목록 자체를 훑는 단계(파서)는 여전히 목록 페이지만 본다.

**소스 1(네이티브 "베스트" 게시판, 그 안에서 다시 문턱값을 넘는 것만 발송,
2026-08-25부터) — 4곳**:
- 루리웹 베스트(`/best/all`) — robots.txt에 걸리는 쿼리 패턴(`orderby=` 등) 없이
  기본 URL 그대로 실시간 베스트가 나온다.
- 더쿠 HOT(`/hot`) — robots.txt가 404(규칙 없음), 목록 페이지 정상 응답.
- 디시인사이드 주식갤(`neostock`)·부동산갤(`immovables`) 개념글 — robots.txt의
  갤러리별 차단 14곳에 안 들어있다(부동산갤은 옛날 개별 글 2건만 차단, 목록은
  허용). `exception_mode=recommend`로 개념글(추천 컷 통과글)만 가져온다.

**소스 2(베스트 필터가 없는 전체 최신글 게시판, 문턱값 넘는 새 글만 발송,
2026-08-25 사용자 요청으로 추가) — 3곳**: 클리앙 모두의공원(`min_recommend`),
인벤 오픈이슈갤러리(`min_recommend`), 뽐뿌 자유게시판(`min_views` — 이 곳만
추천수 표기가 실측상 20건 중 1건 꼴로만 채워져 있어 조회수를 대신 쓴다). 각
파서 옆 주석에 실측 분포와 문턱값 근거가 있다 — 채널이 너무 시끄럽거나 너무
조용하면 SOURCES의 `min_recommend`/`min_views` 값을 조정할 것.

제외한 곳: 웃긴대학(robots.txt가 `User-agent: *`에도 `/board/best/`를 명시적으로
막음 — 봇 이름과 무관하게 모두에게 적용되는 규칙), 인스티즈(Cloudflare 봇 챌린지,
우회 안 함), DC인사이드의 다른 갤러리들(로봇 배제 목록에 있거나 이번엔 검토 안 함).

**첫 실행 부트스트랩**: 상태 파일이 비어 있는 첫 실행에 그 시점 베스트 목록
전체를 "새 글"로 보면 한 번에 수십 건이 쏟아진다. 그래서 소스별로 이번이 첫
수집이면(상태 파일에 해당 소스 기록이 전무하면) 발송하지 않고 현재 목록을
"이미 본 것"으로만 기록한다 — 다음 실행부터 진짜 신규 글만 실시간으로 나간다.
문턱값 미만인 글은 발송만 안 할 뿐 guid를 기록하지 않으므로(2026-08-25부터
소스 1도 동일), 나중에 추천·조회가 늘어 문턱값을 넘으면 그때 다시 후보로
잡혀 보내진다.

**본문 요약과 콘텐츠 필터**(2026-08-25 사용자 요청, 2단계, 같은 날 "19금은
최대한 제외하라니까"로 재차 강조 — 애매하면 통과가 아니라 차단 쪽으로 기운다):
1차로 `_is_risky_title()`이 제목에 붙은 경고 태그(약혐, 후방주의, 19금, 몰카
등 정식 표기 + "후방)"·"[혐]"처럼 괄호로 축약된 관용 표기)를 공짜로 즉시
거른다 — 이건 모든 후보에 적용된다.
2차는 진짜로 보낼 후보(문턱값까지 통과한 것)에만 적용된다: `crawl_post_text()`로
개별 글 페이지 본문을 가져오고(실패하면 빈 문자열), `summarize_and_check()`가
fetch_news.py와 같은 Gemini 키로 "위험 여부"와 "1문장 요약"을 한 번의 호출로
같이 받는다 — 예전엔 위험판정만 제목으로 따로 불렀는데, 이제 같은 호출에 본문을
실어 보내 위험판정 정확도도 올리고 쿼터도 아낀다. 크롤링·Gemini가 실패해도
fetch_news.py의 요약 실패 처리와 동일하게 (요약 없음, 위험 아님)으로 fail-open —
이 검사 하나 때문에 커뮤니티 발송 전체가 멈추면 안 된다. 걸러진 글도 guid는
기록해 다음 실행에서 재평가하지 않는다. 영상·이미지 위주라 요약할 텍스트가
없는 글은 요약이 빈 채로 발송된다(텔레그램 미리보기만으로 판단하게 됨) —
이것도 완벽한 해결책은 아니다.

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
# 다이제스트 청크 사이 대기(청크가 여러 개일 때만 씀 — 아래 COMMUNITY_CHECK_
# INTERVAL_MIN 참고). 원래는 개별 글마다 발송 간격으로 썼던 값인데(1.5초→3초
# 실험, 클리앙 미리보기가 연속 발송에 밀리는 문제 대응), 다이제스트로 바뀌면서
# 그 문제 자체가 없어졌다 — 메시지 한 통에 몰아 보내니 "연속 발송"이 아니다.
SEND_INTERVAL_SEC = 3.0
TELEGRAM_MAX_RETRIES = 3

# 커뮤글은 뉴스처럼 "먼저 알리는 게 가치"가 아니라는 사용자 지적(2026-08-25,
# "커뮤글의 경우는 사실 속도가 문제가 아니란말야") — 몇 분 늦게 보내도 손해가
# 없다. 그래서 10분마다 트리거되는 워크플로 안에서도 실제로는 이 주기마다만
# 체크하고, 나머지 실행에서는 네트워크 요청 없이 바로 건너뛴다(아래 run()의
# last_run_at 가드). 뉴스(fetch_news.py)는 그대로 10분마다 매번 돈다 — 빠른
# 소식이 가치인 쪽만 그 속도를 유지한다.
COMMUNITY_CHECK_INTERVAL_MIN = 60
# 이번 체크에서 모인 후보를 개별 메시지 대신 다이제스트 한 통(또는 길면 여러
# 통)으로 묶어 보낸다(2026-08-25 사용자 결정 — "발송 간격을 늘리고 모아서
# 보내는 걸로"). 텔레그램 메시지 상한은 4096자라 여유를 두고 이 값을 넘기
# 직전에 청크를 끊는다.
DIGEST_MAX_CHARS = 3500

# 상태 파일이 무한정 커지지 않도록 오래된 guid는 정리한다. 커뮤니티 베스트글은
# 뉴스보다 훨씬 빨리 순환하므로(대부분 하루 안에 목록에서 밀려남) 3일이면
# 재수집 방지 목적으로 충분하다.
RETENTION_HOURS = 72

# fetch_news.py와 같은 GEMINI_API_KEY(_2)를 그대로 쓴다(2026-08-25) — 같은 구글
# 계정 쿼터를 공유하므로 news 요약과 경합할 수 있지만, 별도 키를 새로 발급받는
# 것보다 지금은 이게 더 간단하다. 나중에 쿼터가 부족해지면 그때 키를 분리할 것.
GEMINI_API_KEYS = [k for k in [os.getenv("GEMINI_API_KEY"), os.getenv("GEMINI_API_KEY_2")] if k]
# 위험판정+1문장 요약을 한 번에 받는 가벼운 작업이라 lite 모델만 쓴다
# (fetch_news.py의 GEMINI_MODELS_LITE와 동일한 순서).
GEMINI_MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
GEMINI_TIMEOUT_SEC = 10

CRAWL_TIMEOUT_SEC = 8
CRAWL_MAX_CHARS = 4000  # LLM에 넘길 원문 상한(fetch_news.py CRAWL_MAX_CHARS와 동일)


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


# 성적/혐오/불법 소지가 있는 글은 거른다(2026-08-25 사용자 요청, "19금은 최대한
# 제외하라니까"로 재차 강조됨 — 애매하면 통과보다 차단 쪽으로 기운다). 이건 1차
# 필터라 모든 후보(문턱값 통과 여부와 무관)에 적용되고, 본문 크롤링 전에 제목
# 텍스트만으로 판단할 수 있는 신호로 즉시 걸러낸다 — 한국 커뮤니티는 관례적으로
# 이런 글에 작성자가 스스로 경고 태그를 붙인다는 점을 이용한다. 완벽한 필터는
# 아니다(제목에 신호가 없는 글은 못 거른다) — 2차로 summarize_and_check()가
# 본문까지 보고 한 번 더 거른다(발송 직전 후보에만).
_RISKY_TITLE_KEYWORDS = [
    "약혐", "혐주의", "혐오주의", "고어주의",
    "19금", "19禁", "후방주의", "노출주의",
    "성인물", "성인용", "야짤", "야동",
    "몰카", "몰래카메라", "도촬",
]
_RISKY_TITLE_RE = re.compile("|".join(re.escape(w) for w in _RISKY_TITLE_KEYWORDS))

# 위 목록은 "후방주의"처럼 정식 표기만 잡아서, "후방) 축 늘어진 유방.jpg"처럼
# 괄호로 축약된 실제 관용 표기를 놓쳤다(2026-08-25 실사고 — 이미지 위주 글이라
# 2차 Gemini 검사도 본문이 거의 없어 같이 놓침). 최초 발견 사례 하나만 문자열로
# 추가하는 대신, 한국 커뮤니티가 경고 태그를 여는 괄호 없이도 "단어)"·"[단어]"
# 형태로 붙이는 관례 자체를 정규식으로 잡는다 — 새로운 태그 표기가 또 나와도
# 이 패턴 하나로 커버된다.
_RISKY_TAG_ROOTS = ["후방", "약혐", "혐", "고어", "잔인", "선정", "몰카", "도촬", "노출", "19"]
_RISKY_TAG_RE = re.compile(
    "(?:" + "|".join(re.escape(w) for w in _RISKY_TAG_ROOTS) + r")\s*[)\]]"
)


def _is_risky_title(title: str) -> bool:
    return bool(_RISKY_TITLE_RE.search(title)) or bool(_RISKY_TAG_RE.search(title))


def crawl_post_text(url: str) -> str:
    """개별 글 페이지에서 본문 후보 텍스트를 긁어온다. fetch_news.py의
    crawl_article_text와 완전히 같은 전략(정교한 스코핑 대신 nav/header/footer/
    script류만 걷어내고 통째로 Gemini에 넘김) — 게시판마다 마크업이 다 달라
    정교하게 자르려 하면 오히려 엉뚱한 내용이 잡힌다는 게 뉴스 쪽에서 이미 실측
    확인된 교훈이라 그대로 재사용한다. 우리 정직한 커스텀 UA를 그대로 쓴다(목록
    fetch와 동일 — 개별 글이라고 브라우저를 사칭하지 않는다). 실패하면 빈
    문자열(발송 자체는 계속 진행, 요약 없이 나감)."""
    try:
        res = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=CRAWL_TIMEOUT_SEC)
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


def _parse_gemini_verdict(text: str) -> tuple[str, bool]:
    """"위험:예/아니오" 줄과 요약 줄을 파싱한다. "위험:" 줄을 못 찾으면
    안전하게 (요약 없음, 위험 아님)으로 처리 — 파싱 실패를 위험 판정 실패와
    똑같이 fail-open 시킨다."""
    lines = [ln.strip() for ln in text.strip().split("\n") if ln.strip()]
    for i, line in enumerate(lines):
        if line.startswith("위험"):
            risky = "예" in line
            summary = lines[i + 1] if not risky and i + 1 < len(lines) else ""
            return summary, risky
    return "", False


def summarize_and_check(title: str, raw_text: str) -> tuple[str, bool]:
    """본문(크롤링 성공 시) 또는 제목만으로 위험 판정과 1문장 요약을 한 번의
    Gemini 호출로 같이 받는다(2026-08-25 사용자 요청 — "글만 있거나 영상으로
    된 건 미리보기가 비어 있다"는 지적으로 본문 요약을 추가하면서, 기존에
    제목만 보던 위험판정도 같은 호출에 실어 정확도를 올리고 쿼터를 아꼈다).
    발송 직전(문턱값까지 통과한 후보)에만 호출한다. fetch_news.py의 요약 실패
    처리와 동일하게 실패 시 (요약 없음, 위험 아님)으로 fail-open — 이 검사
    하나 때문에 커뮤니티 발송 전체가 멈추면 안 된다."""
    if not GEMINI_API_KEYS:
        return "", False

    body_block = f"\n\n[본문 텍스트]\n{raw_text}" if raw_text else "\n\n(본문을 가져오지 못했다 — 제목만으로 판단해라)"
    prompt = (
        f"다음은 한국 커뮤니티 게시판 글이다.\n\n"
        f"제목: \"{title}\"{body_block}\n\n"
        "본문에는 광고, 댓글, 다른 글 목록 같은 잡음이 섞여 있을 수 있다. 다음 두 "
        "줄을 정확히 이 순서로 출력해라. 다른 말은 절대 덧붙이지 마라.\n"
        "1번째 줄: 이 글이 성적인 내용(노출, 몸매 품평·성적 대상화 포함), 혐오· "
        "잔인·엽기적인 내용, 몰카·도촬 등 불법 촬영물을 조금이라도 암시하면 "
        "\"위험:예\"라고 써라. 단, 단순히 정치적으로 민감하거나 논쟁적인 주제라는 "
        "이유만으로는 \"위험:예\"라고 하지 마라. 그 외에 판단이 애매하면 통과시키지 "
        "말고 \"위험:예\"라고 해라(19금 등 문제 소지가 있는 글은 최대한 걸러내는 "
        "쪽을 우선한다 — 2026-08-25 사용자 지시).\n"
        "2번째 줄: 본문 핵심을 1문장, 60자 안팎의 한국어로 해라체로 요약해라. 본문에서 "
        "확인되지 않는 내용은 추가하지 마라. 본문이 없거나(영상·이미지 위주 글) 요약할 "
        "내용이 없으면, 또는 1번째 줄이 \"위험:예\"면 이 줄은 빈 줄로 둬라."
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2048},
    }
    for model in GEMINI_MODELS:
        for key in GEMINI_API_KEYS:
            try:
                res = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    params={"key": key},
                    json=payload,
                    timeout=GEMINI_TIMEOUT_SEC,
                )
                if res.status_code != 200:
                    continue
                candidates = res.json().get("candidates") or []
                if not candidates:
                    continue
                cand = candidates[0]
                if cand.get("finishReason", "") not in ("STOP", ""):
                    continue
                parts = cand.get("content", {}).get("parts") or []
                text = "".join(p.get("text", "") for p in parts).strip()
                if text:
                    return _parse_gemini_verdict(text)
            except Exception:
                continue
    return "", False


# =========================
# 소스별 파서
# =========================
# 각 파서는 (title, url, views, comments, recommend) 튜플의 리스트를 반환한다.
# 얻을 수 없는 지표는 None — 임의로 만들어내지 않는다(추정값을 넣으면 나중에
# 진짜 값과 헷갈린다). recommend는 소스 2(베스트 필터 없는 게시판)의 문턱값
# 판정에 쓰인다 — SOURCES의 min_recommend/min_views 참고.

_RULIWEB_ROW_RE = re.compile(
    r'<tr class="table_body[^"]*">(?P<row>.*?)</tr>', re.DOTALL)
_RULIWEB_LINK_RE = re.compile(
    r'<a class="subject_link[^"]*"\s+href="(?P<href>/best/board/[^"]+)">(?P<inner>.*?)</a>', re.DOTALL)
# 상위 4위(`best_top_row`)는 <strong class="text_over">, 5위 이하(`mode_list`)는
# <span class="text_over">로 마크업이 다르다 — 실측으로 확인.
_RULIWEB_TITLE_RE = re.compile(r'<(?:strong|span) class="text_over">(?P<title>.*?)</(?:strong|span)>', re.DOTALL)
_RULIWEB_HIT_RE = re.compile(r'<td class="hit">\s*([\d,]+)\s*</td>')
_RULIWEB_REPLY_RE = re.compile(r'\((\d+)\)')


_RULIWEB_RECOMD_RE = re.compile(r'<td class="recomd">\s*([\d,]+)\s*</td>')


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
        recomd_m = _RULIWEB_RECOMD_RE.search(row)
        if not title:
            continue
        url = "https://bbs.ruliweb.com" + link_m.group("href")
        items.append((
            title,
            url,
            _to_int(hit_m.group(1)) if hit_m else None,
            int(reply_m.group(1)) if reply_m else None,
            _to_int(recomd_m.group(1)) if recomd_m else None,
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
            None,  # 더쿠 HOT 목록엔 추천수가 없다
        ))
    return items


_DC_ROW_RE = re.compile(
    r'<tr class="ub-content us-post"[^>]*data-no="(?P<no>\d+)"[^>]*>(?P<row>.*?)</tr>', re.DOTALL)
_DC_TITLE_CELL_RE = re.compile(r'<td class="gall_tit[^"]*">(?P<cell>.*?)</td>', re.DOTALL)
_DC_LINK_RE = re.compile(r'<a\s+href="(?P<href>/board/view/\?[^"]+)"[^>]*>(?P<inner>.*?)</a>', re.DOTALL)
_DC_REPLY_RE = re.compile(r'class="reply_num">\[?(\d+)\]?<')
_DC_COUNT_RE = re.compile(r'<td class="gall_count">([\d,]+|-)</td>')
_DC_RECOMMEND_RE = re.compile(r'<td class="gall_recommend">([\d,]+|-)</td>')


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
        recommend_m = _DC_RECOMMEND_RE.search(row)
        url = "https://gall.dcinside.com" + html.unescape(link_m.group("href"))
        items.append((
            title,
            url,
            _to_int(count_m.group(1)) if count_m else None,
            int(reply_m.group(1)) if reply_m else None,
            _to_int(recommend_m.group(1)) if recommend_m else None,
        ))
    return items


_CLIEN_OPEN_RE = re.compile(
    r'<div class="list_item symph_row[^"]*"[^>]*data-board-sn=(?P<sn>\d+)[^>]*data-comment-count=(?P<comment>\d+)')
_CLIEN_TITLE_RE = re.compile(r'<span class="subject_fixed"[^>]*title="(?P<title>[^"]*)"')
_CLIEN_HIT_RE = re.compile(r'<div class="list_hit">\s*<span class="hit">([\d,]+)</span>', re.DOTALL)
_CLIEN_LIKE_RE = re.compile(r'data-role="list-like-count"><span>(\d+)</span>')


def parse_clien(html_text: str) -> list:
    """클리앙 모두의공원. `div`는 `<tr>`처럼 명확한 닫는 태그로 한 행을 자를 수
    없어서(중첩 div가 많음), 여는 태그(data-board-sn·data-comment-count가 이미
    거기 있음)의 위치를 기준으로 다음 글 시작 전까지를 한 행으로 슬라이스한다.
    공지(`list_item notice`)·광고(`list_item hongbo`)는 `symph_row` 클래스와
    `data-board-sn` 속성이 없어 이 정규식에서 자연히 제외된다."""
    items = []
    opens = list(_CLIEN_OPEN_RE.finditer(html_text))
    for i, m in enumerate(opens):
        end = opens[i + 1].start() if i + 1 < len(opens) else len(html_text)
        row = html_text[m.start():end]
        title_m = _CLIEN_TITLE_RE.search(row)
        if not title_m:
            continue
        title = html.unescape(title_m.group("title")).strip()
        if not title:
            continue
        hit_m = _CLIEN_HIT_RE.search(row)
        like_m = _CLIEN_LIKE_RE.search(row)
        url = f"https://www.clien.net/service/board/park/{m.group('sn')}"
        items.append((
            title,
            url,
            _to_int(hit_m.group(1)) if hit_m else None,
            int(m.group("comment")),
            int(like_m.group(1)) if like_m else None,
        ))
    return items


_INVEN_ROW_RE = re.compile(r'<tr class="(?P<cls>[^"]*)">(?P<row>.*?)</tr>', re.DOTALL)
_INVEN_LINK_RE = re.compile(
    r'<a class="subject-link" href="(?P<href>https://www\.inven\.co\.kr/board/webzine/2097/\d+)">(?P<inner>.*?)</a>',
    re.DOTALL)
_INVEN_COMMENT_RE = re.compile(r'class="con-comment">\[(\d+)\]')
_INVEN_VIEW_RE = re.compile(r'<td class="view">([\d,]+)</td>')
_INVEN_RECO_RE = re.compile(r'<td class="reco">(\d+)</td>')


def parse_inven(html_text: str) -> list:
    """인벤 오픈이슈갤러리. 공지 행은 `<tr class="notice all">`라 class 속성에
    "notice"가 있으면 제외한다(실제 글 행은 `<tr class="">`)."""
    items = []
    for m in _INVEN_ROW_RE.finditer(html_text):
        if "notice" in m.group("cls"):
            continue
        row = m.group("row")
        link_m = _INVEN_LINK_RE.search(row)
        if not link_m:
            continue
        title = _strip_tags(link_m.group("inner"))
        if not title:
            continue
        comment_m = _INVEN_COMMENT_RE.search(row)
        view_m = _INVEN_VIEW_RE.search(row)
        reco_m = _INVEN_RECO_RE.search(row)
        items.append((
            title,
            link_m.group("href"),
            _to_int(view_m.group(1)) if view_m else None,
            int(comment_m.group(1)) if comment_m else None,
            int(reco_m.group(1)) if reco_m else None,
        ))
    return items


_PPOMPPU_ROW_RE = re.compile(r'<tr align="center" class="baseList ">(?P<row>.*?)</tr>', re.DOTALL)
_PPOMPPU_TITLE_RE = re.compile(
    r'<a class="baseList-title[^"]*"\s+href="(?P<href>view\.php\?[^"]+)"[^>]*><span>(?P<title>.*?)</span></a>',
    re.DOTALL)
_PPOMPPU_COMMENT_RE = re.compile(r'class="baseList-c"[^>]*>(\d+)</span>')
_PPOMPPU_VIEWS_RE = re.compile(r'baseList-views"\s*colspan="2">([\d,]*)</td>')
_PPOMPPU_REC_RE = re.compile(r'baseList-rec"\s*colspan="2">([\d,]*)</td>')


def parse_ppomppu(html_text: str) -> list:
    """뽐뿌 자유게시판. 추천수(`baseList-rec`)는 실측상 20건 중 1건 꼴로만 채워져
    있어(일정 추천을 넘겨야 표시되는 듯) 임계값 신호로 못 쓴다 — 대신 항상 채워지는
    조회수를 임계값 신호로 쓴다(SOURCES의 min_views 참고)."""
    items = []
    for m in _PPOMPPU_ROW_RE.finditer(html_text):
        row = m.group("row")
        title_m = _PPOMPPU_TITLE_RE.search(row)
        if not title_m:
            continue
        title = _strip_tags(title_m.group("title"))
        if not title:
            continue
        comment_m = _PPOMPPU_COMMENT_RE.search(row)
        views_m = _PPOMPPU_VIEWS_RE.search(row)
        rec_m = _PPOMPPU_REC_RE.search(row)
        url = "https://www.ppomppu.co.kr/zboard/" + title_m.group("href")
        items.append((
            title,
            url,
            _to_int(views_m.group(1)) if views_m else None,
            int(comment_m.group(1)) if comment_m else None,
            _to_int(rec_m.group(1)) if rec_m else None,
        ))
    return items


# 소스 1(네이티브 "베스트" 게시판) 4곳도 2026-08-25까지는 문턱값 없이 베스트
# 목록에 오른 새 글을 전부 보냈는데, 뉴스보다 훨씬 자주·많이 발송돼 채널이
# 커뮤니티로 도배되는 문제가 생겼다("지금 보니 뉴스는 별로 안나오고, 커뮤만
# 가득인데" — 2026-08-25). 사용자가 "소스 1도 문턱값 적용"을 선택해, 이미
# "베스트"로 걸러진 목록이라도 그 안에서 다시 상위 절반 정도만 추리도록
# 문턱값을 추가했다. 값은 그날 실측 분포(라이브 fetch 후 정렬)로 대략 중앙값
# 근처를 잡았다 — 더쿠는 목록에 추천수 필드가 아예 없어(parse_theqoo 참고)
# 조회수를 대신 쓴다.
#   루리웹 베스트: 추천수 [4,6,6,6,6,6,6,7,8,10,12,12,13,14,16,17,19,20,21,23,
#     25,25,27,33,37,47,48,74,90,104,114] (31건) → 20 이상만(14건, ~45%)
#   더쿠 HOT: 조회수 [3846~66704] (20건, 추천수 없음) → 35000 이상만(10건, 50%)
#   디시 주식갤 개념글: 추천수 [49~193] (50건) → 100 이상만(27건, ~54%)
#   디시 부동산갤 개념글: 추천수 [11~151] (49건) → 40 이상만(25건, ~51%)
SOURCES = [
    {
        "id": "ruliweb",
        "name": "루리웹 베스트",
        "emoji": "🔵",
        "url": "https://bbs.ruliweb.com/best/all",
        "parse": parse_ruliweb,
        "min_recommend": 20,
    },
    {
        "id": "theqoo",
        "name": "더쿠 HOT",
        "emoji": "🟣",
        "url": "https://theqoo.net/hot",
        "parse": parse_theqoo,
        "min_views": 35000,
    },
    {
        "id": "dc_neostock",
        "name": "디시 주식갤 개념글",
        "emoji": "🟢",
        "url": "https://gall.dcinside.com/board/lists/?id=neostock&exception_mode=recommend",
        "parse": parse_dcinside,
        "min_recommend": 100,
    },
    {
        "id": "dc_immovables",
        "name": "디시 부동산갤 개념글",
        "emoji": "🟢",
        "url": "https://gall.dcinside.com/board/lists/?id=immovables&exception_mode=recommend",
        "parse": parse_dcinside,
        "min_recommend": 40,
    },
    # 아래 3곳은 사이트 자체에 "베스트" 게시판이 없다(전체 최신글 목록만 있음) —
    # 2026-08-25 사용자 요청으로 추가하면서, 직접 문턱값(추천수 또는 조회수)을
    # 넘는 새 글만 보내도록 했다. 문턱값은 실측 분포(각 소스 파서 옆 주석 참고)로
    # 대략 잡은 초깃값이라, 채널이 너무 시끄럽거나 너무 조용하면 조정이 필요하다.
    {
        "id": "clien",
        "name": "클리앙 모두의공원",
        "emoji": "🟠",
        "url": "https://www.clien.net/service/board/park",
        "parse": parse_clien,
        "min_recommend": 5,
    },
    {
        "id": "inven",
        "name": "인벤 오픈이슈갤러리",
        "emoji": "🟠",
        "url": "https://www.inven.co.kr/board/webzine/2097",
        "parse": parse_inven,
        "min_recommend": 5,
    },
    {
        "id": "ppomppu",
        "name": "뽐뿌 자유게시판",
        "emoji": "🟠",
        "url": "https://www.ppomppu.co.kr/zboard/zboard.php?id=freeboard",
        "parse": parse_ppomppu,
        "min_views": 500,
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

def _format_digest_item(emoji: str, title: str, url: str, summary: str) -> str:
    """다이제스트 한 줄(글 하나) 포맷. 개별 발송 시절의 footer("출처" 링크)는
    뺐다 — 제목 자체가 이미 그 글로 가는 링크라 중복이다. [커뮤] 태그는
    유지한다(뉴스와 구분하는 공통 카테고리 태그라는 취지는 다이제스트에서도
    그대로 적용됨)."""
    title_safe = html.escape(title)
    title_linked = f'<a href="{url}">{title_safe}</a>'
    summary_line = f"\n{html.escape(summary)}" if summary else ""
    return f"{emoji} [커뮤] {title_linked}{summary_line}"


def build_digest_chunks(candidates: list) -> list:
    """후보 목록(dict: guid/source/emoji/title/url/summary)을 텔레그램 메시지
    상한(DIGEST_MAX_CHARS)을 넘지 않는 청크로 묶는다. 순수 함수라 네트워크
    없이 테스트 가능 — 반환값은 [(메시지 텍스트, 그 청크에 들어간 후보 리스트), ...].
    보통은 청크가 1개뿐이지만(체크 주기가 1시간이라 후보 수가 적음), 드물게
    넘치면 여러 통으로 나눠 보낸다."""
    chunks = []
    current_items: list = []
    current_lines: list = []
    for cand in candidates:
        line = _format_digest_item(cand["emoji"], cand["title"], cand["url"], cand["summary"])
        header = f"🗂 <b>커뮤니티 베스트 모음</b> ({len(current_items) + 1}건)"
        projected = header + "\n\n" + "\n\n".join(current_lines + [line])
        if current_lines and len(projected) > DIGEST_MAX_CHARS:
            chunks.append((_finalize_digest_chunk(current_lines), current_items))
            current_lines = [line]
            current_items = [cand]
        else:
            current_lines.append(line)
            current_items.append(cand)
    if current_lines:
        chunks.append((_finalize_digest_chunk(current_lines), current_items))
    return chunks


def _finalize_digest_chunk(lines: list) -> str:
    header = f"🗂 <b>커뮤니티 베스트 모음</b> ({len(lines)}건)"
    return header + "\n\n" + "\n\n".join(lines)


def send_digest_chunk(text: str) -> dict:
    """다이제스트 청크 하나를 발송한다. 링크가 여러 개 섞이므로 미리보기는
    아예 끈다(is_disabled) — 개별 발송 때처럼 특정 글 하나만 썸네일로 띄우면
    나머지 글과 형평이 안 맞고 헷갈린다."""
    data = {
        "chat_id": CHAT_ID,
        "text": text,
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
            print(f"  ⏳ 429(재시도 {attempt+1}/{TELEGRAM_MAX_RETRIES}), {retry_after}초 대기")
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

    # 커뮤글은 뉴스처럼 "먼저 알리는 게 가치"가 아니다(2026-08-25 사용자 지적) —
    # 워크플로 자체는 10분마다 트리거되지만, 여기서 마지막 체크 이후 경과 시간을
    # 보고 COMMUNITY_CHECK_INTERVAL_MIN(60분) 미만이면 네트워크 요청 없이 바로
    # 건너뛴다. last_run_at이 없으면(첫 실행) 그냥 진행한다.
    last_run_at = state.get("last_run_at")
    if last_run_at:
        elapsed_min = (now_kst() - datetime.fromisoformat(last_run_at)).total_seconds() / 60
        if elapsed_min < COMMUNITY_CHECK_INTERVAL_MIN:
            print(f"[SKIP] 직전 체크 {elapsed_min:.0f}분 전 — {COMMUNITY_CHECK_INTERVAL_MIN}분 주기라 이번엔 건너뜀")
            return

    sent_guids = {s["guid"] for s in state["sent"]}
    # 소스별로 "이번이 첫 수집인가"를 판단하기 위해 이미 알고 있는 소스 id를 모은다.
    known_source_ids = {s.get("source") for s in state["sent"]}

    new_entries = []
    # 문턱값·필터를 다 통과한 진짜 후보만 여기 모은다 — 개별 발송 대신 이번
    # 체크가 끝난 뒤 한꺼번에 다이제스트로 묶어 보낸다(2026-08-25 사용자 결정).
    digest_candidates = []

    for source in SOURCES:
        items = fetch_source(source)
        print(f"[{source['name']}] {len(items)}건 수집")

        is_bootstrap = source["id"] not in known_source_ids
        if is_bootstrap and items:
            print(f"  🌱 {source['name']} 첫 수집 — 발송 없이 기준선만 기록")

        min_recommend = source.get("min_recommend")
        min_views = source.get("min_views")

        for title, url, views, comments, recommend in items:
            guid = hashlib.md5(url.encode("utf-8")).hexdigest()
            if guid in sent_guids:
                continue

            if _is_risky_title(title):
                # 발송은 안 하되 매 실행마다 다시 걸러내지 않도록 "본 것"으로는
                # 기록한다 — bootstrap과 동일하게 처리.
                new_entries.append({"guid": guid, "source": source["id"], "sent_at": now_kst().isoformat()})
                sent_guids.add(guid)
                print(f"  🚫 필터링: {title[:50]}")
                continue

            if is_bootstrap:
                new_entries.append({"guid": guid, "source": source["id"], "sent_at": now_kst().isoformat()})
                sent_guids.add(guid)
                continue

            # min_recommend/min_views가 설정된 소스는(2026-08-25부터 7곳 전부)
            # 문턱값 미만이면 그냥 건너뛴다 — sent_guids에 기록하지 않으므로
            # 나중에 문턱값을 넘으면(추천·조회가 계속 늘어) 그때 다시 후보로
            # 잡혀 보내진다.
            if min_recommend is not None and (recommend is None or recommend < min_recommend):
                continue
            if min_views is not None and (views is None or views < min_views):
                continue

            # 여기까지 온 건 진짜로 보낼 후보뿐이다 — 크롤링·Gemini 호출을 발송
            # 직전으로 미뤄서 문턱값 미달로 어차피 안 보낼 글에는 요청을 안 보낸다.
            raw_text = crawl_post_text(url)
            summary, risky = summarize_and_check(title, raw_text)
            if risky:
                new_entries.append({"guid": guid, "source": source["id"], "sent_at": now_kst().isoformat()})
                sent_guids.add(guid)
                print(f"  🚫 Gemini 필터링: {title[:50]}")
                continue

            digest_candidates.append({
                "guid": guid, "source": source["id"], "emoji": source["emoji"],
                "title": title, "url": url, "summary": summary,
            })

        time.sleep(SOURCE_DELAY_SEC)

    sent_count = 0
    if digest_candidates:
        chunks = build_digest_chunks(digest_candidates)
        for i, (text, chunk_items) in enumerate(chunks):
            res = send_digest_chunk(text)
            if res.get("ok"):
                sent_count += len(chunk_items)
                for item in chunk_items:
                    new_entries.append({
                        "guid": item["guid"], "source": item["source"], "sent_at": now_kst().isoformat(),
                    })
                    sent_guids.add(item["guid"])
                print(f"  ✅ 다이제스트 청크 {i + 1}/{len(chunks)} 발송 ({len(chunk_items)}건)")
            else:
                # 실패한 청크의 후보는 guid를 기록하지 않는다 — 다음 체크에서
                # 다시 후보로 잡혀 재시도된다.
                print(f"  ❌ 다이제스트 청크 {i + 1}/{len(chunks)} 발송 실패: {res}")
            if i < len(chunks) - 1:
                time.sleep(SEND_INTERVAL_SEC)
    else:
        print("  (이번 체크에서 새로 보낼 커뮤글 없음)")

    state["sent"].extend(new_entries)
    state["last_run_at"] = now_kst().isoformat()
    save_state(state)

    print(f"\n[완료] 발송 {sent_count}건 / 신규 기록 {len(new_entries)}건")


if __name__ == "__main__":
    run()
