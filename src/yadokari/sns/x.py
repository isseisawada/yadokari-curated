"""X の投稿文（2026-09-30）。URL は WP 側が公開のときに末尾へ足す（パーマリンクが決まるのはそのとき）。

X の文字数は重み付き: 半角英数などは1、日本語は2、URL は長さに関係なく23。上限280。
"""

from __future__ import annotations

import unicodedata

from yadokari.config import XConfig

LIMIT = 280
URL_WEIGHT = 23
# twitter-text の「重み1」の範囲
_LIGHT = ((0x0000, 0x10FF), (0x2000, 0x200D), (0x2010, 0x201F), (0x2032, 0x2037))


def weight(text: str) -> int:
    text = unicodedata.normalize("NFC", text)
    return sum(1 if any(a <= ord(c) <= b for a, b in _LIGHT) else 2 for c in text)


def _cut(text: str, budget: int) -> str:
    if weight(text) <= budget:
        return text
    out = ""
    for c in text:
        if weight(out + c + "…") > budget:
            break
        out += c
    return out.rstrip("、。 ") + "…"


def compose(cfg: XConfig, *, title: str, prefix: str, quote: str, tags: list[str]) -> str:
    """タイトル（【海外事例】は外す）＋ QUOTE ＋ ハッシュタグ。末尾の URL の分を空けておく。"""
    head = title.removeprefix(prefix).strip()
    tagline = " ".join(f"#{t}" for t in [t for t in cfg.hashtags if t in tags][: cfg.max_hashtags])
    budget = LIMIT - URL_WEIGHT - 2  # URL の前の改行2つ
    fixed = weight(tagline) + (2 if tagline else 0)
    head = _cut(head, max(budget - fixed, 20))
    text = head
    room = budget - fixed - weight(head) - 2
    if quote and room >= 40:
        text += "\n\n" + _cut(quote.strip(), room)
    if tagline:
        text += "\n\n" + tagline
    return text
