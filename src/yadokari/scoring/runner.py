"""未採点の記事をまとめて採点する。1件の失敗で止めず、行に理由を残して次へ進む。"""

from __future__ import annotations

from dataclasses import dataclass

from yadokari.config import Config
from yadokari.db.connection import DbConnection
from yadokari.db.repository import approved_rules, save_score, save_score_error, unscored
from yadokari.logging_setup import get_logger
from yadokari.scoring.client import ScoringClient, ScoringError
from yadokari.scoring.dedupe import mark_duplicates
from yadokari.scoring.prompt import SYSTEM_PROMPT, build_user_prompt
from yadokari.scoring.weights import build_axes, total

log = get_logger(__name__)


@dataclass
class ScoreStats:
    scored: int = 0
    failed: int = 0

    def summary(self) -> str:
        return f"採点 {self.scored} 件（失敗 {self.failed}）"


def score_pending(
    config: Config, conn: DbConnection, limit: int = 40, client: ScoringClient | None = None,
    rules: list[str] | None = None,
) -> ScoreStats:
    stats = ScoreStats()
    rows = unscored(conn, limit)
    if not rows:
        return stats
    client = client or ScoringClient(config, SYSTEM_PROMPT)
    # 学習ループで**人が承認した**ルールだけを載せる
    if rules is None:
        rules = approved_rules(conn)
    for row in rows:
        prompt = build_user_prompt(
            row["title"], row["source_url"], row["source"], row["content_text"] or "",
            row["image_count"], row["photo_credit"], rules=rules,
        )
        try:
            a = client.assess(prompt)
        except ScoringError as exc:
            log.warning("採点に失敗: %s (%s)", row["source_url"], exc)
            save_score_error(conn, row["id"], str(exc))
            conn.commit()
            stats.failed += 1
            continue
        axes = build_axes(config.scoring.weights, a, row["image_count"], row["published_at"])
        detail = {ax.name: ax.to_dict() for ax in axes}
        save_score(conn, row["id"], total(axes, a), detail, a.to_dict(), client.model)
        conn.commit()
        stats.scored += 1
    # 別の媒体で同じ作品を紹介しているものに印を付ける（審査待ちの一覧から外す）
    if stats.scored:
        mark_duplicates(conn)
        conn.commit()
    return stats


def score_one(config: Config, conn: DbConnection, article_id: int,
              client: ScoringClient | None = None) -> float:
    """1件だけ採点する（手動投入の直後に画面から）。"""
    row = conn.execute("SELECT * FROM articles WHERE id = ? AND status = 'collected'",
                       (article_id,)).fetchone()
    if row is None:
        raise ValueError("未採点の記事ではありません")
    client = client or ScoringClient(config, SYSTEM_PROMPT)
    a = client.assess(build_user_prompt(
        row["title"], row["source_url"], row["source"], row["content_text"] or "",
        row["image_count"], row["photo_credit"], rules=approved_rules(conn),
    ))
    axes = build_axes(config.scoring.weights, a, row["image_count"], row["published_at"])
    score = total(axes, a)
    save_score(conn, row["id"], score, {ax.name: ax.to_dict() for ax in axes}, a.to_dict(), client.model)
    conn.commit()
    return score
