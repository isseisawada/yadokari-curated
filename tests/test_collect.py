from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from yadokari.collect.runner import collect_source
from yadokari.config import Source
from yadokari.db.repository import exists_source_url
from yadokari.net.client import RobotsDisallowed

BODY = "<p>" + ("A tiny house on wheels with a loft and a porch. " * 20) + "</p>"


def _rss(items: list[tuple[str, str, datetime]], body: str = BODY) -> str:
    entries = "".join(
        f"<item><title>{t}</title><link>{u}</link>"
        f"<pubDate>{d.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>"
        f"<description><![CDATA[{body}]]></description></item>"
        for t, u, d in items
    )
    return f"<?xml version='1.0'?><rss version='2.0'><channel>{entries}</channel></rss>"


class FakeClient:
    """URL → 本文。robots で禁止する URL と、失敗させる URL を持てる。"""

    def __init__(self, pages: dict[str, str], disallow=(), fail=()):
        self.pages = pages
        self.disallow = set(disallow)
        self.fail = set(fail)
        self.requested: list[str] = []

    def get(self, url):
        self.requested.append(url)
        if url in self.disallow:
            raise RobotsDisallowed(url)
        if url in self.fail:
            raise httpx.HTTPError("boom")
        body = self.pages[url]
        return httpx.Response(200, content=body.encode(), request=httpx.Request("GET", url))


def _article(title="Cabin", imgs=5) -> str:
    img = "".join(f"<img src='/p/{i}.jpg'>" for i in range(imgs))
    return f"<html><head><meta property='og:title' content='{title}'></head><body><article>{img}{BODY}</article></body></html>"


NOW = datetime.now(UTC)


def test_feed_articles_are_inserted_once(config, db):
    src = Source(name="t", feed="https://t.com/feed")
    pages = {
        "https://t.com/feed": _rss([("A", "https://t.com/a", NOW), ("B", "https://t.com/b", NOW)]),
        "https://t.com/a": _article(),
        "https://t.com/b": _article(),
    }
    first = collect_source(config, FakeClient(pages), db, src)
    second = collect_source(config, FakeClient(pages), db, src)
    assert first.inserted == 2
    assert second.inserted == 0
    assert second.skipped_existing == 2
    assert db.execute("SELECT COUNT(*) AS n FROM articles").fetchone()["n"] == 2
    row = db.execute("SELECT * FROM articles WHERE source_url = 'https://t.com/a'").fetchone()
    assert row["image_count"] == 5


def test_existing_are_dropped_before_the_limit(config, db):
    """frmg で踏んだ穴: 上限で切ってから取得済みを落とすと、先頭が全部取得済みのとき永久に0件。"""
    src = Source(name="t", index_urls=["https://t.com/list"], url_include=r"/p/\d+$")
    links = "".join(f"<a href='/p/{i}'>x</a>" for i in range(1, 6))
    pages = {"https://t.com/list": f"<html><body>{links}</body></html>"}
    pages.update({f"https://t.com/p/{i}": _article() for i in range(1, 6)})
    assert collect_source(config, FakeClient(pages), db, src, limit=2).inserted == 2
    assert collect_source(config, FakeClient(pages), db, src, limit=2).inserted == 2
    assert collect_source(config, FakeClient(pages), db, src, limit=2).inserted == 1


def test_dry_run_does_not_count_feed_and_index_twice(config):
    src = Source(
        name="t", feed="https://t.com/feed", index_urls=["https://t.com/list"], url_include=r"/a$"
    )
    pages = {
        "https://t.com/feed": _rss([("A", "https://t.com/a", NOW)]),
        "https://t.com/list": "<a href='/a'>A</a>",
        "https://t.com/a": _article(),
    }
    stats = collect_source(config, FakeClient(pages), None, src, dry_run=True)
    assert stats.inserted == 1


def test_old_articles_are_skipped_without_fetching(config, db):
    src = Source(name="t", feed="https://t.com/feed")
    old = NOW - timedelta(days=config.collect.lookback_days + 5)
    client = FakeClient({"https://t.com/feed": _rss([("Old", "https://t.com/old", old)])})
    stats = collect_source(config, client, db, src)
    assert stats.skipped_old == 1
    assert "https://t.com/old" not in client.requested


def test_prefilter_skips_unrelated_without_fetching(config, db):
    src = Source(name="t", feed="https://t.com/feed", prefilter=True)
    unrelated = "<p>" + ("A new lamp design with brass details. " * 30) + "</p>"
    client = FakeClient({"https://t.com/feed": _rss([("Brass lamp", "https://t.com/lamp", NOW)], body=unrelated)})
    stats = collect_source(config, client, db, src)
    assert stats.skipped_prefilter == 1
    assert "https://t.com/lamp" not in client.requested


def test_feed_only_source_uses_feed_body_and_never_fetches_pages(config, db):
    src = Source(name="dz", feed="https://dz.com/feed", fetch_article=False)
    body = "<img src='https://static.dz.com/1.jpg'>" + BODY
    client = FakeClient({"https://dz.com/feed": _rss([("Cabin", "https://dz.com/c", NOW)], body=body)})
    stats = collect_source(config, client, db, src)
    assert stats.inserted == 1
    assert client.requested == ["https://dz.com/feed"]


def test_one_failure_does_not_stop_the_source(config, db):
    src = Source(name="t", feed="https://t.com/feed")
    pages = {
        "https://t.com/feed": _rss([
            ("A", "https://t.com/a", NOW), ("B", "https://t.com/b", NOW), ("C", "https://t.com/c", NOW),
        ]),
        "https://t.com/c": _article(),
    }
    client = FakeClient(pages, disallow={"https://t.com/a"}, fail={"https://t.com/b"})
    stats = collect_source(config, client, db, src)
    assert stats.failed == 2
    assert stats.inserted == 1
    assert exists_source_url(db, "https://t.com/c")


def test_unreadable_feed_is_logged_not_raised(config, db):
    src = Source(name="t", feed="https://t.com/feed")
    stats = collect_source(config, FakeClient({}, fail={"https://t.com/feed"}), db, src)
    assert stats.inserted == 0


def test_source_lookback_overrides_default(config, db):
    src = Source(name="t", feed="https://t.com/feed", lookback_days=365)
    old = NOW - timedelta(days=config.collect.lookback_days + 60)
    pages = {"https://t.com/feed": _rss([("Old", "https://t.com/old", old)]), "https://t.com/old": _article()}
    assert collect_source(config, FakeClient(pages), db, src).inserted == 1
