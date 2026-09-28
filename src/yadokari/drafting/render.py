"""下書きの部品から、WP に入れる本文 HTML とタイトルを組み立てる。

形は既存記事に合わせる（2026-09-28 に 96036 の HTML を確認）:

    写真 → via: ドメイン        ← wp-caption のブロック
    リード 2段落
    写真 → via
    <h3><b>見出し</b></h3> → 段落 …（2〜3回）
    締め
    残りの写真
    via; 出典URL

写真は元サイトへの直リンク、キャプションは `via: ドメイン`（元記事へのリンク）。
既存記事と同じ運用（2026-09-28 ユーザー判断）。
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from urllib.parse import urlparse

from yadokari.drafting.numbers import plain


@dataclass
class DraftParts:
    catch: str
    subject: str
    name: str
    lead: list[str]
    sections: list[tuple[str, list[str]]]
    closing: list[str]

    @classmethod
    def from_json(cls, data: dict) -> DraftParts:
        def paras(xs) -> list[str]:
            return [p for p in (plain(str(x)) for x in xs or []) if p]

        return cls(
            catch=plain(str(data.get("catch") or "")),
            subject=plain(str(data.get("subject") or "")),
            name=plain(str(data.get("name") or "")),
            lead=paras(data.get("lead")),
            sections=[
                (plain(str(s.get("heading") or "")).strip("「」"), paras(s.get("paragraphs")))
                for s in data.get("sections") or []
            ],
            closing=paras(data.get("closing")),
        )

    def all_text(self) -> str:
        chunks = [self.catch, self.subject, self.name, *self.lead, *self.closing]
        for heading, ps in self.sections:
            chunks.append(heading)
            chunks.extend(ps)
        return "\n".join(chunks)


def via_domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def build_title(prefix: str, parts: DraftParts) -> str:
    catch = parts.catch.rstrip("。．. ")
    name = parts.name.strip("「」 ")
    tail = f"{parts.subject}「{name}」" if name else parts.subject
    return f"{prefix}{catch}。{tail}"


def _figure(src: str, source_url: str) -> str:
    return (
        '<div class="wp-caption alignnone">'
        f'<img loading="lazy" decoding="async" src="{escape(src, quote=True)}" alt="" />'
        f'<p class="wp-caption-text">via: <a href="{escape(source_url, quote=True)}">'
        f"{escape(via_domain(source_url))}</a></p></div>"
    )


def _p(text: str) -> str:
    return f"<p>{escape(text)}</p>"


def build_body(parts: DraftParts, images: list[str], source_url: str) -> str:
    imgs = list(images)
    out: list[str] = []

    def take() -> None:
        if imgs:
            out.append(_figure(imgs.pop(0), source_url))

    take()
    out.extend(_p(x) for x in parts.lead)
    take()
    for heading, paragraphs in parts.sections:
        out.append(f"<h3><b>{escape(heading)}</b></h3>")
        out.extend(_p(x) for x in paragraphs)
        take()
    out.extend(_p(x) for x in parts.closing)
    while imgs:
        take()
    link = escape(source_url, quote=True)
    out.append(
        f'<p>via;<br /><a href="{link}" target="_blank" rel="noopener">{escape(source_url)}</a></p>'
    )
    return "\n".join(out)


def excerpt_of(parts: DraftParts) -> str:
    """WP の抜粋。既存記事はリードの1段落目がそのまま入っている。"""
    return parts.lead[0] if parts.lead else ""


def body_chars(parts: DraftParts) -> int:
    text = "".join([*parts.lead, *parts.closing, *(p for _, ps in parts.sections for p in ps)])
    return len("".join(text.split()))
