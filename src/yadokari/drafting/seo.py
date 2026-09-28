"""下書きの SEO / AIO チェック。下書き画面に出し、人が直したあとも毎回測り直す。

**合格は上位表示の保証ではない。** 検索順位は競合・被リンク・サイト全体の評価で決まる。
ここで見るのは、記事の側でできることが抜けていないかだけ。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape

from yadokari.config import SeoConfig


@dataclass
class Check:
    label: str
    ok: bool
    detail: str = ""


def _text(html: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", html))


def _first_paragraph(body_html: str) -> str:
    for m in re.finditer(r"<p>(.*?)</p>", body_html, re.S):
        t = _text(m.group(1)).strip()
        if t and not t.startswith(("via", "関連")):
            return t
    return ""


def checks(cfg: SeoConfig, *, title: str, excerpt: str, body_html: str, tags: list[str],
           keyword: str) -> list[Check]:
    out: list[Check] = []
    out.append(Check(f"タイトルに「{keyword}」", keyword in title))
    out.append(Check("タイトルが長すぎない（60字以内）", len(title) <= 60, f"{len(title)}字"))
    n = len(excerpt)
    out.append(Check(
        f"説明文（meta description）が{cfg.description_min}〜{cfg.description_max}字",
        cfg.description_min <= n <= cfg.description_max, f"{n}字",
    ))
    out.append(Check(f"説明文に「{keyword}」", keyword in excerpt))
    first = _first_paragraph(body_html)
    first_sentence = first.split("。")[0]
    out.append(Check(f"リードの1文目に「{keyword}」（AI の要約に引かれやすい定義の文）",
                     keyword in first_sentence, first_sentence[:40]))
    headings = [_text(h) for h in re.findall(r"<h3>(.*?)</h3>", body_html, re.S)]
    out.append(Check(f"見出しのどれかに「{keyword}」", any(keyword in h for h in headings)))
    body = _text(body_html)
    count = body.count(keyword)
    out.append(Check(f"本文で「{keyword}」が3〜8回（多すぎも不自然）", 3 <= count <= 8, f"{count}回"))
    required = list(cfg.required_tags) + (list(cfg.trailer_tags) if keyword == "トレーラーハウス" else [])
    missing = [t for t in required if t not in tags]
    out.append(Check("必須タグ（" + "・".join(required) + "）", not missing,
                     "足りない: " + "・".join(missing) if missing else ""))
    imgs = re.findall(r"<img[^>]*>", body_html)
    empty = sum(1 for i in imgs if re.search(r'alt=""', i) or "alt=" not in i)
    out.append(Check("写真の alt が空でない", empty == 0, f"空 {empty}/{len(imgs)}"))
    chars = len("".join(body.split()))
    out.append(Check("本文 1,200字以上", chars >= 1200, f"{chars}字"))
    out.append(Check("データ欄（事実の一覧）がある", "のデータ</b></h3>" in body_html))
    out.append(Check("内部リンク（記事一覧）がある", "の記事一覧</a>" in body_html))
    return out


def failed(results: list[Check]) -> list[str]:
    return [f"SEO: {c.label}" + (f"（{c.detail}）" if c.detail else "") for c in results if not c.ok]
