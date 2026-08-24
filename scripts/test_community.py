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

from fetch_community import parse_ruliweb, parse_theqoo, parse_dcinside, parse_clien, parse_inven, parse_ppomppu


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
        and check("조회수·댓글수·추천수 정확", items[0][2] == 1000 and items[0][3] == 12 and items[0][4] == 30 if items else False)
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
        and check("조회수·댓글수·추천수 정확", items[0][2] == 500 and items[0][3] == 9 and items[0][4] == 20 if items else False)
    )


def test_parse_clien():
    """실사고(2026-08-25): `list_hit` div와 `hit` span 사이에 줄바꿈·들여쓰기가
    있어서 태그가 붙어있다고 가정한 첫 정규식이 조회수를 전부 못 찾았다(실제
    사이트에서 30건 전부 views=None). 공지(`list_item notice`)·광고
    (`list_item hongbo`)가 `symph_row` 클래스·`data-board-sn` 속성이 없어 자연히
    제외되는지도 확인한다. div는 명확한 닫는 태그로 행을 못 잘라서 다음 글 시작
    직전까지를 한 행으로 슬라이스하는 방식이라, 두 번째 글의 지표가 첫 번째 글
    슬라이스에 안 새는지가 특히 중요하다."""
    html = '''
    <div class="list_item notice">
        <div class="list_title"><a class="list_subject" href="/service/board/annonce/1">공지(제외돼야 함)</a></div>
    </div>
    <div class="list_item symph_row  " data-role="list-row" data-author-id=aaa data-board-sn=111 data-comment-count=3>
        <div class="list_symph view_symph" data-role="list-like-count"><span>7</span></div>
        <div class="list_title" data-role="list-title">
            <a class="list_subject" href="/service/board/park/111?od=T31" data-role="cut-string">
                <span class="subject_fixed" data-role="list-title-text" title="합성 제목 다섯">합성 제목 다섯</span>
            </a>
        </div>
        <div class="list_hit">
            <span class="hit">1234</span>
        </div>
    </div>
    <div class="list_item symph_row  " data-role="list-row" data-author-id=bbb data-board-sn=112 data-comment-count=0>
        <div class="list_symph view_symph" data-role="list-like-count"><span>0</span></div>
        <div class="list_title" data-role="list-title">
            <a class="list_subject" href="/service/board/park/112?od=T31" data-role="cut-string">
                <span class="subject_fixed" data-role="list-title-text" title="합성 제목 여섯">합성 제목 여섯</span>
            </a>
        </div>
        <div class="list_hit">
            <span class="hit">50</span>
        </div>
    </div>
    '''
    items = parse_clien(html)
    return (
        check("공지 제외, 2건만 파싱", len(items) == 2, f"got {len(items)}")
        and check("조회수 정확(줄바꿈 있어도)", items[0][2] == 1234 if items else False, f"views={items[0][2] if items else None}")
        and check("댓글수는 속성에서 바로", items[0][3] == 3 if items else False)
        and check("추천수 정확", items[0][4] == 7 if items else False)
        and check("두 번째 글로 안 샘", items[1][2] == 50 and items[1][3] == 0 if len(items) > 1 else False)
    )


def test_parse_inven():
    """공지 행(`class="notice all"`)이 제외되는지, 댓글 배지가 없는 글(댓글 0건)도
    안 죽고 처리되는지 확인한다."""
    html = '''
    <tr class="notice all">
        <td class="tit"><a class="subject-link" href="https://www.inven.co.kr/board/webzine/2097/1">공지(제외돼야 함)</a></td>
    </tr>
    <tr class="">
        <td class="tit">
            <a class="subject-link" href="https://www.inven.co.kr/board/webzine/2097/2718999">
                <span class="category">[기타]</span>합성 제목 일곱
            </a>
            <span class="con-comment">[8]</span>
        </td>
        <td class="view">2,345</td>
        <td class="reco">6</td>
    </tr>
    '''
    items = parse_inven(html)
    return (
        check("공지 제외, 1건만 파싱", len(items) == 1, f"got {len(items)}")
        and check("카테고리+제목 텍스트", "합성 제목 일곱" in items[0][0] if items else False)
        and check("조회수 콤마 제거", items[0][2] == 2345 if items else False)
        and check("댓글수·추천수 정확", items[0][3] == 8 and items[0][4] == 6 if items else False)
    )


def test_parse_ppomppu():
    """추천수(`baseList-rec`)가 대부분 빈 칸인 뽐뿌 특성상 None으로 빠지는지,
    댓글 배지가 없는 글(댓글 0건)도 안 죽는지 확인한다."""
    html = '''
    <tr align="center" class="baseList ">
        <td class="baseList-space baseList-numb" colspan="2">100</td>
        <td class="baseList-space " align="left">
            <a class="baseList-title  " href="view.php?id=freeboard&no=100"  ><span>합성 제목 여덟</span></a><span class="baseList-c" onclick="win_comment('?id=freeboard&no=100');">4</span>
        </td>
        <td class="baseList-space" colspan="2"><time class="baseList-time" title="26.08.25 04:00:00">04:00:00</time></td>
        <td class="baseList-space baseList-rec" colspan="2"></td>
        <td class="baseList-space baseList-views" colspan="2">777</td>
    </tr>
    '''
    items = parse_ppomppu(html)
    return (
        check("1건 파싱", len(items) == 1, f"got {len(items)}")
        and check("URL 접두사 정확", items[0][1].startswith("https://www.ppomppu.co.kr/zboard/") if items else False)
        and check("조회수·댓글수 정확", items[0][2] == 777 and items[0][3] == 4 if items else False)
        and check("추천수 빈칸 → None", items[0][4] is None if items else False)
    )


def main():
    results = [
        test_parse_ruliweb(),
        test_parse_theqoo(),
        test_parse_dcinside(),
        test_parse_clien(),
        test_parse_inven(),
        test_parse_ppomppu(),
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
