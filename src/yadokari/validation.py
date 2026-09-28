"""採点の検証。「載せたい／載せたくない」の見本を採点して、点の並びが人の判断と合うかを見る。

    載せたい（pos）: YADOKARI が実際に記事にした元記事（validation/positives.tsv。
                    既存記事121本の出典リンクから作った）
    載せたくない（neg）: 人が選ぶ（validation/negatives.tsv）

frmg は承認実績で approval-report を回して重みを見直した。ここでは公開実績を先に使う。
見本は審査の候補（articles）と別のテーブルに置き、審査画面には出さない。
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from yadokari.collect.page import normalize_url, parse_page
from yadokari.config import Config
from yadokari.db.connection import DbConnection
from yadokari.db.repository import now_iso
from yadokari.logging_setup import get_logger
from yadokari.net.client import HttpClient, RobotsDisallowed
from yadokari.scoring.client import ScoringClient, ScoringError
from yadokari.scoring.prompt import SYSTEM_PROMPT, build_user_prompt
from yadokari.scoring.weights import build_axes, total

log = get_logger(__name__)


def read_list(path: str | Path) -> list[tuple[str, str]]:
    """TSV（url<TAB>メモ…）。# で始まる行と空行は飛ばす。"""
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        url, _, note = line.partition("\t")
        rows.append((url.strip(), note.strip()))
    return rows


@dataclass
class ImportStats:
    added: int = 0
    existing: int = 0
    failed: list[str] = field(default_factory=list)


def import_list(config: Config, conn: DbConnection, path: str | Path, label: str,
                limit: int | None = None, client=None) -> ImportStats:
    """見本の記事ページを取る。収集と同じ HttpClient（robots・3秒間隔）を通す。"""
    if label not in ("pos", "neg"):
        raise ValueError("label は pos か neg")
    stats = ImportStats()
    rows = read_list(path)
    todo = []
    for url, note in rows:
        url = normalize_url(url)
        if conn.execute("SELECT 1 FROM validation WHERE url = ?", (url,)).fetchone():
            stats.existing += 1
            continue
        todo.append((url, note))
    todo = todo[:limit] if limit else todo
    own = client is None
    client = client or HttpClient(config.http)
    try:
        for url, note in todo:
            try:
                page = parse_page(client.get(url).text, url)
                conn.execute(
                    "INSERT INTO validation (url, label, note, title, published_at, content_text,"
                    " image_count, photo_credit, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (url, label, note, page.title, page.published_at, page.text, len(page.images),
                     page.credit, now_iso()),
                )
                stats.added += 1
            except (RobotsDisallowed, httpx.HTTPError) as exc:
                conn.execute(
                    "INSERT INTO validation (url, label, note, fetch_error, fetched_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (url, label, note, str(exc)[:500], now_iso()),
                )
                stats.failed.append(f"{url} ({exc})")
            conn.commit()
    finally:
        if own:
            client.close()
    return stats


def score_all(config: Config, conn: DbConnection, limit: int = 200,
              client: ScoringClient | None = None) -> tuple[int, int]:
    rows = conn.execute(
        "SELECT * FROM validation WHERE score IS NULL AND content_text IS NOT NULL LIMIT ?",
        (limit,),
    ).fetchall()
    if not rows:
        return 0, 0
    client = client or ScoringClient(config, SYSTEM_PROMPT)
    ok = ng = 0
    for r in rows:
        try:
            a = client.assess(build_user_prompt(
                r["title"], r["url"], "validation", r["content_text"], r["image_count"],
                r["photo_credit"],
            ))
        except ScoringError as exc:
            conn.execute("UPDATE validation SET score_error = ? WHERE id = ?", (str(exc)[:500], r["id"]))
            conn.commit()
            ng += 1
            continue
        axes = build_axes(config.scoring.weights, a, r["image_count"], r["published_at"])
        conn.execute(
            "UPDATE validation SET score = ?, score_detail = ?, assessment = ?, scored_at = ?,"
            " score_error = NULL WHERE id = ?",
            (total(axes, a), json.dumps({ax.name: ax.score for ax in axes}),
             json.dumps(a.to_dict(), ensure_ascii=False), now_iso(), r["id"]),
        )
        conn.commit()
        ok += 1
    return ok, ng


def auc(pos: list[float], neg: list[float]) -> float | None:
    """載せたいものが載せたくないものより高い点になる確率（0.5 = 見分けられていない）。"""
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def report(config: Config, conn: DbConnection) -> str:
    rows = conn.execute("SELECT * FROM validation").fetchall()
    th = config.scoring.review_threshold
    lines = [f"見本 {len(rows)} 件（取得失敗 {sum(1 for r in rows if r['fetch_error'])}・"
             f"採点済み {sum(1 for r in rows if r['score'] is not None)}）"]
    by: dict[str, list] = {"pos": [], "neg": []}
    for r in rows:
        if r["score"] is not None:
            by[r["label"]].append(r)
    for label, name in (("pos", "載せたい（公開実績）"), ("neg", "載せたくない")):
        rs = by[label]
        if not rs:
            lines.append(f"{name}: 採点済みなし")
            continue
        scores = [r["score"] for r in rs]
        relevant = sum(1 for r in rs if json.loads(r["assessment"])["relevant"])
        lines.append(
            f"{name}: {len(rs)} 件 / 平均 {statistics.mean(scores):.1f}・中央値 {statistics.median(scores):.1f}"
            f" / 審査ライン {th:g} 点以上 {sum(s >= th for s in scores)} 件"
            f" / relevant {relevant} 件"
        )
        axes: dict[str, list[float]] = {}
        for r in rs:
            for k, v in json.loads(r["score_detail"]).items():
                axes.setdefault(k, []).append(v)
        lines.append("  軸の平均: " + "・".join(f"{k} {statistics.mean(v):.0f}" for k, v in axes.items()))
    a = auc([r["score"] for r in by["pos"]], [r["score"] for r in by["neg"]])
    if a is not None:
        lines.append(f"見分けの精度（AUC）: {a:.2f}（1.0 が完全、0.5 は当てずっぽう）")
    else:
        lines.append("AUC: 載せたくない見本がまだ無いので出せない（validation/negatives.tsv に入れる）")
    low = sorted(by["pos"], key=lambda r: r["score"])[:8]
    if low:
        lines.append("載せたいのに点が低いもの（軸を見直す手がかり）:")
        for r in low:
            a_ = json.loads(r["assessment"])
            why = "" if a_["relevant"] else f" 対象外: {a_['relevant_reason']}"
            lines.append(f"  {r['score']:5.1f} {r['title'] or r['url']}{why}")
    return "\n".join(lines)
