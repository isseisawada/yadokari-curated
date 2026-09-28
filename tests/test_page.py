from __future__ import annotations

from yadokari.collect.page import (
    find_credit,
    gallery_from_scripts,
    normalize_url,
    parse_fragment,
    parse_page,
)

BODY = "<p>" + ("A small cabin in the woods with a pitched roof. " * 20) + "</p>"


def _html(inner: str, head: str = "") -> str:
    return f"<html><head>{head}</head><body><nav><img src='/logo.png'></nav><article>{inner}</article></body></html>"


def test_normalize_url_drops_fragment_and_trailing_slash():
    assert normalize_url("https://a.com/x/#gallery") == "https://a.com/x"


def test_images_in_order_and_deduped_across_sizes():
    html = _html(
        "<img src='/wp/cabin-1-1024x683.jpg'>"
        + BODY
        + "<img src='/wp/cabin-1.jpg'><img data-src='/wp/cabin-2.jpg' src='data:image/gif;base64,xx'>"
    )
    page = parse_page(html, "https://a.com/post")
    assert page.images == ["https://a.com/wp/cabin-1-1024x683.jpg", "https://a.com/wp/cabin-2.jpg"]


def test_navigation_logos_and_tiny_images_are_not_photos():
    html = _html(BODY + "<img src='/avatar/me.jpg'><img src='/x.jpg' width='40'><img src='/ok.jpg'>")
    assert parse_page(html, "https://a.com/p").images == ["https://a.com/ok.jpg"]


def test_srcset_takes_the_largest():
    html = _html(BODY + "<img srcset='/s.jpg 300w, /l.jpg 1600w, /m.jpg 800w'>")
    assert parse_page(html, "https://a.com/p").images == ["https://a.com/l.jpg"]


def test_extensionless_cdn_images_are_kept():
    # Squarespace / Dwell の画像サーバは拡張子が無い
    html = _html(BODY + "<img src='https://images.dwell.com/photos/123/abc'>")
    assert parse_page(html, "https://a.com/p").images == ["https://images.dwell.com/photos/123/abc"]


def test_related_block_is_not_body_text():
    html = _html(BODY + "<div class='related-posts'><p>Mansion for $9 million</p></div>")
    assert "$9 million" not in parse_page(html, "https://a.com/p").text


def test_gallery_in_scripts_is_used_when_img_tags_are_few():
    # ArchDaily: <img> は2枚、JSON に24枚（2026-09-28 実測）
    og = "https://images.adsttc.com/media/images/1/large_jpg/cabin_1.jpg"
    script = "".join(
        f'"url":"https:\\/\\/images.adsttc.com\\/media\\/images\\/{i}\\/thumb_jpg\\/cabin_{i}.jpg",'
        f'"big":"https:\\/\\/images.adsttc.com\\/media\\/images\\/{i}\\/large_jpg\\/cabin_{i}.jpg",'
        for i in range(1, 7)
    )
    html = (
        f"<html><head><meta property='og:image' content='{og}'></head><body><article>"
        f"<img src='{og}'>{BODY}</article><script>var g={{{script}}}</script></body></html>"
    )
    page = parse_page(html, "https://www.archdaily.com/1/cabin")
    assert len(page.images) == 6
    assert all("/large_jpg/" in u for u in page.images)


def test_gallery_ignores_other_hosts():
    html = '"https://ads.example.com/banner.jpg" "https://cdn.a.com/p/one.jpg"'
    assert gallery_from_scripts(html, "https://cdn.a.com/p/og.jpg") == ["https://cdn.a.com/p/one.jpg"]


def test_credit_is_found_but_site_copyright_is_not():
    assert find_credit("Some text\nPhotos by Ema Peter\nmore") == "Photos by Ema Peter"
    assert find_credit("© 2026 Tiny Living. All rights reserved") is None
    assert find_credit("© Tom Alarcón") == "© Tom Alarcón"


def test_feed_fragment_is_parsed_like_a_page():
    page = parse_fragment(f"<img src='https://static.dezeen.com/a.jpg'>{BODY}", "https://www.dezeen.com/x")
    assert page.images == ["https://static.dezeen.com/a.jpg"]
    assert "small cabin" in page.text
