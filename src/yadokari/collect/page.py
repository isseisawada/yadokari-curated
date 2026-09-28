"""記事HTMLから本文・画像・写真クレジットを取り出す。

frmg の collect/base.py を元にしている。変えたところ:
  - 販売シグナルの検出は持ってこない（売出中かどうかはこのメディアの軸ではない）
  - 画像は代表1枚ではなく**本文中の全部**を順番どおりに取る。記事は
    「写真 → via → 本文」の繰り返しで組むので、枚数と順番が要る
  - 写真クレジット（Photos by … / © … / Courtesy of …）の原文を控える
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

_STRIP_TAGS = ("script", "style", "noscript", "svg", "form", "iframe")
_CHROME_TAGS = ("nav", "aside", "footer", "header")

_ARTICLE_SELECTORS = (
    "article",
    "main",
    "[role='main']",
    "[itemprop='articleBody']",
    ".entry-content",
    ".post-content",
    ".article-content",
    ".article-body",
)

# 記事要素の内側に混ざる、記事ごとに内容が変わらないブロック。
# frmg で関連記事の見出しや価格を本文として拾った実例がある。
_NOISE_TOKENS = re.compile(
    r"(?:^|[-_\s])(?:"
    r"related|recirc\w*|recommend\w*|more-?stories|most-?recent|read-?more|"
    r"trending|popular|newsletter|subscribe|promo|advert\w*|"
    r"author-?bio|byline-?bio|share|social|comments?"
    r")(?:[-_\s]|$)",
    re.IGNORECASE,
)

# 画像のURLにこれが入っていたら写真ではない
_IMAGE_NOISE = re.compile(
    r"(logo|icon|avatar|sprite|badge|banner|advert|placeholder|favicon|"
    r"share|social|1x1|spacer|pixel|gravatar)",
    re.IGNORECASE,
)
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
# WordPress のリサイズ版（-1024x683.jpg）や @2x は同じ写真として数える
_SIZE_SUFFIX = re.compile(r"(-\d{2,5}x\d{2,5}|-scaled|[@_-][23]x)(?=\.[a-z]{3,4}$)", re.I)

CREDIT = re.compile(
    r"(?:photo(?:graph(?:y|s|er))?s?\s*(?:by|:|©|courtesy)|courtesy of|"
    r"images?\s*(?:by|:|courtesy)|©)\s*[^\n|]{2,80}",
    re.IGNORECASE,
)
# サイト共通の著作権表示（© 2026 Tiny Living）は写真クレジットではない
_SITE_COPYRIGHT = re.compile(r"©\s*(?:\d{4}|copyright)", re.I)

# `<script>` の中の JSON に入っている画像URL。
# **ギャラリーを JS で描くサイトは、HTML の <img> に数枚しか出ない。**
# 実例（2026-09-28、ArchDaily）: 本文の <img> は2枚、ページ内の JSON には24枚。
# frmg でも Coldwell Banker で同じ形（19枚中3枚しか取れていなかった）。
_JSON_IMAGE = re.compile(
    r"https?://[^\s\"'<>\\]+\.(?:jpg|jpeg|png|webp)(?:\?[^\s\"'<>\\]*)?", re.IGNORECASE
)
# 本文の <img> がこれ未満なら、JSON からも拾う（既存記事の最少が4枚）
GALLERY_FALLBACK_BELOW = 4

MIN_ARTICLE_CHARS = 400
MAX_TEXT_CHARS = 20000


@dataclass
class Page:
    title: str | None
    text: str
    images: list[str] = field(default_factory=list)
    og_image: str | None = None
    credit: str | None = None
    published_at: str | None = None


def normalize_url(url: str) -> str:
    """フラグメントを落とし、末尾のスラッシュ差を吸収する（同じ記事の二重登録を防ぐ）。"""
    url, _ = urldefrag(url.strip())
    parsed = urlparse(url)
    path = parsed.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return parsed._replace(path=path).geturl()


def _strip_noise(node) -> None:
    targets = [
        el for el in node.find_all(True)
        if el.attrs is not None
        and _NOISE_TOKENS.search(" ".join(el.get("class") or []) + " " + (el.get("id") or ""))
    ]
    for el in targets:
        if el.parent is not None:
            el.decompose()


def _article_root(soup):
    for selector in _ARTICLE_SELECTORS:
        node = soup.select_one(selector)
        if node is None:
            continue
        for tag in node(_CHROME_TAGS):
            tag.decompose()
        _strip_noise(node)
        if len(node.get_text(strip=True)) >= MIN_ARTICLE_CHARS:
            return node
    for tag in soup(_CHROME_TAGS):
        tag.decompose()
    _strip_noise(soup)
    return soup.body or soup


def _body_text(root) -> str:
    """地の文は <p> に入る。<p> だけ集めると関連記事の見出しの羅列が落ちる。
    短すぎるときは全体に戻す（<p> を使わないページのため）。"""
    paragraphs = [p.get_text(" ", strip=True) for p in root.find_all("p")]
    text = " ".join(" ".join(paragraphs).split())
    if len(text) >= MIN_ARTICLE_CHARS:
        return text
    return " ".join(root.get_text(separator=" ").split())


def _img_src(img, base_url: str) -> str | None:
    """遅延読み込み（data-src / srcset）にも対応する。srcset は一番大きいものを取る。"""
    for attr in ("data-src", "data-lazy-src", "data-original", "src"):
        value = (img.get(attr) or "").strip()
        if value and not value.startswith("data:"):
            return urljoin(base_url, value)
    srcset = img.get("srcset") or img.get("data-srcset") or ""
    best, best_w = None, -1
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        width = int(bits[1][:-1]) if len(bits) > 1 and bits[1].endswith("w") and bits[1][:-1].isdigit() else 0
        if width > best_w:
            best, best_w = bits[0], width
    return urljoin(base_url, best) if best else None


def _image_key(url: str) -> str:
    path = urlparse(url).path.lower()
    return _SIZE_SUFFIX.sub("", path)


def extract_images(root, base_url: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for img in root.find_all("img"):
        src = _img_src(img, base_url)
        if not src or _IMAGE_NOISE.search(src):
            continue
        path = urlparse(src).path.lower()
        # 拡張子の無い CDN（Squarespace・Dwell の画像サーバ）もあるので、
        # 拡張子が付いていて画像でないものだけを落とす
        if "." in path.rsplit("/", 1)[-1] and not path.endswith(_IMAGE_EXT):
            continue
        width = img.get("width")
        if width and str(width).isdigit() and int(width) < 200:
            continue
        key = _image_key(src)
        if key in seen:
            continue
        seen.add(key)
        urls.append(src)
    return urls


def _size_rank(url: str) -> int:
    path = urlparse(url).path.lower()
    for rank, word in ((4, "original"), (3, "large"), (2, "medium"), (1, "newsletter")):
        if word in path:
            return rank
    return 0 if "thumb" in path or "small" in path else 1


def gallery_from_scripts(raw_html: str, og_image: str | None) -> list[str]:
    """ページ内の JSON から、og:image と同じホストの画像を拾う。

    同じ写真がサイズ違いで何度も出てくる（ArchDaily は thumb_jpg / medium_jpg /
    large_jpg）。ファイル名が同じものは1枚と数え、大きいサイズ（large を含む）を選ぶ。
    """
    if not og_image:
        return []
    host = urlparse(og_image).netloc
    best: dict[str, str] = {}
    order: list[str] = []
    for raw in _JSON_IMAGE.findall(raw_html.replace("\\/", "/")):
        if urlparse(raw).netloc != host or _IMAGE_NOISE.search(raw):
            continue
        name = _SIZE_SUFFIX.sub("", urlparse(raw).path.rsplit("/", 1)[-1].lower())
        if name not in best:
            best[name] = raw
            order.append(name)
        elif _size_rank(raw) > _size_rank(best[name]):
            best[name] = raw
    return [best[n] for n in order]


def find_credit(text: str) -> str | None:
    for m in CREDIT.finditer(text):
        found = " ".join(m.group(0).split())
        if _SITE_COPYRIGHT.match(found):
            continue
        return found[:120]
    return None


def parse_page(html: str, base_url: str) -> Page:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(_STRIP_TAGS):
        tag.decompose()

    title = None
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()
    elif soup.title and soup.title.string:
        title = soup.title.string.strip()

    og_image = None
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        og_image = urljoin(base_url, og["content"].strip())

    published = None
    meta_time = soup.find("meta", property="article:published_time")
    if meta_time and meta_time.get("content"):
        published = meta_time["content"].strip()

    root = _article_root(soup)
    text = _body_text(root)[:MAX_TEXT_CHARS]
    images = extract_images(root, base_url)
    if len(images) < GALLERY_FALLBACK_BELOW:
        gallery = gallery_from_scripts(html, og_image)
        if len(gallery) > len(images):
            images = gallery
    credit = find_credit(root.get_text("\n"))
    return Page(
        title=title, text=text, images=images, og_image=og_image, credit=credit,
        published_at=published,
    )


def parse_fragment(html: str, base_url: str) -> Page:
    """フィードの本文（HTML断片）を記事ページと同じ形にする。Dezeen 用。"""
    soup = BeautifulSoup(f"<article>{html}</article>", "lxml")
    for tag in soup(_STRIP_TAGS):
        tag.decompose()
    root = soup.find("article") or soup
    text = _body_text(root)[:MAX_TEXT_CHARS]
    return Page(
        title=None, text=text, images=extract_images(root, base_url),
        credit=find_credit(root.get_text("\n")),
    )
