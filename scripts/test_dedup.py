"""
scripts/test_dedup.py
----------------------
fetch_news.py의 중복판정·메타응답 필터 회귀 테스트.

실사고 대응 코드를 고칠 때마다 매번 Bash로 즉석 검증 스니펫을 새로 짜던 걸
막기 위해 만들었다 — 지금까지 실제로 겪은 사고 사례를 그대로 테스트 케이스로
남겨뒀다. fetch_news.py의 임계값(SUMMARY_WORD_OVERLAP_THRESHOLD 등)을 조정할
때는 이 파일을 먼저 돌려서 기존에 맞던 사례가 깨지지 않는지 확인한다.

실행: python scripts/test_dedup.py
(pytest 없이도 돌아가도록 plain assert로 작성 — 이 프로젝트엔 pytest가
requirements.txt에 없다.)
"""

import sys
import time

from fetch_news import (
    is_duplicate,
    is_summary_duplicate,
    _looks_like_refusal,
    word_overlap,
    char_trigram_overlap,
    _article_age_hours,
    STALE_BREAKING_NEWS_HOURS,
    _dedup_window_hours,
    _is_summary_borderline,
    SUMMARY_WORD_OVERLAP_THRESHOLD,
    SUMMARY_CHAR_TRIGRAM_OVERLAP_THRESHOLD,
    SUMMARY_BORDERLINE_MARGIN,
    _link_already_sent,
    _should_bypass_title_dup,
)


