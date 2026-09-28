# ソース候補の実測（2026-09-28）

`scripts/probe_sources.py` の結果。生データは `docs/source-survey.tsv`（初回の実行分）。
New Atlas `tiny-houses`・Dwell `/@dwell/rss`・designboom `tag/cabin`・Dezeen `tag/micro-homes`・
ArchDaily の一覧ページは追加で測ったもので、TSV には入っていない（スクリプトの候補は差し替え済み）。
測った場所は Claude のクラウド環境（データセンターの IP）。GitHub Actions や Render から
取れるかの目安になる。

## 測ったもの

- robots.txt（取れなければ取らない・fail-closed）
- フィードの件数と**窓（何日分か）**、1週間あたりの本数
- そのうち小さな家・動く家に当たりそうな本数（タイトルと本文のキーワード）
  — **キーワードは粗い。上限の目安**（Yanko Design は "micro-toolkit" や "camper" の
  電動おもちゃまで拾っていて、実際は16本中2本）
- 最新記事の経過日数（止まったフィードを見分ける）
- フィードが全文か抜粋か
- 記事ページがこの環境から取れるか
- 記事ページの写真クレジットの書き方

**利用規約（自動収集の禁止）はまだ見ていない。** 採用候補について次に確認する。

## 結果

### 本命 — 量があり、記事ページも取れる

| ソース | 関連/週（上限） | 窓 | 最新 | 本文 | 記事ページ | 写真クレジットの例 |
|---|---|---|---|---|---|---|
| **ArchDaily**（cabins-and-lodges 一覧） | 未測※ | 一覧24件 | — | 抜粋 | 200 | © Alexander Bogorodskiy（撮影者） |
| **Dwell** `/@dwell/rss` | 2.2 | 35日 | 0.1日 | 全文 | 200 | Photos by Ema Peter（撮影者） |
| **Tiny House Talk** | 9.2 | 3.8日 | 4.5日 | 抜粋 | 200 | Photos: The Backcountry Hut Company（**ビルダー**） |
| **New Atlas** `tiny-houses` | 4.3 | 97日 | 0.3日 | 抜粋 | 200 | — |
| **designboom** `tag/cabin` | 1.3 | 53日 | 17.6日 | 全文 | 200 | ©撮影者 |
| Leibal | 4.1 | 5日 | 0.4日 | 全文 | 200 | 見当たらず |

※ **ArchDaily は YADOKARI の既存記事でいちばん使われている媒体**（121本中47本がリンク、
画像328枚）。ただしサイト全体のフィードは24件で**窓が1.4日**しかなく、関連は1件だけ。
**`/category/cabins-and-lodges` の一覧ページ**（robots 許可・200・記事リンク24件、
記事ページも200）を入口にする。frmg の「一覧ページでバックフィル」と同じやり方。
週あたりの本数は一覧の日付を取って別途測る。

### 取れるが制約あり

| ソース | 関連/週 | 本文 | 記事ページ | 制約 |
|---|---|---|---|---|
| **Dezeen** `tag/cabins`・`tag/micro-homes` | 0.4 / 0.1 | **全文** | **403** | 記事ページはデータセンターから取れない（frmg と同じ）。**フィードが全文なのでフィードだけで回せる** |
| Tiny House Blog | 0.9 | 全文 | 200 | 量が少ない |
| Design Milk `architecture` | 0 | 全文 | 200 | 直近12件に該当なし。YADOKARI では画像71枚を使っているので、別カテゴリを探す |
| Gessato | 0.3 | 抜粋 | 200 | 量が少ない |

### 止まっている・薄い（採らない）

| ソース | 理由 |
|---|---|
| Living Big in a Tiny House | 最新が **2,498日前**（フィード停止。YouTube 中心に移った可能性・推測） |
| Small House Bliss | 最新が 3,578日前 |
| Humble Homes | 最新が 600日前 |
| Cabin Porn | 最新が 230日前（Substack に移行） |
| IGNANT | 最新 95日前・関連ほぼ0 |
| Inhabitat `tiny-homes` | 50件で3年分・関連/週 0.0 |
| Tiny Living | 窓 724日・関連/週 0.1 |
| Yanko Design | プロダクト中心。キーワードの上限19/週は誤検出で、実際は16本中2本 |
| Contemporist | フィードが0件（取り方を変えれば取れる可能性あり・未確認） |
| Treehugger / autoevolution | フィードを見つけられず（未確認） |

## 量の見立て（推測）

