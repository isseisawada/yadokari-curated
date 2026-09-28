"""週次レポート。frmg の report/weekly.py の考え方（数字は DB から数えるだけ）。

見るもの: ソース別の流入と承認率、非承認の理由、下書き・送信・公開の数、予約投稿の取りこぼし。
「どのソースを増やす／外すか」「採点の重みを見直すか」の判断材料。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta


def weekly(conn, now: datetime | None = None, days: int = 7) -> str:
    now = now or datetime.now(UTC)
    since = (now - timedelta(days=days)).isoformat()
    lines = [f"週次レポート（{(now - timedelta(days=days)):%Y-%m-%d} 〜 {now:%Y-%m-%d}）"]

    collected = conn.execute(
        "SELECT source, COUNT(*) AS n, AVG(score) AS avg FROM articles WHERE collected_at >= ?"
        " GROUP BY source ORDER BY n DESC", (since,),
    ).fetchall()
    lines.append(f"\n■ 収集 {sum(r['n'] for r in collected)} 件")
    for r in collected:
        avg = f"{r['avg']:.1f}" if r["avg"] is not None else "−"
        lines.append(f"  {r['source']:16} {r['n']:3} 件  平均点 {avg}")

    decisions = conn.execute(
        "SELECT a.source, f.decision, f.tag FROM feedback f JOIN articles a ON a.id = f.article_id"
        " WHERE f.created_at >= ?", (since,),
    ).fetchall()
    per: dict[str, Counter] = defaultdict(Counter)
    tags: Counter = Counter()
    for d in decisions:
        per[d["source"]][d["decision"]] += 1
        if d["decision"] == "rejected":
            tags[d["tag"] or "（未分類）"] += 1
    total_ok = sum(c["approved"] for c in per.values())
    total_ng = sum(c["rejected"] for c in per.values())
    rate = f"{total_ok / (total_ok + total_ng):.0%}" if total_ok + total_ng else "−"
    lines.append(f"\n■ 審査 承認 {total_ok} / 非承認 {total_ng}（承認率 {rate}）")
    for src, c in sorted(per.items(), key=lambda x: -sum(x[1].values())):
        n = c["approved"] + c["rejected"]
        lines.append(f"  {src:16} 承認 {c['approved']:2} / 非承認 {c['rejected']:2}（{c['approved'] / n:.0%}）")
    if tags:
        lines.append("  非承認の理由: " + "・".join(f"{t} {n}" for t, n in tags.most_common()))

    def count(sql: str) -> int:
        return conn.execute(sql, (since,)).fetchone()["n"]

    lines.append("\n■ 記事")
    lines.append(f"  下書きを作成 {count('SELECT COUNT(*) AS n FROM drafts WHERE generated_at >= ?')}"
                 f" / WP に送信 {count('SELECT COUNT(*) AS n FROM drafts WHERE pushed_at >= ?')}"
                 f" / 公開 {count('SELECT COUNT(*) AS n FROM drafts WHERE published_at >= ?')}")
    missed = conn.execute(
        "SELECT COUNT(*) AS n FROM drafts WHERE state = 'scheduled' AND scheduled_at < ?",
        ((now - timedelta(minutes=30)).isoformat(),),
    ).fetchone()["n"]
    if missed:
        lines.append(f"  要確認: 予定を過ぎても公開されていない予約 {missed} 件")
    waiting = conn.execute(
        "SELECT COUNT(*) AS n FROM articles WHERE status = 'scored'").fetchone()["n"]
    approved_no_draft = conn.execute(
        "SELECT COUNT(*) AS n FROM articles a WHERE a.status = 'approved'"
        " AND NOT EXISTS (SELECT 1 FROM drafts d WHERE d.article_id = a.id)").fetchone()["n"]
    lines.append(f"  審査待ち {waiting} 件 / 承認済みで下書き未作成 {approved_no_draft} 件")
    return "\n".join(lines)
