-- newsissue 중복판정 디버깅용 결정 이력 (Cloudflare D1)
-- 실사고(2026-08-20): 삼성전자 100조 주주환원 기사가 오탐으로 걸렸는데,
-- GitHub Actions 로그는 90일 뒤 사라지고, 그나마도 c['title']만 찍혀서
-- 실제 비교된 요약 텍스트와 매칭 대상을 재구성할 수 없었다. 모든 스테이지-2
-- 판정(발송/중복스킵/도메인제외)을 여기 남겨서 나중에 SQL로 바로 조회한다.

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,          -- KST ISO8601, 실행 시각
    guid TEXT NOT NULL,
    category TEXT NOT NULL,        -- 속보/단독/종합
    title TEXT NOT NULL,
    link TEXT,
    decision TEXT NOT NULL,        -- sent / dup_title / dup_summary / excluded_domain
    compare_text TEXT,             -- 실제 비교에 쓰인 텍스트(요약 또는 제목 폴백)
    matched_text TEXT,             -- 중복으로 판정된 경우, 매칭된 상대 텍스트 전체
    word_score REAL,
    char_score REAL,
    summary TEXT                   -- 최종 발송된 요약(발송된 경우만)
);

CREATE INDEX IF NOT EXISTS idx_decisions_run_at ON decisions(run_at);
CREATE INDEX IF NOT EXISTS idx_decisions_decision ON decisions(decision);
CREATE INDEX IF NOT EXISTS idx_decisions_guid ON decisions(guid);
