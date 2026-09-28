# 運用（2026-09-28）

## 全体の流れ

```
[GitHub Actions 毎朝] 収集 → 採点 → 学習ループ → WP の状態確認
                          ↓
[審査画面（Render）]  承認／非承認（非承認は理由のタグか自由記述が必須）
                          ↓ 承認
                     「下書きを作る」（LLM・1分ほど）→ 人が編集 → 公開日時を入れる
                          ↓
                     「WordPress に下書き保存」（status=draft）
                          ↓ WP 上で確認して OK
                     「公開日時で予約する」（status=future）← allow_schedule が true のときだけ
                          ↓
[WordPress]          予約時刻に公開（wp-cron）
                          ↓
[毎朝の確認]         monitor が「予定どおり出たか」を判定
```

## 決めたこと

- **写真**: 本文は元サイトへの直リンク、キャプションは `via: ドメイン`（元記事へのリンク）、
  アイキャッチは元写真を WP のメディアに取り込む。既存記事と同じ運用（2026-09-28 ユーザー判断）
- **文章**: 翻訳・要約ではなく YADOKARI の視点で書き直す（プロンプトで指示）
- **事実**: 元記事にあるものだけ。**元記事に無い数字は検算で拾い**、1回書き直させ、
  それでも残れば下書きの上部に赤で出す
- **公開の判断は必ず人。** 下書きまでが自動
- **予約投稿は WP に任せる。** 自前の常駐ワーカーは置かない（DB を叩き続けない。
  frmg で Neon の無料枠を食い潰して9日止まった穴を避ける）
- **二重投稿しない。** slug を `yc-<記事ID>` に固定し、送る前に WP で同じ slug を探す。
  送り直しは同じ投稿の更新になる。公開済みは上書きしない
- **タグは WP に既にあるものだけ付ける。** 新しいタグは作らない
- **1日の本数** は `wordpress.post_times` の数。最初は `["19:00"]`（既存記事の大半が 19:00 公開）

## 最初の本番（**ユーザーの OK を取ってから**）

1. WP に投稿用の専用アカウントを作り、Application Password を発行する
   （ユーザー → プロフィール → アプリケーションパスワード）
2. `.env`（手元）と Render・GitHub Secrets に `WP_USER` / `WP_APP_PASSWORD` を入れる。
   **チャットに貼らない**
3. 審査画面で1本承認 → 下書き → 「WordPress に下書き保存」
4. WP の管理画面でその下書きを開き、見た目・写真・via・タグ・アイキャッチを確認
5. OK なら `config.yaml` の `wordpress.allow_schedule: true` にして push
6. 1本予約して、公開されたことを `wp sync`（または画面の「WordPress の状態を確認」）で確かめる

ステージングの WP があるなら、先に `wordpress.base_url` をそちらに向けて 3〜6 を通す。

## 必要な秘匿値

| 名前 | どこに | 用途 |
|---|---|---|
| DATABASE_URL | Render・GitHub Secrets・手元 .env | Neon（**frmg とは別のプロジェクト**。無料枠はプロジェクト単位） |
| ANTHROPIC_API_KEY | Render・GitHub Secrets・手元 .env | 採点・下書き・学習 |
| WP_USER / WP_APP_PASSWORD | Render・GitHub Secrets・手元 .env | WordPress への送信 |
| REVIEW_UI_USER / REVIEW_UI_PASSWORD | Render | 審査画面の Basic 認証（無いと起動しない） |

## 毎日の確認

```
python -m yadokari.cli monitor
```

- WP の公開 API（認証なし）で、カテゴリに昨日以降に公開された記事を数える
- DB の予定と突き合わせて「予定を過ぎたのに公開されていない（予約投稿の失敗）」
  「今日の予定がまだ予約されていない」を出す
- 終了コード 1 が要確認

frmg は「止まったことを知らせる経路が無く9日間気づかなかった」。本番に入ったら、
これを毎朝決まった時刻に回して携帯・メールに送る（Claude のルーティンなど）。
**GitHub Actions の定期実行は3〜10時間遅れるので、この用途には使わない。**

## コマンド

```
python -m yadokari.cli collect [--source NAME] [--limit N] [--dry-run]
python -m yadokari.cli score
python -m yadokari.cli list --status scored --min-score 50
python -m yadokari.cli serve
python -m yadokari.cli draft ARTICLE_ID
python -m yadokari.cli wp push DRAFT_ID [--schedule]
python -m yadokari.cli wp sync
python -m yadokari.cli learn
python -m yadokari.cli monitor
python -m yadokari.cli report
python -m yadokari.cli validate import|score|report
```

## 分かっている制約

- **Tiny House Blog** は3秒間隔でも 429（アクセス過多）を返した（2026-09-28）。
  採用はしたが、続くようなら外す
- **ArchDaily・Dwell・Gessato は利用規約で自動収集を禁止**（2026-09-28 確認）だが、
  **ユーザー判断で自動収集している**（2026-09-29）。遮断や申し入れがあれば止める
- 手動投入（審査画面の ADD）は、上のどれにも当たらない媒体やビルダーのサイト用
- **Dezeen** は記事ページがデータセンターから 403。フィードの全文だけで回している
- Dezeen・designboom は規約を読めていない（docs/source-survey.md）
