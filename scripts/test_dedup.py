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

from fetch_news import (
    is_duplicate,
    is_summary_duplicate,
    _looks_like_refusal,
    word_overlap,
    char_trigram_overlap,
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
    dup_exact, score_exact, _ = is_duplicate(
        "종합특검, '관저 이전 의혹' 김건희·윤한홍 기소", "종합", recent)
    dup_diff, score_diff, _ = is_duplicate(
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
