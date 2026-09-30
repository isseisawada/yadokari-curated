"""下書きの部品から、WP に入れる本文 HTML とタイトルを組み立てる。

形は既存記事に合わせる（2026-09-28 に 96036 の HTML を確認）:

    写真 → via: ドメイン        ← wp-caption のブロック
    リード 2段落（1文目は「何か・誰が・どこで」の定義の文）
    写真 → via
    <h3><b>見出し</b></h3> → 段落 …（2〜3回）
    締め
    〈物件名〉のデータ（事実だけの一覧）        ← SEO/AIO（2026-09-29）
    よくある質問                              ← SEO/AIO（2026-09-29）
    残りの写真
    via: 出典URL
    関連：トレーラーハウス／小屋／タイニーハウスの記事一覧（内部リンク。この順で固定）

写真は元サイトへの直リンク、キャプションは `via: ドメイン`（元記事へのリンク）。
既存記事と同じ運用（2026-09-28 ユーザー判断）。alt は空にせず、物件名と種別を入れる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from urllib.parse import urlparse

from yadokari.drafting.numbers import plain

# データ欄に並べる事実（ラベル・assessment.facts のキー）
DATA_ROWS = [
    ("物件名", "name"),
    ("ビルダー", "builder"),
    ("設計", "architect"),
    ("国", "country"),
    ("地域", "region"),
    ("面積", "area"),
    ("サイズ", "dimensions"),
    ("価格", "price"),
    ("完成", "year"),
    ("写真", "photographer"),
]


@dataclass
class DraftParts:
    catch: str
    subject: str
    name: str
    lead: list[str]
    sections: list[tuple[str, list[str]]]
    closing: list[str]
    description: str = ""
    faq: list[tuple[str, str]] = field(default_factory=list)

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
            description=plain(str(data.get("description") or "")).replace("\n", ""),
            faq=[
                (plain(str(f.get("q") or "")), plain(str(f.get("a") or "")))
                for f in data.get("faq") or []
                if f.get("q") and f.get("a")
            ],
        )

    def all_text(self) -> str:
        chunks = [self.catch, self.subject, self.name, *self.lead, *self.closing, self.description]
        for heading, ps in self.sections:
            chunks.append(heading)
            chunks.extend(ps)
        for q, a in self.faq:
            chunks.extend([q, a])
        return "\n".join(chunks)


def via_domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def build_title(prefix: str, parts: DraftParts) -> str:
    catch = parts.catch.rstrip("。．. ")
    name = parts.name.strip("「」 ")
    tail = f"{parts.subject}「{name}」" if name else parts.subject
    return f"{prefix}{catch}。{tail}"


def _figure(src: str, source_url: str, alt: str) -> str:
    return (
        '<div class="wp-caption alignnone">'
        f'<img loading="lazy" decoding="async" src="{escape(src, quote=True)}" alt="{escape(alt, quote=True)}" />'
        f'<p class="wp-caption-text">via: <a href="{escape(source_url, quote=True)}">'
        f"{escape(via_domain(source_url))}</a></p></div>"
    )


def _p(text: str) -> str:
    return f"<p>{escape(text)}</p>"


def build_body(
    parts: DraftParts,
    images: list[str],
    source_url: str,
    *,
    facts: dict[str, str] | None = None,
    internal_links: dict[str, str] | None = None,
    link_keywords: list[str] | None = None,
    data_box: bool = True,
    faq: bool = True,
) -> str:
    imgs = list(images)
    total = len(imgs)
    name = parts.name.strip("「」 ") or parts.subject
    out: list[str] = []

    def take() -> None:
        if imgs:
            n = total - len(imgs) + 1
            alt = f"{parts.subject}「{name}」の写真 {n}/{total}" if parts.subject else f"「{name}」の写真 {n}/{total}"
            out.append(_figure(imgs.pop(0), source_url, alt))

    take()
    out.extend(_p(x) for x in parts.lead)
    take()
    for heading, paragraphs in parts.sections:
        out.append(f"<h3><b>{escape(heading)}</b></h3>")
        out.extend(_p(x) for x in paragraphs)
        take()
    out.extend(_p(x) for x in parts.closing)

    rows = [(label, (facts or {}).get(key, "")) for label, key in DATA_ROWS]
    rows = [(label, value) for label, value in rows if value]
    if data_box and rows:
        out.append(f"<h3><b>{escape(name)}のデータ</b></h3>")
        out.append("<ul>" + "".join(
            f"<li>{escape(label)}：{escape(value)}</li>" for label, value in rows
        ) + "</ul>")
    if faq and parts.faq:
        out.append(f"<h3><b>{escape(name)}のよくある質問</b></h3>")
        for q, a in parts.faq:
            out.append(f"<p><b>Q. {escape(q)}</b><br />A. {escape(a)}</p>")

    while imgs:
        take()
    link = escape(source_url, quote=True)
    # 2026-09-30 ユーザー指定: 「via: URL」を1行で（すべての記事）
    out.append(
        f'<p>via: <a href="{link}" target="_blank" rel="noopener">{escape(source_url)}</a></p>'
    )
    if internal_links and link_keywords:
        items = [
            f'<a href="{escape(internal_links[k], quote=True)}">{escape(k)}の記事一覧</a>'
            for k in link_keywords if k in internal_links
        ]
        if items:
            out.append("<p>関連：" + "／".join(items) + "</p>")
    return "\n".join(out)


def excerpt_of(parts: DraftParts) -> str:
    """WP の抜粋（＝ meta description）。説明文が無ければリードの1段落目。"""
    if parts.description:
        return parts.description
    return parts.lead[0] if parts.lead else ""


def body_chars(parts: DraftParts) -> int:
    text = "".join([*parts.lead, *parts.closing, *(p for _, ps in parts.sections for p in ps)])
    return len("".join(text.split()))
