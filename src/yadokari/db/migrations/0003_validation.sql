-- 採点の検証用。審査の候補（articles）とは混ぜない（審査画面に出さないため）。
-- label: pos = YADOKARI が実際に記事にしたもの / neg = 載せたくないもの（人が選ぶ）
CREATE TABLE validation (
  id INTEGER PRIMARY KEY,
  url           TEXT NOT NULL UNIQUE,
  label         TEXT NOT NULL,
  note          TEXT,
  title         TEXT,
  published_at  TEXT,
  content_text  TEXT,
  image_count   INTEGER NOT NULL DEFAULT 0,
  photo_credit  TEXT,
  fetched_at    TEXT,
  fetch_error   TEXT,
  score         REAL,
  score_detail  TEXT,
  assessment    TEXT,
  scored_at     TEXT,
  score_error   TEXT
);
