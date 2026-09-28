"""承認された記事から下書きを作る。**下書きまでが自動。公開の判断は必ず人。**

検算に落ちたら1回だけ書き直させる。それでも元資料に無い数字が残ったら、
下書きは保存するが warnings に数字を残し、審査画面で目立たせる（人が直す前提）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from yadokari.config import Config
from yadokari.db.connection import DbConnection, Row
from yadokari.db.repository import get_article, save_draft
from yadokari.drafting.numbers import unsupported_numbers
from yadokari.drafting.prompt import SCHEMA, SYSTEM, build_source, build_user
from yadokari.drafting.render import (
    DraftParts,
    body_chars,
    build_body,
    build_title,
    excerpt_of,
)
from yadokari.llm import call_json, make_client
from yadokari.logging_setup import get_logger

log = get_logger(__name__)


@dataclass
class Draft:
    title: str
    excerpt: str
    body_html: str
    tags: list[str]
    featured_image: str | None
    warnings: list[str]
    chars: int


def generate(config: Config, row: Row, client=None) -> Draft:
    d = config.drafting
    assessment = json.loads(row["assessment"]) if row["assessment"] else None
    text = row["content_text"] or ""
    source = build_source(row["title"], row["source_url"], text, assessment)
    # 検算の元資料は**元記事だけ**。LLM が抽出した事実は元資料に入れない
    # （抽出の段階で数字を作っていたら、それを正として通してしまうため）
    check_source = f"{row['title'] or ''}\n{text}"

    client = client or make_client(config.anthropic_api_key)
    messages: list[dict] = [{"role": "user", "content": build_user(source, d.target_chars)}]
    parts: DraftParts | None = None
    stray: set[str] = set()
    for attempt in range(2):
        data, _ = call_json(
            client, model=d.model, system=SYSTEM, messages=messages, schema=SCHEMA,
            max_tokens=d.max_tokens, effort=d.effort, fallbacks=d.fallbacks,
        )
        parts = DraftParts.from_json(data)
        stray = unsupported_numbers(parts.all_text(), check_source)
        if not stray:
            break
        if attempt == 0:
            log.warning("下書きに元資料に無い数字が出たので書き直させます: %s", "、".join(sorted(stray)))
            messages.append({"role": "assistant", "content": json.dumps(data, ensure_ascii=False)})
            messages.append({
                "role": "user",
                "content": (
                    f"次の数字は資料にありません: {'、'.join(sorted(stray))}。"
                    "換算や計算をせず、資料にある値だけを使って書き直してください。"
                    "資料に無い数字は、数字を使わない書き方にしてください。"
                ),
            })
    assert parts is not None

    images = json.loads(row["image_urls"] or "[]")[: d.max_images]
    featured = row["og_image"] or (images[0] if images else None)
    tags = (assessment or {}).get("suggested_tags") or []
    return Draft(
        title=build_title(d.title_prefix, parts),
        excerpt=excerpt_of(parts),
        body_html=build_body(parts, images, row["source_url"]),
        tags=tags,
        featured_image=featured,
        warnings=[f"元資料に無い数字: {n}" for n in sorted(stray)],
        chars=body_chars(parts),
    )


def generate_for(config: Config, conn: DbConnection, article_id: int, client=None) -> int:
    row = get_article(conn, article_id)
    if row is None:
        raise KeyError(f"記事 {article_id} がありません")
    if row["status"] != "approved":
        raise ValueError("承認済みの記事だけ下書きにできます")
    draft = generate(config, row, client=client)
    draft_id = save_draft(
        conn, article_id, title=draft.title, excerpt=draft.excerpt, body_html=draft.body_html,
        tags=draft.tags, featured_image=draft.featured_image, warnings=draft.warnings,
        model=config.drafting.model,
    )
    conn.commit()
    log.info("下書きを作りました: article=%s draft=%s（%d字）", article_id, draft_id, draft.chars)
    return draft_id
