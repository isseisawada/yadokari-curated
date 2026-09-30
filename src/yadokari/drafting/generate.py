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
from yadokari.drafting.prompt import SCHEMA, build_source, build_user, system_prompt
from yadokari.drafting.render import (
    DraftParts,
    body_chars,
    build_body,
    build_title,
    excerpt_of,
)
from yadokari.drafting.seo import checks, failed
from yadokari.llm import call_json, make_client
from yadokari.logging_setup import get_logger
from yadokari.scoring.hero import ordered_images

log = get_logger(__name__)

RELATED_ORDER = ("トレーラーハウス", "小屋", "タイニーハウス")


@dataclass
class Draft:
    title: str
    excerpt: str
    body_html: str
    tags: list[str]
    featured_image: str | None
    warnings: list[str]
    chars: int


def keyword_for(config: Config, assessment: dict | None) -> str:
    return config.seo.keyword_for((assessment or {}).get("kind", ""))


def checked_facts(facts: dict[str, str], check_source: str) -> tuple[dict[str, str], list[str]]:
    """データ欄に出す事実。**元記事に無い数字を含む値は出さない**（抽出の段階の誤りを本文に持ち込まない）。"""
    ok: dict[str, str] = {}
    dropped: list[str] = []
    for key, value in facts.items():
        if not value:
            continue
        if unsupported_numbers(value, check_source):
            dropped.append(f"データ欄から外した（元記事に無い数字）: {key}={value}")
            continue
        ok[key] = value
    return ok, dropped


def generate(config: Config, row: Row, client=None) -> Draft:
    d = config.drafting
    seo = config.seo
    assessment = json.loads(row["assessment"]) if row["assessment"] else None
    keyword = keyword_for(config, assessment)
    text = row["content_text"] or ""
    source = build_source(row["title"], row["source_url"], text, assessment)
    # 検算の元資料は**元記事だけ**。LLM が抽出した事実は元資料に入れない
    # （抽出の段階で数字を作っていたら、それを正として通してしまうため）
    check_source = f"{row['title'] or ''}\n{text}"

    client = client or make_client(config.anthropic_api_key)
    system = system_prompt(seo.description_min, seo.description_max)
    messages: list[dict] = [{"role": "user", "content": build_user(source, d.target_chars, keyword)}]
    parts: DraftParts | None = None
    stray: set[str] = set()
    for attempt in range(2):
        data, _ = call_json(
            client, model=d.model, system=system, messages=messages, schema=SCHEMA,
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

    facts, dropped = checked_facts((assessment or {}).get("facts") or {}, check_source)
    # 1枚目は外観（scoring/hero.py で選んだもの）。選んでいなければ元記事の og:image
    images = ordered_images(row)[: d.max_images]
    featured = row["hero_image"] or row["og_image"] or (images[0] if images else None)
    tags = seo.tags_for((assessment or {}).get("kind", ""),
                        (assessment or {}).get("suggested_tags") or [])
    # 関連リンクは「トレーラーハウス／小屋／タイニーハウス」の順で3つとも（2026-09-30 ユーザー指定）
    link_keywords = [k for k in RELATED_ORDER if k in seo.internal_links]
    title = build_title(d.title_prefix, parts)
    excerpt = excerpt_of(parts)
    body = build_body(
        parts, images, row["source_url"], facts=facts, internal_links=seo.internal_links,
        link_keywords=link_keywords, data_box=seo.data_box, faq=seo.faq,
    )
    warnings = [f"元資料に無い数字: {n}" for n in sorted(stray)] + dropped
    warnings += failed(checks(seo, title=title, excerpt=excerpt, body_html=body, tags=tags,
                              keyword=keyword))
    return Draft(
        title=title,
        excerpt=excerpt,
        body_html=body,
        tags=tags,
        featured_image=featured,
        warnings=warnings,
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
