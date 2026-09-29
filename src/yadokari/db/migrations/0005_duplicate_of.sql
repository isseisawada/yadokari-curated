-- 別の媒体で同じ作品を紹介している記事（2026-09-29: ArchDaily と designboom で Wiki World の
-- Red Submarine Cabin が二重に入った）。先に入った方の id を指す。審査待ちの一覧からは外す
ALTER TABLE articles ADD COLUMN duplicate_of INTEGER;
