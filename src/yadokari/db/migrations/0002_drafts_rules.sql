-- 記事の下書き。1記事につき1本（article_id UNIQUE）。
-- WordPress の post ID を持つので、再実行しても同じ記事を2本作らない。
CREATE TABLE drafts (
  id INTEGER PRIMARY KEY,
  article_id    INTEGER NOT NULL UNIQUE REFERENCES articles (id),
  title         TEXT NOT NULL,
  excerpt       TEXT,
  body_html     TEXT NOT NULL,
  tags          TEXT,
  featured_image TEXT,
  -- 検算で見つかった、元資料に無い数字（JSON配列）。空でなければ人が確認する
  warnings      TEXT,
  model         TEXT,
  generated_at  TEXT NOT NULL,
  edited_at     TEXT,
  -- editing → wp_draft（WP に下書きがある）→ scheduled（WP に予約済み）→ published
  state         TEXT NOT NULL DEFAULT 'editing',
  scheduled_at  TEXT,
  wp_post_id    INTEGER,
  wp_status     TEXT,
  wp_link       TEXT,
  wp_media_id   INTEGER,
  pushed_at     TEXT,
  published_at  TEXT,
  error         TEXT
);

CREATE INDEX idx_drafts_state ON drafts (state);

-- 学習ループ。非承認理由のタグが繰り返されたら、ルール候補を出す。
-- 採点のプロンプトに載るのは、人が approved にしたものだけ。
CREATE TABLE rule_candidates (
  id INTEGER PRIMARY KEY,
  reason_tag  TEXT NOT NULL UNIQUE,
  hit_count   INTEGER NOT NULL,
  proposal    TEXT,
  state       TEXT NOT NULL DEFAULT 'proposed',
  created_at  TEXT NOT NULL,
  updated_at  TEXT
);
