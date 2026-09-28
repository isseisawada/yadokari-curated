"""毎朝の確認:「昨日の分は出たか」「今日の分は予約されているか」。

frmg で**止まったことを知らせる経路が無く、9日間誰も気づかなかった**（2026-09-17〜25）。
その反省で最初から持つ。判定は2つの情報源を突き合わせる:

  - DB: 予約した下書き（scheduled_at）
  - WP の公開 API: カテゴリの最新記事（**認証なしで読める**。DB が落ちていても確かめられる）

終了コード 0 = 問題なし / 1 = 要確認。通知（メール・携帯）はこの結果を読む側がやる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from yadokari.config import Config
from yadokari.db.connection import DbConnection


@dataclass
class Report:
    ok: bool = True
    lines: list[str] = field(default_factory=list)

    def warn(self, text: str) -> None:
        self.ok = False
        self.lines.append(f"要確認: {text}")

    def info(self, text: str) -> None:
        self.lines.append(text)


def check(config: Config, conn: DbConnection | None, now: datetime | None = None,
          http: httpx.Client | None = None) -> Report:
    tz = ZoneInfo(config.app.timezone)
    now = now or datetime.now(UTC)
    today = now.astimezone(tz).date()
    start_yesterday = datetime.combine(today - timedelta(days=1), datetime.min.time(), tzinfo=tz)
    start_today = start_yesterday + timedelta(days=1)
    end_today = start_today + timedelta(days=1)
    report = Report()

    # WP 側: 昨日から今までに公開された記事
    own = http is None
    http = http or httpx.Client(timeout=30, headers={"User-Agent": "YADOKARI-curated/0.1"})
    try:
        r = http.get(
            config.wordpress.base_url.rstrip("/") + "/wp-json/wp/v2/posts",
            params={"categories": config.wordpress.category_ids[0],
                    # after は WP の現地時刻（post_date）と比べられる
                    "after": start_yesterday.replace(tzinfo=None).isoformat(),
                    "per_page": 20, "_fields": "id,date,link"},
        )
        r.raise_for_status()
        posts = r.json()
        report.info(f"WP: 昨日以降にカテゴリで公開された記事 {len(posts)} 本")
        for p in posts:
            report.info(f"  {p.get('date')} {p.get('link')}")
    except (httpx.HTTPError, ValueError) as exc:
        report.warn(f"WP の公開 API を読めませんでした: {exc}")
    finally:
        if own:
            http.close()

    if conn is None:
        return report
    try:
        rows = conn.execute(
            "SELECT id, title, state, scheduled_at, wp_status FROM drafts"
            " WHERE scheduled_at >= ? AND scheduled_at < ? ORDER BY scheduled_at",
            (start_yesterday.astimezone(UTC).isoformat(), end_today.astimezone(UTC).isoformat()),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - DB が落ちていること自体を知らせる
        report.warn(f"DB を読めませんでした（止まっている可能性）: {exc}")
        return report

    for r in rows:
        due = datetime.fromisoformat(r["scheduled_at"])
        label = f"{due.astimezone(tz):%m/%d %H:%M} {r['title']}"
        if due <= now and r["state"] != "published":
            report.warn(f"予定時刻を過ぎたのに公開を確認できていません（{r['wp_status'] or r['state']}）: {label}")
        elif due > now and r["state"] != "scheduled":
            report.warn(f"今日の予定がまだ WP に予約されていません（{r['state']}）: {label}")
        else:
            report.info(f"OK {r['state']}: {label}")
    today_rows = [r for r in rows if datetime.fromisoformat(r["scheduled_at"]) >= start_today]
    if config.wordpress.allow_schedule and not today_rows:
        report.warn("今日の予定が1本もありません（在庫切れか、審査が止まっている）")
    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM articles WHERE status = 'scored' AND score >= ?",
        (config.scoring.review_threshold,),
    ).fetchone()["n"]
    report.info(f"審査待ち（{config.scoring.review_threshold:g}点以上）: {pending} 件")
    return report
