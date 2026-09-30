-- D1 쓰기 사용량 절감 (2026-09-29) — 미적용. 적용:
--   wrangler d1 execute newsissue-dedup --remote --file migrations/2026-09-29_drop_unused_decisions_indexes.sql
-- decisions INSERT/DELETE는 행 + 인덱스 수만큼 쓰기 행이 잡힌다(현재 인덱스 5개 = 행당 6).
-- 아래 두 인덱스는 scripts/의 어떤 쿼리도 쓰지 않는다(코드 쿼리는 link+decision,
-- category+decision, run_at 세 개로 모두 커버). 7일 보관(~1.2만 행)이라 수동 디버깅 조회는
-- 인덱스 없이 전체 스캔해도 충분하다. 삭제 후 행당 쓰기 6 → 4.
DROP INDEX IF EXISTS idx_decisions_guid;
DROP INDEX IF EXISTS idx_decisions_decision;
