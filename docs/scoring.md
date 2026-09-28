# 採点の軸（案・2026-09-28）

YADOKARI の既存記事121本の分析（docs/existing-articles.md）から起こした**初期案**。
承認・非承認が溜まったら、実績で重みを見直す。

## 考え方（frmg と同じ）

- **LLM に任せるのは抽出と、読まないと分からない判断だけ。** 合算は Python 側で、
  config.yaml の重みを使って行う
- 事実（設計者・国・面積・価格など）は**記事に書いてあるものだけ**。無ければ空

## 軸と重み

| 軸 | 重み | 誰が決めるか | 中身 |
|---|---|---|---|
| design | 0.25 | LLM（0〜100） | 外観・室内・素材・ディテールのデザインの質 |
| story | 0.25 | LLM（0〜100） | 暮らし方・つくり手の思い・場所との関係を物語として書けるか |
| smallness | 0.15 | LLM が種別を選び、Python が表で点にする | 下の表 |
| photos | 0.15 | 記事から数える | 7枚以上 100 / 4〜6枚 70 / 1〜3枚 30 / 0枚 0（既存記事は中央値7枚・最少4枚） |
| japan | 0.10 | LLM（0〜100） | 日本の読者との接点（既存記事の 70本中26本が日本につなげて締めている） |
| facts | 0.05 | 抽出結果から数える | 設計者/ビルダー 40・国 30・面積/寸法 20・価格 10 |
| freshness | 0.05 | 公開日から | 14日以内 100 / 30日 80 / 90日 50 / それ以上 20 / 不明 50 |

**relevant が false のもの**（家具・プロダクト、ニュース、売り出し情報だけの記事、
大きな邸宅や集合住宅）は**0点**。消さずに審査画面には残す。

### smallness の表

| 種別 | 点 |
|---|---|
| 車輪付きタイニーハウス・トレーラー・バン | 100 |
| ボートハウス・コンテナハウス | 90 |
| ツリーハウス | 85 |
| 小屋・キャビン | 80 |
| プレハブ・モジュール | 75 |
| 小さめの住宅（ADU など） | 60 |
| 住まいだが小さくも動きもしない | 20 |
| 建物ではない | 0 |

## モデル

- 既定は `claude-opus-5-5`、effort `low`。1件あたり約2〜3円の見積もり（推測。入力4千トークン・出力1千トークン程度として）
- 安全分類器が判定を断ったときに別モデルでやり直す **server-side fallback を有効**にしている
  （`scoring.fallbacks: true`）
- Haiku 4.5 に下げる場合は `effort: null` にすること（Haiku は effort を受け付けず 400 で全件落ちる。frmg で踏んだ）

## 検証のしかた

```
python -m yadokari.cli validate import validation/positives.tsv --label pos
python -m yadokari.cli validate import validation/negatives.tsv --label neg
python -m yadokari.cli validate score
python -m yadokari.cli validate report
```

- **載せたい（pos）**: `validation/positives.tsv`。YADOKARI が 2024年以降に実際に記事にした
  元記事 104本（既存記事の本文の最初の出典リンク）。2026-09-28 に取り込みを試し、89本取れた
  （Dezeen はデータセンターから 403、Small House Bliss などは robots で不可）
- **載せたくない（neg）**: `validation/negatives.tsv`。**人が選ぶ**（5〜10本）。
  これが入ると、採点が見分けられているか（AUC）が出る
- report は「載せたいのに点が低いもの」を並べる。軸と重みを見直す手がかりにする
- 見本は審査の候補とは別のテーブル（validation）に置き、審査画面には出さない
