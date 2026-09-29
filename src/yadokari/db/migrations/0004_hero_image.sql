-- 1枚目に使う写真（外観）。審査画面の1枚目・カードのサムネイル・下書きのアイキャッチに使う。
-- NULL = まだ選んでいない / '' = 選んだが外観が見つからなかった（元の並びのまま）
ALTER TABLE articles ADD COLUMN hero_image TEXT;
