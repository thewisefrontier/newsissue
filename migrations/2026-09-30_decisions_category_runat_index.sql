-- query_d1_recent_sent(category, decision='sent') 재조회가 인덱스(category, decision)를
-- id DESC로 훑으면서 run_at 조건을 행마다 나중에 걸러, LIMIT 300에 못 미치면 그 카테고리의
-- 보관 기간(7일) 전체를 다 읽는다(2026-09-30 점검 실측: 회당 평균 2389행, 기대 최대 300).
-- run_at을 인덱스에 포함해 ORDER BY run_at DESC로 바로 최신 300행만 읽게 한다.
DROP INDEX IF EXISTS idx_decisions_category_decision;
CREATE INDEX IF NOT EXISTS idx_decisions_cat_dec_runat ON decisions(category, decision, run_at);
