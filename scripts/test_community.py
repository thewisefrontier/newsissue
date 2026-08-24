"""
scripts/test_community.py
---------------------------
fetch_community.py 파서 회귀 테스트.

실제 사이트 HTML을 저장소에 넣지 않는다(저작권·용량 문제) — 대신 실제 구조를
반영해 직접 지어낸 짧은 합성 HTML 조각으로 파서를 검증한다. 제목 등 내용은
전부 테스트용으로 지어낸 것이고 실제 게시물이 아니다.

실행: python scripts/test_community.py
"""

import sys

from fetch_community import parse_ruliweb, parse_theqoo, parse_dcinside


def check(name: str, cond: bool, detail: str = ""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    return cond


def test_parse_ruliweb():
    """실사고(2026-08-25): 상위 4위(`best_top_row`)는 <strong class="text_over">,
    5위 이하(`mode_list`)는 <span class="text_over">로 마크업이 달라서 span
    케이스를 처음에 놓쳤다(실제 사이트에서 31건 중 4건만 잡힘). 순위 배지 숫자가
    제목에 안 섞이는지, 사이드바 위젯(다른 board id 링크)이 안 섞이는지도 같이
    확인한다."""
    html = '''
    <tr class="table_body blocktarget best_top_row">
        <td class="subject">
            <a class="subject_link deco flex center" href="/best/board/300143/read/111?m=humor">
                <span style="...">1</span>
                <strong class="text_over">합성 제목 하나</strong>
                <span class="num_reply flex_item_1"> (12)</span>
            </a>
        </td>
        <td class="recomd">30</td>
        <td class="hit">1000</td>
    </tr>
    <tr class="table_body blocktarget mode_list">
        <td class="subject">
            <a class="subject_link deco flex center" href="/best/board/300143/read/222?m=all&t=now">
                <span class="text_over">합성 제목 둘</span>
                <span class="num_reply flex_item_1"> (3)</span>
            </a>
        </td>
        <td class="recomd">5</td>
        <td class="hit">200</td>
    </tr>
    <tr class="table_body blocktarget best_top_row">
        <td class="subject">
            <a class="deco" href="/market/board/1020/read/999">
                <strong class="text_over">사이드바 핫딜 위젯(제외돼야 함)</strong>
            </a>
        </td>
        <td class="hit">50</td>
    </tr>
    '''
    items = parse_ruliweb(html)
    titles = [it[0] for it in items]
    return (
        check("2건 파싱(핫딜 위젯 제외)", len(items) == 2, f"got {len(items)}")
        and check("순위 배지 숫자 안 섞임", titles[0] == "합성 제목 하나" if items else False, f"title={titles[0] if items else None}")
        and check("5위 이하(span) 케이스도 파싱", "합성 제목 둘" in titles, f"titles={titles}")
        and check("조회수·댓글수 정확", items[0][2] == 1000 and items[0][3] == 12 if items else False)
    )


def test_parse_theqoo():
    """공지 행(`class="notice"`)이 안 섞이는지, 조회수 콤마 구분자가 정수로
    변환되는지 확인한다."""
    html = '''
    <tr class="notice  nofn" data-document_srl="1">
        <td class="title"><a href="/hot/1">공지(제외돼야 함)</a></td>
    </tr>
    <tr>
        <td class="no">100</td>
        <td class="cate"><span>이슈</span></td>
        <td class="title">
            <a href="/hot/555">합성 제목 셋</a>
            <a href="/hot/555#555_comment" class="replyNum">42</a>
        </td>
        <td class="time">12:34</td>
        <td class="m_no">1,234</td>
    </tr>
    '''
    items = parse_theqoo(html)
    return (
        check("공지 제외, 1건만 파싱", len(items) == 1, f"got {len(items)}")
        and check("URL 정확", items[0][1] == "https://theqoo.net/hot/555" if items else False)
        and check("조회수 콤마 제거 정수 변환", items[0][2] == 1234 if items else False)
        and check("댓글수 정확", items[0][3] == 42 if items else False)
    )


def test_parse_dcinside():
    """광고 행(`data-no` 없음)이 안 섞이는지, 조회수 '-'(광고 특유 표기)를 만나도
    안 죽는지 확인한다."""
    html = '''
    <tr class="ub-content ">
        <td class="gall_num">AD</td>
        <td class="gall_tit ub-word"><a href="https://addc.dcinside.com/x">광고(제외돼야 함)</a></td>
        <td class="gall_count">-</td>
    </tr>
    <tr class="ub-content us-post" data-no="777" data-type="icon_recomimg">
        <td class="gall_num">777</td>
        <td class="gall_tit ub-word">
            <a href="/board/view/?id=neostock&amp;no=777&amp;exception_mode=recommend&amp;page=1">
                <em class="icon_img icon_recomimg"></em>합성 제목 넷
            </a>
            <a class="reply_numbox" href="https://gall.dcinside.com/board/view/?id=neostock&amp;no=777"><span class="reply_num">[9]</span></a>
        </td>
        <td class="gall_date" title="2026-08-25 01:00:00">08.25</td>
        <td class="gall_count">500</td>
        <td class="gall_recommend">20</td>
    </tr>
    '''
    items = parse_dcinside(html)
    return (
        check("광고 제외, 1건만 파싱", len(items) == 1, f"got {len(items)}")
        and check("아이콘 태그 제거된 제목", items[0][0] == "합성 제목 넷" if items else False, f"title={items[0][0] if items else None}")
        and check("URL 엔티티(&amp;) 정상 해제", "&no=777" in items[0][1] if items else False)
        and check("조회수·댓글수 정확", items[0][2] == 500 and items[0][3] == 9 if items else False)
    )


def main():
    results = [
        test_parse_ruliweb(),
        test_parse_theqoo(),
        test_parse_dcinside(),
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
