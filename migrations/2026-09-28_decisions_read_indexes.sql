-- D1 읽기 사용량 절감 (2026-09-28)
-- query_d1_link_exists: WHERE link = ? AND decision = 'sent' — link 인덱스가 없어 회당 ~6,087행 스캔
-- query_d1_recent_sent: WHERE category = ? AND decision = 'sent' ... ORDER BY id DESC LIMIT — 회당 ~3,565행
-- (category, decision)의 끝에는 rowid(id)가 암묵적으로 붙어 ORDER BY id DESC를 정렬 없이 처리한다.

CREATE INDEX IF NOT EXISTS idx_decisions_link_decision ON decisions(link, decision);
CREATE INDEX IF NOT EXISTS idx_decisions_category_decision ON decisions(category, decision);
