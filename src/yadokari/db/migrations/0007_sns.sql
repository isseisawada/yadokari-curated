-- 公式 SNS（X・Threads @yadokari_mobi）の投稿（2026-10-13 ユーザー指定）。
-- 1日4本: 08:15・16:15 はその日の新着記事（kind=new）、12:15・20:15 は過去記事（kind=archive）。
-- draft（承認待ち）→ approved → queued（WP に予約を渡した）→ posted / failed。skipped は人が外したもの
CREATE TABLE sns_posts (
  id INTEGER PRIMARY KEY,
  kind          TEXT NOT NULL,
  slot_at       TEXT NOT NULL UNIQUE,
  wp_post_id    INTEGER NOT NULL,
  link          TEXT NOT NULL,
  title         TEXT,
  text          TEXT NOT NULL,
  pattern       TEXT,
  warnings      TEXT,
  model         TEXT,
  state         TEXT NOT NULL DEFAULT 'draft',
  created_at    TEXT NOT NULL,
  approved_at   TEXT,
  queued_at     TEXT,
  posted_at     TEXT,
  x_id          TEXT,
  threads_id    TEXT,
  error         TEXT,
  -- 測った数字（JSON）。{"x": {"impressions": .., "likes": ..}, "threads": {...}, "at": ...}
  metrics       TEXT
);

CREATE INDEX idx_sns_state ON sns_posts (state);
CREATE INDEX idx_sns_wp ON sns_posts (wp_post_id);

-- 週ごとのフォロワー数など、投稿にひもづかない数字
CREATE TABLE sns_stats (
  id INTEGER PRIMARY KEY,
  measured_at   TEXT NOT NULL,
  platform      TEXT NOT NULL,
  followers     INTEGER,
  extra         TEXT
);
