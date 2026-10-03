"""収集。フィード（RSS/Atom）を先に読み、一覧ページで取りこぼしを拾う。

    フィード → 一覧ページ（index_urls）→ 取得済みを落とす → 上限で切る
      → 記事ページ（fetch_article のときだけ）→ articles に入れる

frmg で踏んだ穴をそのまま避けている:
  - **上限で切ってから取得済みを落とすと永久に進まない。** 一覧の先頭N件が
    全部取得済みだと毎回0件で終わる。取得済みを先に落としてから切る
  - **--dry-run では DB に書かないので、取得済みの判定が効かない。**
    同じ記事をフィードと一覧の両方から二度数えないよう、この実行で見たURLも除く
  - robots.txt の Disallow・取得失敗は1件スキップで続け、ソースごと止めない
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup

from yadokari.collect.page import Page, normalize_url, parse_fragment, parse_page
from yadokari.config import Config, Source
from yadokari.db.connection import DbConnection
from yadokari.db.repository import NewArticle, exists_source_url, insert_article
from yadokari.logging_setup import get_logger
from yadokari.net.client import HttpClient, RobotsDisallowed

log = get_logger(__name__)

# 小さな家・動く家に当たりそうな語。prefilter: true のソースで、タイトルと冒頭に
# これが無い記事は採点に回さない（LLM の費用を抑えるため。判定そのものは LLM がする）
KEYWORDS = re.compile(
    r"\b(tiny|cabins?|trailers?|caravans?|vans?|vanlife|campers?|motorhomes?|"
    r"prefab(?:ricated)?|modular|containers?|micro|small (?:house|home)s?|huts?|sheds?|"
    r"tree ?houses?|houseboats?|floating|off-?grid|a-frames?|bothy|adu|granny flat|"
    r"yurts?|geodesic domes?|mobile homes?|pods?)\b",
    re.I,
)


@dataclass
class Item:
    url: str
    title: str | None = None
    published_at: str | None = None
    summary_html: str | None = None


@dataclass
class SourceStats:
    source: str
    feed_items: int = 0
    index_items: int = 0
    skipped_old: int = 0
    skipped_existing: int = 0
    skipped_prefilter: int = 0
    skipped_short: int = 0
    failed: int = 0
    inserted: int = 0
    inserted_urls: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.source}: 新規 {self.inserted} 件"
            f"（フィード {self.feed_items} / 一覧 {self.index_items}・"
            f"取得済み {self.skipped_existing}・古い {self.skipped_old}・"
            f"対象外 {self.skipped_prefilter}・本文不足 {self.skipped_short}・失敗 {self.failed}）"
        )


def _entry_date(entry) -> str | None:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime(*t[:6], tzinfo=UTC).isoformat() if t else None


def _entry_html(entry) -> str:
    contents = entry.get("content") or []
    body = " ".join(c.get("value", "") for c in contents)
    return body or entry.get("summary", "") or ""


def read_feed(client: HttpClient, source: Source, url: str | None = None) -> list[Item]:
    response = client.get(url or source.feed)
    parsed = feedparser.parse(response.content)
    items = []
    for e in parsed.entries:
        link = e.get("link")
        if not link:
            continue
        items.append(
            Item(
                url=normalize_url(link), title=e.get("title"), published_at=_entry_date(e),
                summary_html=_entry_html(e),
            )
        )
    return items


def read_index(client: HttpClient, source: Source, index_url: str) -> list[Item]:
    response = client.get(index_url)
    soup = BeautifulSoup(response.text, "lxml")
    pattern = re.compile(source.url_include or ".*")
    seen: list[str] = []
    for a in soup.find_all("a", href=True):
        url = normalize_url(urljoin(index_url, a["href"]))
        if pattern.search(url) and url not in seen:
            seen.append(url)
    return [Item(url=u) for u in seen]


def _too_old(item: Item, cutoff: datetime) -> bool:
    if not item.published_at:
        return False
    try:
        when = datetime.fromisoformat(item.published_at)
    except ValueError:
        return False
    if when.tzinfo is None:  # ページによってはタイムゾーンが無い（New Atlas）。UTC とみなす
        when = when.replace(tzinfo=UTC)
    return when < cutoff


def _passes_prefilter(item: Item, page: Page | None) -> bool:
    head = " ".join(
        x for x in (
            item.title or "",
            (page.title if page else "") or "",
            BeautifulSoup(item.summary_html or "", "lxml").get_text(" ")[:1500],
            (page.text[:1500] if page else ""),
        ) if x
    )
    return bool(KEYWORDS.search(head))


def collect_source(
    config: Config,
    client: HttpClient,
    conn: DbConnection | None,
    source: Source,
    limit: int | None = None,
    dry_run: bool = False,
) -> SourceStats:
    stats = SourceStats(source=source.name)
    if source.manual_only:
        # 名前を指定して呼ばれても取りに行かない（利用規約で自動収集が禁止）
        log.info("%s: 手動投入のみのソースなので自動収集しません", source.name)
        return stats
    limit = limit or config.collect.per_source_limit
    cutoff = datetime.now(UTC) - timedelta(days=source.lookback_days or config.collect.lookback_days)

    items: list[Item] = []
    seen: set[str] = set()
    if source.feed:
        try:
            for it in read_feed(client, source):
                if it.url not in seen:
                    seen.add(it.url)
                    items.append(it)
                    stats.feed_items += 1
        except (RobotsDisallowed, httpx.HTTPError) as exc:
            log.warning("%s: フィードを読めませんでした: %s", source.name, exc)
    for index_url in source.index_urls:
        try:
            for it in read_index(client, source, index_url):
                if it.url not in seen:
                    seen.add(it.url)
                    items.append(it)
                    stats.index_items += 1
        except (RobotsDisallowed, httpx.HTTPError) as exc:
            log.warning("%s: 一覧ページを読めませんでした（%s）: %s", source.name, index_url, exc)

    _ingest(config, client, conn, source, items, cutoff, limit, stats, dry_run)
    return stats


def _ingest(config: Config, client: HttpClient, conn: DbConnection | None, source: Source,
            items: list[Item], cutoff: datetime, limit: int, stats: SourceStats,
            dry_run: bool) -> None:
    # 取得済みと古いものを先に落とし、それから上限で切る
    fresh: list[Item] = []
    for it in items:
        if _too_old(it, cutoff):
            stats.skipped_old += 1
            continue
        if conn is not None and exists_source_url(conn, it.url):
            stats.skipped_existing += 1
            continue
        fresh.append(it)
    fresh = fresh[:limit]

    for it in fresh:
        # 記事ページを取る前に、フィードの情報だけで絞れるものは絞る
        # （一覧ページ由来はタイトルが無いので、ページを取ってから判定する）
        if source.prefilter and (it.title or it.summary_html) and not _passes_prefilter(it, None):
            stats.skipped_prefilter += 1
            continue
        try:
            if source.fetch_article:
                page = parse_page(client.get(it.url).text, it.url)
            else:
                page = parse_fragment(it.summary_html or "", it.url)
        except RobotsDisallowed:
            stats.failed += 1
            continue
        except httpx.HTTPError as exc:
            log.warning("%s: 記事ページを取れませんでした: %s (%s)", source.name, it.url, exc)
            stats.failed += 1
            continue

        if source.prefilter and not _passes_prefilter(it, page):
            stats.skipped_prefilter += 1
            continue
        if len(page.text) < config.collect.min_text_chars:
            stats.skipped_short += 1
            continue
        if page.published_at and not it.published_at:
            it.published_at = page.published_at
        if _too_old(it, cutoff):
            stats.skipped_old += 1
            continue

        article = NewArticle(
            source=source.name,
            source_url=it.url,
            title=it.title or page.title,
            published_at=it.published_at,
            content_text=page.text,
            image_urls=page.images,
            # フィードだけで回すソース（Dezeen）は og:image が無いので本文の1枚目で代える
            og_image=page.og_image or (page.images[0] if page.images else None),
            photo_credit=page.credit,
        )
        if dry_run or conn is None:
            stats.inserted += 1
            stats.inserted_urls.append(it.url)
            continue
        if insert_article(conn, article) is not None:
            conn.commit()
            stats.inserted += 1
            stats.inserted_urls.append(it.url)
        else:
            stats.skipped_existing += 1




def collect_backfill(
    config: Config,
    client: HttpClient,
    conn: DbConnection | None,
    source: Source,
    limit: int,
    dry_run: bool = False,
) -> SourceStats:
    """過去記事を backfill_url のページを順にめくって集める（collect.backfill_since 以降）。

    取得済みは飛ばして次のページへ。新規が limit 件に達するか、ページが空になるか、
    ページの記事が全部 since より古くなったら止める。間隔は HttpClient が守る（3秒・直列）。
    """
    stats = SourceStats(source=source.name)
    if not source.backfill_url or source.manual_only:
        return stats
    since = datetime.fromisoformat(config.collect.backfill_since).replace(tzinfo=UTC)
    as_index = bool(source.url_include) and "feed" not in source.backfill_url
    seen: set[str] = set()
    for page_no in range(1, config.collect.backfill_max_pages + 1):
        url = source.backfill_url.format(page=page_no)
        try:
            page_items = (read_index(client, source, url) if as_index
                          else read_feed(client, source, url))
        except (RobotsDisallowed, httpx.HTTPError) as exc:
            log.warning("%s: バックフィルのページを読めませんでした（%s）: %s", source.name, url, exc)
            break
        page_items = [it for it in page_items if it.url not in seen]
        if not page_items:
            break
        seen.update(it.url for it in page_items)
        if as_index:
            stats.index_items += len(page_items)
        else:
            stats.feed_items += len(page_items)
        dated = [it for it in page_items if it.published_at]
        all_old = bool(dated) and len(dated) == len(page_items) and all(
            _too_old(it, since) for it in page_items)
        inserted_before, old_before = stats.inserted, stats.skipped_old
        _ingest(config, client, conn, source, page_items, since, limit - stats.inserted,
                stats, dry_run)
        # 一覧ページ（日付が無い）は新しい順に並ぶので、記事を取って since より古いものが出て、
        # そのページで1件も入らなかったら、それより後ろのページも古いとみなして止める
        page_old = stats.skipped_old - old_before
        if all_old or (as_index and page_old > 0 and stats.inserted == inserted_before):
            break
        if stats.inserted >= limit:
            break
    return stats


def collect_all(
    config: Config,
    conn: DbConnection | None,
    names: list[str] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    backfill: bool = False,
) -> list[SourceStats]:
    """ソースを順番に回す。**並列にしない**（レート制限はプロセス内のドメイン単位）。"""
    results = []
    with HttpClient(config.http) as client:
        for source in config.sources:
            if names and source.name not in names:
                continue
            if (not source.enabled and not names) or source.manual_only:
                continue
            if backfill:
                if not source.backfill_url:
                    continue
                try:
                    stats = collect_backfill(config, client, conn, source,
                                             limit=limit or config.collect.per_source_limit,
                                             dry_run=dry_run)
                except Exception as exc:  # noqa: BLE001 - 1つの媒体の失敗で残りを止めない
                    log.error("%s: バックフィルが途中で止まりました: %s", source.name, exc)
                    if conn is not None:
                        conn.rollback()
                    continue
            else:
                stats = collect_source(config, client, conn, source, limit=limit, dry_run=dry_run)
            log.info(stats.summary())
            results.append(stats)
    return results
