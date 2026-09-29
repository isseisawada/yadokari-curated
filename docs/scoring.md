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

## 実測（trial #1・2026-09-29・GitHub Actions）

- 候補24件を採点、失敗0件。売り出し情報・詐欺の注意喚起・「Top 10」のまとめ・焙煎所・インテリアのみは0点
- 公開実績の見本18本: 平均 65.6・中央値 68.5、**17本が審査ライン50点以上**
  - 軸の平均: design 69・story 60・smallness 86・photos 93・japan 58・facts 74・freshness 27・seo 69
  - freshness が低いのは見本が過去の記事だから（元記事の公開日から数える）
- 落ちた1本は「Morinest 北軽井沢」（国内のトレーラーハウスホテル）。「海外事例ではない」で0点
  → **国内の施設も対象**にプロンプトを直した
- 載せたくない見本がまだ無く、AUC は未計測

## 審査を受けた追加（2026-09-29）

- **重複**: 別の媒体で同じ作品を紹介している記事（ArchDaily と designboom の Wiki World
  「Red Submarine Cabin」）が二重に審査に出た。採点で抜き出した物件名・設計者とタイトルの言葉から
  後から入った方に `duplicate_of` を付け、審査待ちから外す（`scoring/dedupe.py`、LLM は使わない）。
  外れていたら審査画面の「重複ではない」で戻す
- **量産型のトレーラーハウスを下げる**: 非承認の理由「よくある車検なしトレーラーなのでNG」から。
  ユーザーの指示で、学習ループ（同じ理由3回）を待たずに承認済みのルールとして入れた
  （rule_candidates の reason_tag `mass_produced_trailer`、審査画面の RULES で直せる）
  - ルールだけでは足りなかった: デザイン・物語は 40 以下になったが、小ささ・写真・SEO が満点なので
    61〜65点で審査待ちに残った。採点に `catalog_model`（量産型・カタログ型か）を足し、true なら
    **45点で頭打ち**（審査ライン50の下）にした（`scoring/weights.py` の CATALOG_CAP）
