# yadokari-curated

YADOKARI.net「世界の小さな家・動く家」キュレーション記事システム。

```
収集 → 採点(LLM) → 審査(人) → 下書き生成 → 人が編集 → WordPress 予約投稿 → SNS
```

できているもの: 収集・採点・審査画面・下書き生成・WordPress 送信（下書き／予約）・
公開の確認・学習ループ・毎日の確認。**SNS はまだ**（どのアカウントに出すか未定）。

実際の WordPress にはまだ一度も送っていない。最初の1本はユーザーの OK を取ってから
（docs/operations.md）。

## 動かし方

```
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
python -m yadokari.cli db migrate
python -m yadokari.cli collect --dry-run --limit 3
python -m yadokari.cli collect
python -m yadokari.cli score
python -m yadokari.cli list --status scored
python -m yadokari.cli serve
```

`.env` に `ANTHROPIC_API_KEY` を入れる（採点に使う）。`DATABASE_URL` が空なら
`data/yadokari.db`（SQLite）を使う。**鍵はコミットしない・チャットに貼らない。**

## 守ること

- 公式 RSS 優先。足りないときは一覧ページ
- robots.txt を必ず確認し、取れなければ取らない（fail-closed）
- 同一ドメインは3秒間隔・並列なし。User-Agent に連絡先
- ログイン・CAPTCHA・レート制限・bot 検知の回避はしない
- 再実行しても同じ記事は二度入らない（`source_url` UNIQUE）

設定の下限（3秒・並列なし・robots）は `config.py` で弾くので、config.yaml で緩められない。

## ドキュメント

- `docs/existing-articles.md` — 既存記事121本の分析（型・画像の扱い・更新ペース）
- `docs/source-survey.md` — ソース候補の実測
- `docs/scoring.md` — 採点の軸と重み（案）
- `docs/seo.md` — SEO / AIO（トレーラーハウスで上位を狙う）
- `docs/operations.md` — 運用の流れ・最初の本番の手順・秘匿値・毎日の確認

frmg-jp/ig-post（FREMING CURATED）の net・db・採点クライアントを元にしている。
共有ライブラリにはせず、コピーして作り変えている。