def check(name: str, cond: bool, detail: str = ""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    return cond


def test_stage1_title_same_batch():
    """실사고(2026-08-17): 같은 배치 안의 후보끼리 비교가 안 돼서 조선비즈가
    중앙일보와 100% 동일한 제목으로 나란히 발송됐던 문제. recent에 배치 내
    후보를 미리 넣어두고 그다음 후보가 걸리는지 확인한다."""
    recent = [{"title": "종합특검, '관저 이전 의혹' 김건희·윤한홍 기소", "category": "종합"}]
    dup_exact, score_exact, _, _ = is_duplicate(
        "종합특검, '관저 이전 의혹' 김건희·윤한홍 기소", "종합", recent)
    dup_diff, score_diff, _, _ = is_duplicate(
        "尹정부 관저 이전 특혜 의혹, 김건희·윤한홍 재판行", "종합", recent)
    return (
        check("완전 동일 제목 → 중복 판정", dup_exact, f"score={score_exact}")
        and check("문구 다른 제목 → 중복 아님", not dup_diff, f"score={score_diff}")
    )


def test_summary_overlap_asymmetric_length():
    """실사고(2026-08-18): 경향신문(상세)·조선일보(간략) 요약처럼 분량 차이가
    큰 진짜 중복은 Jaccard로는 새고, 오버랩 계수로는 잡혀야 한다."""
    detailed = ("정성호 법무부 장관이 형사소송법 통과 등으로 장관으로서의 역할이 "
                "줄어들어 국회에서 당 통합과 법 개정 보완에 기여하고자 사직서를 "
                "제출했다고 밝혔다. 청와대의 후속 인사 조치 요청에 대해서는 사의를 "
                "표명한 만큼 신속한 수리를 기대한다고 덧붙였다.")
    brief = "정성호 의원은 형사소송법 통과로 자신의 역할이 많지 않으며 국회에서 할일이 많을 것이라고 밝혔다."
    recent = [{"title": "x", "summary": detailed, "category": "속보"}]
    is_dup, wo, co, _ = is_summary_duplicate(brief, "속보", recent)
    return check("분량 비대칭 진짜 중복 → 오버랩 계수로 잡힘", is_dup, f"word={wo} char={co}")


def test_summary_overlap_false_positive_guard():
    """오탐 검증: 같은 전당대회의 '당대표 선출' vs '최고위원 선출'은 서로 다른
    사건이므로 중복으로 잡히면 안 된다."""
    chair_elect = ("김민석 의원이 17일 더불어민주당 신임 대표로 선출됐다. 대전컨벤션센터에서 "
                   "열린 전당대회에서 최종 득표율 54.08%를 기록했다.")
    board_elect = ("더불어민주당 전당대회 최고위원 선거에서 최민희, 박선원, 서미화, 이성윤, "
                   "한민수 후보가 당선됐다. 수석최고위원은 최민희 후보가 차지했으며, 친명계 "
                   "김용 후보는 6위로 탈락했다.")
    recent = [{"title": "x", "summary": chair_elect, "category": "속보"}]
    is_dup, wo, co, _ = is_summary_duplicate(board_elect, "속보", recent)
    return check("전당대회 내 다른 사건 → 중복 아님", not is_dup, f"word={wo} char={co}")


def test_numeric_conflict_guard():
    """실사고(2026-08-20): "코스피 4%대 급등…매수 사이드카 발동"과 "코스피 6%대
    급등…매수 사이드카 발동"이 문구 템플릿이 같다는 이유로 72.7% 제목 유사도로
    중복 처리됐다. 시황 속보는 문구는 고정 템플릿이고 핵심 수치만 실시간으로
    바뀌는데, 그 수치가 진짜 새 정보라 숫자가 다르면 문구가 비슷해도 중복이면
    안 된다. 단, 숫자까지 완전히 같으면(진짜 동일 기사 재수집 등) 여전히
    중복으로 잡혀야 한다."""
    recent = [{"title": "[속보] 코스피 4%대 급등…매수 사이드카 발동", "category": "속보"}]
    dup_diff_num, score_diff, _, _ = is_duplicate(
        "[속보] 코스피 6%대 급등…매수 사이드카 발동", "속보", recent)
    dup_same_num, score_same, _, _ = is_duplicate(
        "[속보] 코스피 4%대 급등…매수 사이드카 발동", "속보", recent)
    return (
        check("숫자만 다른 시황 제목 → 중복 아님", not dup_diff_num, f"score={score_diff}")
        and check("숫자까지 같은 완전 동일 제목 → 중복 판정", dup_same_num, f"score={score_same}")
    )


def test_market_term_alias_and_pct_only_conflict_guard():
    """실사고(2026-08-21): 코스닥 매도 사이드카 발동(같은 날, 같은 사건)이
    머니투데이("지수 800.86")·뉴스웍스("4% 급락")로 다르게 보도됐는데, (1) 한쪽은
    공식용어("매도호가 일시효력정지"), 다른 쪽은 통용어("매도 사이드카")를 써서
    단어/문자 겹침이 임계값 미만이었고 (2) 지수값 vs 등락률처럼 아예 다른 종류의
    숫자라 숫자충돌가드가 "숫자 안 겹침 → 다른 기사"로 오판해 둘 다 발송됐다.
    용어 정규화(_normalize_market_terms)로 겹침을 끌어올리고, 숫자충돌가드를
    퍼센트로만 한정해 지수값 vs 등락률 같은 이종 숫자 비교로 인한 오판을 없앤다.
    단, 코스피 4%→6%처럼 %끼리 실제로 다른 시황 속보는 여전히 중복이 아니어야
    한다(회귀 방지 — test_numeric_conflict_guard와 겹치지만 이 케이스에서
    같이 깨지지 않는지 다시 확인)."""
    formal = ("한국거래소가 코스닥 시장에 올해 32번째 프로그램 매도호가 일시효력정지를 "
              "발동했다. 지수가 하락하면서 장중 800.86으로 산출됐다.")
    common = ("21일 한국거래소는 코스닥시장에 매도 사이드카를 발동했다. 이날 코스닥 "
              "지수는 4% 넘게 급락하며 800선이 위태로운 상황이다.")
    recent = [{"title": "x", "summary": formal, "category": "속보"}]
    is_dup, wo, co, _ = is_summary_duplicate(common, "속보", recent)
    ok = check("같은 사이드카 사건, 용어만 다름 → 중복 판정", is_dup, f"word={wo} char={co}")

    kospi_recent = [{"title": "[속보] 코스피 4%대 급등…매수 사이드카 발동", "category": "속보"}]
    dup_diff, score_diff, _, _ = is_duplicate(
        "[속보] 코스피 6%대 급등…매수 사이드카 발동", "속보", kospi_recent)
    ok = check("퍼센트 전용 가드로 바꿔도 진짜 다른 % 시황은 여전히 중복 아님",
               not dup_diff, f"score={score_diff}") and ok
    return ok


def test_stale_breaking_news_age_calc():
    """실사고(2026-08-21): 연합뉴스TV "[속보] 코스피 1%대 하락…6,750선 출발"
    (발행 09:06 KST, 장 시작 시점 기사)이 17:01 KST에야 "[속보]"로 뒤늦게
    발송됐다 — 이미 장이 마감된 뒤였다. 구글 뉴스 검색 결과가 발행순이 아니라
    관련도순이라 오래된 기사가 늦게 우리 폴링에 걸릴 수 있어서다.
    _article_age_hours가 오래된/최신 기사, 발행시각 없는 기사를 올바르게
    구분하는지 확인한다(fetch_candidates가 이 값으로 STALE_BREAKING_NEWS_HOURS
    초과 [속보]를 후보에서 제외한다)."""
    old_entry = {"published_parsed": time.gmtime(time.time() - 8 * 3600)}
    fresh_entry = {"published_parsed": time.gmtime(time.time() - 0.2 * 3600)}
    no_date_entry = {}
    old_age = _article_age_hours(old_entry)
    fresh_age = _article_age_hours(fresh_entry)
    none_age = _article_age_hours(no_date_entry)
    return (
        check(f"8시간 전 기사 → STALE_BREAKING_NEWS_HOURS({STALE_BREAKING_NEWS_HOURS}시간) 초과",
              old_age > STALE_BREAKING_NEWS_HOURS, f"age={old_age}")
        and check("12분 전 기사 → 임계값 이내", fresh_age <= STALE_BREAKING_NEWS_HOURS, f"age={fresh_age}")
        and check("발행시각 없음 → None(필터링 안 함, 판단 못 할 땐 보내는 쪽)", none_age is None)
    )


def test_category_specific_dedup_window():
    """실사고(2026-08-22): "한화·리플 전격 제휴" 단독 기사가 8/21 16:11과
    8/22 09:40, 약 17.5시간 간격으로 완전히 같은 내용으로 두 번 발송됐다.
    전날(2026-08-20) DEDUP_WINDOW_HOURS를 6→2시간으로 줄인 여파 — 시황 오탐은
    그 다음 날 숫자충돌가드로 이미 별도 해결됐으니 짧은 창을 유지할 이유가
    없어진 반면, 단독/종합처럼 몇 시간~하루 간격으로 재등장하는 기사는 못
    잡게 됐다. 그래서 카테고리별로 분리했다: 속보는 시황 실시간성 때문에
    2시간 유지, 단독/종합은 재탕을 잡도록 12시간으로 늘림(사용자 결정)."""
    return (
        check("속보 창 = 2시간(시황 실시간성 유지)", _dedup_window_hours("속보") == 2)
        and check("단독 창 = 12시간(재탕 방지로 확대)", _dedup_window_hours("단독") == 12)
        and check("종합 창 = 12시간(재탕 방지로 확대)", _dedup_window_hours("종합") == 12)
    )


def test_self_comparison_excluded_in_stage2():
    """실사고(2026-08-22): 1단계 루프가 통과한 모든 후보의 제목만 있는 항목을
    recent_for_dedup에 즉시 넣는데, 그 리스트가 2단계 is_summary_duplicate 호출에
    그대로 전달되면서 후보 자기 자신의 제목 항목과도 비교하게 됐다. 제목과 요약은
    같은 사건이라 핵심 단어(예: "장미란", "한림항", "시신")가 늘 겹치므로 오버랩
    계수가 손쉽게 임계값을 넘어 "중복"으로 오판됐다 — 실측(연속 실행 로그): 9회
    연속 실행 중 7회가 스테이지-2 후보가 있었는데도 발송 0건. run()에서 guid로
    자기 자신 항목만 제외하고 비교하도록 고쳤다(recent_excl_self)."""
    guid = "test-guid-jeju-body-found"
    title = "[속보]제주 한림항 인근서 장미란씨 추정 시신 발견…신분증 나와"
    summary = ("제주 한림항 인근 해상에서 실종된 장미란 씨로 추정되는 시신이 발견됐다. "
               "현장에서 신분증이 함께 나와 경찰이 정확한 신원을 확인하고 있다.")
    recent_with_self = [{"guid": guid, "title": title, "category": "속보"}]
    is_dup_bug, wo_bug, co_bug, _ = is_summary_duplicate(summary, "속보", recent_with_self)
    recent_excl_self = [r for r in recent_with_self if r.get("guid") != guid]
    is_dup_fixed, wo_fixed, co_fixed, _ = is_summary_duplicate(summary, "속보", recent_excl_self)
    return (
        check("버그 재현: guid 필터 없이 자기 자신과 비교하면 중복 오판", is_dup_bug,
              f"word={wo_bug} char={co_bug}")
        and check("수정: guid로 자기 자신 제외하면 중복 아님", not is_dup_fixed,
                  f"word={wo_fixed} char={co_fixed}")
    )


def test_correction_mismatch_guard():
    """실사고(2026-08-22): "[속보]제주 한림항 인근서 장미란씨 추정 시신 발견…신분증
    나와"에 이어 "[속보]제주경찰 '실종자 추정 시신발견 사실 아니다' 정보 혼선"이라는
    정정 속보가 나왔다. 둘 다 "제주"·"실종자"·"시신"·"발견" 같은 핵심 단어를 공유하는
    같은 사건 기사라, 문구가 겹치면(이 테스트에서는 일부러 겹치게 구성) 원보도와
    정정보도가 "중복"으로 오판될 수 있다 — 실제로는 내용이 정반대라 절대 같은
    걸로 묶으면 안 된다. 한쪽에만 정정 표현이 있으면 오버랩 점수와 무관하게
    중복 아님으로 강제해야 하고, 반대로 양쪽 다 같은 정정 보도를 전한 것이면
    (다른 매체가 같은 정정 소식을 전함) 정상적으로 중복 판정이 돼야 한다."""
    original = ("제주 한림항 인근 해상에서 발견된 시신은 실종자 장미란 씨로 추정된다. "
                "경찰이 신원을 확인하고 있다.")
    retraction = ("제주 한림항 인근 해상에서 발견된 시신이 실종자 장미란 씨라는 소식은 "
                  "사실이 아니다. 경찰이 신원을 확인하고 있다.")
    recent_orig = [{"title": "x", "summary": original, "category": "속보"}]
    is_dup, wo, co, _ = is_summary_duplicate(retraction, "속보", recent_orig)
    ok = check("원보도-정정보도(문구 겹침) → 중복 아님으로 강제", not is_dup,
               f"word={wo} char={co}")

    retraction2 = ("제주 한림항 인근 해상에서 발견된 시신이 실종자 장미란 씨라는 소식은 "
                   "사실이 아니다. 경찰 관계자가 확인했다.")
    recent_retract = [{"title": "x", "summary": retraction, "category": "속보"}]
    is_dup2, wo2, co2, _ = is_summary_duplicate(retraction2, "속보", recent_retract)
    ok = check("양쪽 다 같은 정정보도(다른 매체) → 정상적으로 중복 판정",
               is_dup2, f"word={wo2} char={co2}") and ok
    return ok


def test_summary_borderline_detection():
    """2026-08-23 사용자 요청: 짧은 창(recent_for_dedup) 비교로 "중복 아님"이
    나왔지만 임계값에 아슬아슬하게 못 미치는 애매한 점수는, D1에서 더 긴 기간을
    추가 조회해 재확인한다(query_d1_recent_sent). 재확인을 언제 트리거할지
    판정하는 _is_summary_borderline이 임계값-마진 경계를 올바르게 나누는지
    확인한다 — 실제 네트워크 호출(query_d1_recent_sent)은 이 테스트 스위트가
    검증하는 범위 밖(다른 함수들처럼 네트워크 의존 부분은 별도 단위 테스트 안 함)."""
    word_edge = SUMMARY_WORD_OVERLAP_THRESHOLD - SUMMARY_BORDERLINE_MARGIN
    char_edge = SUMMARY_CHAR_TRIGRAM_OVERLAP_THRESHOLD - SUMMARY_BORDERLINE_MARGIN
    return (
        check(f"단어 {word_edge}%(마진 경계) → 애매함으로 판정",
              _is_summary_borderline(word_edge, 0))
        and check(f"단어 {word_edge - 1}%(경계 미만) → 애매하지 않음",
                  not _is_summary_borderline(word_edge - 1, 0))
        and check(f"문자 {char_edge}%(마진 경계) → 애매함으로 판정",
                  _is_summary_borderline(0, char_edge))
        and check("둘 다 0% → 애매하지 않음(명백히 다른 기사)",
                  not _is_summary_borderline(0, 0))
    )


def test_link_exact_match():
    """2026-08-23 사용자 요청: 제목·요약은 Gemini가 크롤링할 때마다 문구를 조금씩
    다르게 다시 써서 텍스트 비교만으로는 애매할 수 있지만, 실제 언론사 URL이
    완전히 같으면 100% 같은 기사다. _link_already_sent(짧은 창 안, 무료)와
    query_d1_link_exists(짧은 창 밖, D1 조회 — 네트워크 의존이라 이 스위트에서는
    테스트 안 함)로 텍스트 비교보다 먼저, 더 저렴하게 확인한다."""
    recent = [{"link": "https://example.com/news/123", "category": "단독"}]
    return (
        check("완전히 같은 링크 → 이미 보냄으로 판정",
              _link_already_sent("https://example.com/news/123", recent))
        and check("다른 링크 → 판정 안 됨",
                  not _link_already_sent("https://example.com/news/999", recent))
        and check("빈 링크 → 판정 안 됨(오탐 방지)", not _link_already_sent("", recent))
    )


def test_should_bypass_title_dup_on_different_link():
    """2026-08-24 사용자 요청: 같은 언론사·다른 주소 = 후속 기사다(추측이 아니라
    확정 — 언론사가 별개 URL에 완전히 같은 기사를 중복 게시하는 일은 없다는 사용자
    확인). JTBC가 "위너즈 코인" 수사 지연 단독을 낸 뒤 같은 소재를 다룬 후속
    단독("깡통 코인" 응징하던 유튜버가 실은 사기 피소)을 냈는데, 제목이 겹쳐
    1단계에서 스킵될 뻔한 이런 경우를 실제 URL로 구제한다. matched_link를 모르는
    경우(과거 링크 정보 없음)는 판단 근거가 없어 기존처럼 스킵을 유지해야 하고,
    속보는 2보/3보 관행 때문에 이 예외를 적용하지 않는다(사용자도 별도 검토
    필요하다고 확인)."""
    return (
        check("단독, 링크 다름 → 스킵 취소(후속 기사)",
              _should_bypass_title_dup("단독", "https://jtbc.co.kr/a1", "https://jtbc.co.kr/a2"))
        and check("단독, 링크 같음 → 스킵 유지(진짜 중복)",
                  not _should_bypass_title_dup("단독", "https://jtbc.co.kr/a1", "https://jtbc.co.kr/a1"))
        and check("속보는 예외 미적용(2보/3보 관행, 별도 검토 필요)",
                  not _should_bypass_title_dup("속보", "https://a.com/1", "https://a.com/2"))
        and check("matched_link 모름 → 판단 근거 없어 스킵 유지",
                  not _should_bypass_title_dup("단독", "", "https://jtbc.co.kr/a2"))
    )


def test_title_only_requires_both_scores():
    """실사고(2026-08-24): 미 재무부의 이란 제재 발표를 다룬 속보 3건("송금 허가
    중단"/"2차제재 대상"/"60곳 제재 명단" — 전부 다른 사실)이 Gemini 요약 실패로
    제목만 비교됐는데, 실제 로그에서 단어 오버랩은 28.6%/14.3%로 정확히 "다른
    기사"라고 판단했지만 문자 3-gram 오버랩은 37.5%/29.2%로 임계값(25%)을 넘어
    "둘 중 하나만 넘어도 중복"(OR) 규칙 때문에 셋 다 서로 중복 처리돼 발송
    0건이 됐다. 셋 다 "美재무부 ... 이란 ..."이라는 짧고 상투적인 문구를 공유해
    문자 오버랩만 가짜로 올라간 것 — 요약(80자↑ 문장)과 달리 제목(20~30자)은
    상투구 비중이 커서 문자 오버랩 단독으로는 못 믿는다. title_only=True일 때는
    단어·문자가 동시에 임계값을 넘어야 중복 판정하도록 고쳤다."""
    a = '[속보] 美재무부 "이란 송금 허용했던 일반 허가 효력 중단"'
    b = '[속보] 美재무부 "이란 핵·미사일·사이버·석유 관련 약 60곳 제재"'
    recent = [{"title": b, "category": "속보"}]
    is_dup_or, wo, co, _ = is_summary_duplicate(a, "속보", recent, title_only=False)
    is_dup_and, wo2, co2, _ = is_summary_duplicate(a, "속보", recent, title_only=True)
    return (
        check("버그 재현: title_only=False(OR)면 문자 오버랩 단독으로 중복 오판",
              is_dup_or, f"word={wo} char={co}")
        and check("수정: title_only=True(AND)면 단어 오버랩이 임계값 미달이라 중복 아님",
                  not is_dup_and, f"word={wo2} char={co2}")
    )


def test_refusal_marks():
    """실사고(2026-08-18): "본문 내용이 없어 요약할 수 없다"가 그대로 발송됨.
    실사고(2026-08-19): 뉴스핌 속보 자리채움 문구("자세한 뉴스는 곧 전해질
    예정이다")가 그대로 발송됨."""
    cases = [
        ("기사 본문 내용이 없어 요약할 수 없다.", True),
        ("자세한 뉴스는 곧 전해질 예정이다.", True),
        ("정성호 법무부 장관이 사직서를 제출했다고 밝혔다.", False),
    ]
    ok = True
    for text, expected in cases:
        got = _looks_like_refusal(text)
        ok = check(f"_looks_like_refusal({text[:20]}...) == {expected}", got == expected) and ok
    return ok


def main():
    results = [
        test_stage1_title_same_batch(),
        test_summary_overlap_asymmetric_length(),
        test_summary_overlap_false_positive_guard(),
        test_numeric_conflict_guard(),
        test_market_term_alias_and_pct_only_conflict_guard(),
        test_stale_breaking_news_age_calc(),
        test_category_specific_dedup_window(),
        test_self_comparison_excluded_in_stage2(),
        test_correction_mismatch_guard(),
        test_summary_borderline_detection(),
        test_link_exact_match(),
        test_should_bypass_title_dup_on_different_link(),
        test_title_only_requires_both_scores(),
        test_refusal_marks(),
    ]
    print()
    if all(results):
        print(f"전체 통과 ({len(results)}개)")
        sys.exit(0)
    else:
        print(f"실패 있음 ({results.count(False)}/{len(results)})")
        sys.exit(1)


if __name__ == "__main__":
    main()
