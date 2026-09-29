"""媒体をまたいだ重複（同じ作品）を見つける。

2026-09-29、ArchDaily「Red Submarine Cabin / Wiki World」と designboom「compact forest cabin by
wiki world takes shape as a red wooden submarine」が別々に審査に出た。採点で抜き出した
物件名と設計者（facts）を見比べて、後から入った方に duplicate_of（先の記事の id）を付ける。

LLM は使わない。採点の結果（facts）とタイトルの言葉だけで判定する。
- 物件名が同じ（片方がもう片方を含む）で、設計者・ビルダーが食い違わない
- または、設計者・ビルダーが同じで、タイトルの言葉の大半が重なる
"""

from __future__ import annotations

import json
import re
import unicodedata

from yadokari.db.connection import DbConnection
from yadokari.logging_setup import get_logger

log = get_logger(__name__)

STOPWORDS = {
    "a", "an", "the", "by", "of", "in", "on", "at", "to", "for", "and", "with", "as", "is", "its",
    "into", "from", "that", "this", "takes", "shape", "makes", "offers", "new", "tiny", "house",
    "home", "cabin", "cabins", "design", "designed", "architecture", "architects", "studio",
}
MIN_NAME_KEY = 6  # 「Cabin」「Hut」だけのような短い名前では判定しない


def _key(text: str | None) -> str:
    """比較用のキー。括弧の中を落とし、英数字（と日本語の文字）だけを小文字で残す。"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[（(\[【].*?[）)\]】]", "", text)
    return re.sub(r"[\W_]+", "", text.lower())


def _words(title: str | None) -> set[str]:
    words = re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", title or "").lower())
    return {w for w in words if len(w) > 1 and w not in STOPWORDS}


def _designers(facts: dict) -> set[str]:
    return {k for k in (_key(facts.get("architect")), _key(facts.get("builder"))) if len(k) >= 3}


def _profile(row) -> dict:
    assessment = json.loads(row["assessment"]) if row["assessment"] else {}
    facts = assessment.get("facts") or {}
    return {
        "id": row["id"],
        "name": _key(facts.get("name")),
        "designers": _designers(facts),
        "words": _words(row["title"]),
    }


def same_work(a: dict, b: dict) -> bool:
    designers_clash = bool(a["designers"] and b["designers"] and not (a["designers"] & b["designers"]))
    if designers_clash:
        return False
    na, nb = a["name"], b["name"]
    if len(na) >= MIN_NAME_KEY and len(nb) >= MIN_NAME_KEY and (na in nb or nb in na):
        return True
    if a["designers"] & b["designers"]:
        small = min(len(a["words"]), len(b["words"]))
        if small >= 2 and len(a["words"] & b["words"]) / small >= 0.6:
            return True
    return False


def mark_duplicates(conn: DbConnection) -> int:
    """重複に duplicate_of を付ける。付けた件数を返す。何度走らせても同じ結果になる。

    審査の済んだもの（承認・非承認）には付けない。人が判断した記事を一覧から消さないため。
    重複の相手には、先に入った記事（id が小さい方）を選ぶ。
    """
    rows = conn.execute(
        "SELECT id, title, assessment, status, duplicate_of FROM articles"
        " WHERE assessment IS NOT NULL ORDER BY id"
    ).fetchall()
    profiles = [(_profile(r), r) for r in rows]
    marked = 0
    for i, (p, row) in enumerate(profiles):
        if row["duplicate_of"] is not None or row["status"] not in ("collected", "scored"):
            continue
        for q, earlier in profiles[:i]:
            if earlier["duplicate_of"] is None and same_work(p, q):
                conn.execute("UPDATE articles SET duplicate_of = ? WHERE id = ?", (q["id"], p["id"]))
                log.info("重複: #%s は #%s と同じ作品", p["id"], q["id"])
                marked += 1
                break
    return marked
