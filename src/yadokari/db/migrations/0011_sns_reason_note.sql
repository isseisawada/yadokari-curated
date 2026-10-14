-- 外した理由と、作り直すときの指示（2026-10-14 ユーザー指定）。
-- reason は次の過去記事選びで AI に見せる（似た記事を選ばない）。note はその1本を書き直すときの指示。
ALTER TABLE sns_skipped ADD COLUMN reason TEXT;
ALTER TABLE sns_posts ADD COLUMN note TEXT;
