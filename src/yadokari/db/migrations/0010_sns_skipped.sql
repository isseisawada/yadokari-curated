-- 人が「外す」した記事の控え（2026-10-14 ユーザー指定: 外した本数だけ別の記事を確認に回す）。
-- sns_posts から外した行を消して枠を空け、次の毎時の実行で別の記事を作らせる。
-- 同じ記事をまた出してこないよう、外した記事はここに残す。
CREATE TABLE sns_skipped (
  id INTEGER PRIMARY KEY,
  wp_post_id    INTEGER NOT NULL,
  slot_at       TEXT NOT NULL,
  kind          TEXT,
  title         TEXT,
  skipped_at    TEXT NOT NULL
);

CREATE INDEX idx_sns_skipped_wp ON sns_skipped (wp_post_id);

INSERT INTO sns_skipped (wp_post_id, slot_at, kind, title, skipped_at)
  SELECT wp_post_id, slot_at, kind, title, COALESCE(approved_at, created_at) FROM sns_posts WHERE state = 'skipped';
DELETE FROM sns_posts WHERE state = 'skipped';
