"""1枚目の写真（外観）を選ぶ。

元記事の1枚目が室内や図面のことがあるので、写真を見せて「建物の外観が一番よく分かる1枚」を選ばせる。
審査画面の1枚目・カードのサムネイル・下書きのアイキャッチに使う（2026-09-29 ユーザー要望）。

写真は URL のまま渡す（こちらからは取りに行かない）。元サイトが画像の取得を断ると
API が 400 を返すので、そのときは選ばずに元の並びのままにする。
"""

from __future__ import annotations

import json

import anthropic

from yadokari.config import Config
from yadokari.db.connection import DbConnection
from yadokari.llm import LLMError, call_json, make_client
from yadokari.logging_setup import get_logger

log = get_logger(__name__)

# 見せる枚数の上限。外観はたいてい最初の数枚にある。多いほど費用がかかる
MAX_CANDIDATES = 5

SYSTEM = """あなたは建築メディアの写真編集者です。
小さな家・キャビン・トレーラーハウスなどを紹介する記事の写真が、番号付きで何枚か渡されます。
記事の1枚目に置く写真として、**建物の外観がいちばんよく分かる1枚**を選んでください。

- 外観 = 建物の外側が写っていて、形や素材、周りの景色との関係が分かる写真
- 室内・ディテールの寄り・図面・人物だけの写真は選ばない
- 外観が複数あれば、建物全体が見えて、明るく、構図のよいものを選ぶ
- 外観が1枚も無ければ index を -1 にする"""

SCHEMA = {
    "type": "object",
    "properties": {
        "index": {"type": "integer", "description": "選んだ写真の番号（0始まり）。外観が無ければ -1"},
        "reason": {"type": "string", "description": "選んだ理由を日本語で1文"},
    },
    "required": ["index", "reason"],
    "additionalProperties": False,
}


def pick_exterior(config: Config, urls: list[str], client=None) -> tuple[str | None, str]:
    """外観の写真 URL と理由を返す。外観が無いときは (None, 理由)。"""
    candidates = urls[:MAX_CANDIDATES]
    if not candidates:
        return None, "写真がありません"
    content: list[dict] = []
    for i, u in enumerate(candidates):
        content.append({"type": "text", "text": f"写真 {i}"})
        content.append({"type": "image", "source": {"type": "url", "url": u}})
    content.append({"type": "text", "text": "外観がいちばんよく分かる写真の番号を選んでください。"})
    client = client or make_client(config.anthropic_api_key)
    data, _ = call_json(
        client, model=config.scoring.model, system=SYSTEM,
        messages=[{"role": "user", "content": content}], schema=SCHEMA,
        max_tokens=2000, effort=config.scoring.effort, fallbacks=config.scoring.fallbacks,
    )
    index = data.get("index", -1)
    if not isinstance(index, int) or not 0 <= index < len(candidates):
        return None, data.get("reason", "")
    return candidates[index], data.get("reason", "")


def pick_pending(config: Config, conn: DbConnection, limit: int = 60, client=None) -> tuple[int, int]:
    """審査ライン以上でまだ1枚目を選んでいない記事に、外観の写真を選ぶ。(選べた件数, 失敗件数)。"""
    rows = conn.execute(
        "SELECT id, source_url, image_urls FROM articles"
        " WHERE hero_image IS NULL AND status IN ('scored', 'approved') AND score >= ?"
        " ORDER BY score DESC LIMIT ?",
        (config.scoring.review_threshold, limit),
    ).fetchall()
    picked = failed = 0
    for row in rows:
        urls = json.loads(row["image_urls"] or "[]")
        try:
            url, reason = pick_exterior(config, urls, client=client)
        except anthropic.BadRequestError as exc:
            # 元サイトが画像の取得を断った（400）。次回も同じなので、選ばなかった印（''）を付けて
            # 元の並びのままにする
            log.warning("1枚目の選定に失敗（画像を取れない）: %s (%s)", row["source_url"], exc)
            set_hero(conn, row["id"], "")
            conn.commit()
            failed += 1
            continue
        except (LLMError, anthropic.APIError) as exc:
            # 混雑や一時的な失敗。印を付けずに残し、次の実行でもう一度試す
            log.warning("1枚目の選定に失敗（次回やり直す）: %s (%s)", row["source_url"], exc)
            failed += 1
            continue
        set_hero(conn, row["id"], url or "")
        conn.commit()
        if url:
            picked += 1
        log.info("1枚目: %s → %s（%s）", row["source_url"], url or "外観なし", reason)
    return picked, failed


def set_hero(conn: DbConnection, article_id: int, url: str) -> None:
    conn.execute("UPDATE articles SET hero_image = ? WHERE id = ?", (url, article_id))


def ordered_images(row) -> list[str]:
    """1枚目（外観）を先頭にした写真の並び。選んでいなければ元の並びのまま。"""
    images = json.loads(row["image_urls"] or "[]")
    hero = row["hero_image"] if "hero_image" in row.keys() else None
    if hero and hero in images:
        return [hero] + [u for u in images if u != hero]
    return images