誤検出を差し引いて、実際に候補になりそうなのは**週10〜15本**
（Tiny House Talk 5前後・New Atlas 2〜4・Dwell 1〜2・designboom 1・Leibal 1前後・
Dezeen 0.5・ArchDaily 未測）。

1日1本＝週7本を出すには、候補の**半分近くを承認する**必要がある。
frmg の経験では承認率はそこまで高くならないので、**最初は1日1本でも在庫がきつい可能性が高い**。
ArchDaily の一覧を実測し、足りなければソースを足す。

## 権利の判断に効くこと

**写真の権利者は媒体ではなく撮影者やビルダーであることが多い。**

- ArchDaily・Dwell・designboom・Design Milk・Gessato は、記事ページに**撮影者名**を出している。
  媒体は撮影者から掲載の許可を得ているだけで、**転載の許可は媒体に聞いても出せない**ことがある
- Tiny House Talk は「Photos: ビルダー名」。**ビルダーが配っている写真**で、許諾の窓口が
  はっきりしている（ビルダーに直接聞ける）
- いまの YADOKARI の記事は `via:媒体ドメイン` 表記で、撮影者名は出していない

許諾を取るなら**ビルダー・建築事務所に直接**が筋。ビルダー由来の写真が多いソース
（Tiny House Talk など）は、許諾ベースの運用と相性がよい。

## 利用規約の確認（2026-09-28）

各サイトの規約ページを読み、自動収集・スクレイピングの禁止条項を探した。
**禁止しているものは `manual_only`（手動投入のみ。システムはページを取得しない）にした。**

| ソース | 結果 | 扱い |
|---|---|---|
| **ArchDaily** | **禁止。** "use an automatic device (such as a robot or spider) or manual process to copy or 'scrape' the Website or Website Content for any purpose without the express written permission of ArchDaily" | manual_only |
| **Dwell**（運営 Ziff Davis の規約） | **禁止。** "use any robot, spider ... to crawl, scrape, database scrape, screen scrape, harvest, gather, extract, retrieve or index any portion of the Services" | manual_only |
| **Gessato** | **禁止。** "prohibited from using any data mining, robots, or other data gathering systems and extraction tools" | manual_only |
| New Atlas | 自動収集の禁止条項は見当たらない。ただし "use or attempt to use any Material published on the New Atlas Website to create any web site or publication" を禁止 | 自動収集は継続。**写真・文章の転載は規約上の問題がある**（下記） |
| Dezeen | 規約ページがデータセンターから 403 で読めない | 継続（未確認） |
| designboom | /legal/ は短く、規約本体は daaily.com にあり robots.txt が取れず未確認 | 継続（未確認） |
| Tiny House Talk | 規約ページが見つからない（/disclaimer/ に禁止条項なし） | 継続 |
| Leibal | 規約ページが見つからない | 継続 |
| Tiny House Blog | /terms-of-service/ に禁止条項なし | 継続 |

**注意（推測を含む）:**

- 規約の確認は、この調査より**前**に ArchDaily・Dwell・Gessato のページを実測と試験収集で
  取得していた（各数件〜数十件、3秒間隔・robots 許可の範囲）。試験用の DB からは削除した
- ArchDaily の条項は「手作業でのコピー」まで書面の許可なしに禁じている。**既存の YADOKARI 記事の
  写真（ArchDaily の画像328枚の直リンク）も、この条項との関係を確認した方がよい**
- New Atlas も素材の転載を禁じている。直リンク＋via の運用が規約上どう扱われるかは、
  規約の文言だけでは判断できない（法的判断ではない）

**量への影響:** 自動で回るのは Tiny House Talk・New Atlas・designboom・Leibal・Dezeen・
Tiny House Blog。見立ては**週8〜12本**（推測）。1日1本（週7本）は、候補のほとんどを
承認しないと届かない。手動投入と、規約で許されるソースの追加が要る。

### 2026-09-29 追記: ユーザー判断で全サイトを自動収集

ユーザー判断により、**規約で自動収集を禁じている ArchDaily・Dwell・Gessato も自動収集する**
（`manual_only` を外した。config.yaml の note に判断と日付を残してある）。
規約違反を理由とするアクセス遮断や申し入れのリスクは残る。

変えないこと: robots.txt の尊重（fail-closed）・同一ドメイン3秒間隔・並列なし・
User-Agent に連絡先・ブロックやレート制限の回避をしない。

- ArchDaily は一覧に少し前の作品が並ぶので `lookback_days: 365`（30日だと24件中15件が落ちた）
- Tiny House Blog は robots.txt 自体が 429 を返す日があり、その日は取らない（fail-closed のまま）
