-- 記事候補。source_url の UNIQUE で、何度収集しても同じ記事は二度入らない。
CREATE TABLE articles (
  id INTEGER PRIMARY KEY,
  source        TEXT NOT NULL,
  source_url    TEXT NOT NULL UNIQUE,
  title         TEXT,
  published_at  TEXT,
  collected_at  TEXT NOT NULL,
  content_text  TEXT,
  -- 本文中の画像URL（JSON配列）。写真は直リンク＋via表記の方針（2026-09-28 ユーザー判断）
  image_urls    TEXT,
  image_count   INTEGER NOT NULL DEFAULT 0,
  og_image      TEXT,
  -- 記事ページにある写真クレジットの原文（「Photos by …」など）
  photo_credit  TEXT,
  -- collected → scored → approved | rejected
  status        TEXT NOT NULL DEFAULT 'collected',
  score         REAL,
  score_detail  TEXT,
  assessment    TEXT,
  scored_at     TEXT,
  scored_model  TEXT,
  score_error   TEXT
);

CREATE INDEX idx_articles_status ON articles (status);

-- 審査の結果。非承認の理由は学習ループの入力になる。
CREATE TABLE feedback (
  id INTEGER PRIMARY KEY,
  article_id  INTEGER NOT NULL REFERENCES articles (id),
  decision    TEXT NOT NULL,
  reason      TEXT,
  tag         TEXT,
  created_at  TEXT NOT NULL
);
