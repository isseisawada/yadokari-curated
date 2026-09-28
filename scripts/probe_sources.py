"""ソース候補の実測。

1ソースごとに:
  robots.txt → フィード（候補URL、なければトップページの <link rel=alternate>）→
  最新記事1本のページ
を取り、次を出す:

  - フィードの件数と「何日分か」（窓）、1週間あたりの本数
  - そのうち小さな家・動く家に当たりそうな本数（タイトルと本文のキーワード）
  - 最新記事の経過日数（止まったフィードを見分ける）
  - フィードが全文か抜粋か
  - 記事ページがこの環境（データセンターのIP）から取れるか
  - 記事ページの写真クレジットの書き方（Photo / Courtesy など）と og:image のホスト

守ること: robots.txt が取れない・Disallow のときは取らない（fail-closed）。
同一ドメインは3秒間隔・並列なし。UA に連絡先。

    python scripts/probe_sources.py > docs/source-survey.tsv
"""

from __future__ import annotations

import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import feedparser
import requests
from bs4 import BeautifulSoup

UA = "yadokari-curated-survey/0.1 (+mailto:sawada@yadokari.net)"
INTERVAL = 3.0

# name, homepage, feed candidates
SOURCES: list[tuple[str, str, list[str]]] = [
    ("ArchDaily", "https://www.archdaily.com/", ["https://feeds.feedburner.com/Archdaily"]),
    ("Dezeen micro-homes", "https://www.dezeen.com/", ["https://www.dezeen.com/tag/micro-homes/feed/"]),
    ("Dezeen cabins", "https://www.dezeen.com/", ["https://www.dezeen.com/tag/cabins/feed/"]),
    ("Dwell", "https://www.dwell.com/", ["https://www.dwell.com/@dwell/rss"]),
    ("designboom cabin", "https://www.designboom.com/", ["https://www.designboom.com/tag/cabin/feed/"]),
    ("Design Milk architecture", "https://design-milk.com/", ["https://design-milk.com/category/architecture/feed/"]),
    ("Tiny House Talk", "https://tinyhousetalk.com/", ["https://tinyhousetalk.com/feed/"]),
    ("Living Big in a Tiny House", "https://www.livingbiginatinyhouse.com/", ["https://www.livingbiginatinyhouse.com/feed/"]),
    ("Humble Homes", "https://www.humble-homes.com/", ["https://www.humble-homes.com/feed/"]),
    ("Small House Bliss", "https://smallhousebliss.com/", ["https://smallhousebliss.com/feed/"]),
    ("Tiny Living", "https://www.tinyliving.com/", ["https://www.tinyliving.com/feed/"]),
    ("New Atlas tiny-houses", "https://newatlas.com/", ["https://newatlas.com/tiny-houses/index.rss"]),
    ("Inhabitat tiny homes", "https://inhabitat.com/", ["https://inhabitat.com/tag/tiny-homes/feed/"]),
    ("Contemporist", "https://www.contemporist.com/", ["https://www.contemporist.com/feed/"]),
    ("IGNANT", "https://www.ignant.com/", ["https://www.ignant.com/feed/"]),
    ("Gessato", "https://www.gessato.com/", ["https://www.gessato.com/feed/"]),
    ("Tiny House Blog", "https://tinyhouseblog.com/", ["https://tinyhouseblog.com/feed/"]),
    ("Cabin Porn", "https://cabinporn.com/", ["https://cabinporn.com/rss"]),
    ("Yanko Design", "https://www.yankodesign.com/", ["https://www.yankodesign.com/feed/"]),
    ("Leibal", "https://leibal.com/", ["https://leibal.com/feed/"]),
    ("Treehugger", "https://www.treehugger.com/", []),
    ("autoevolution tiny homes", "https://www.autoevolution.com/", []),
]

KEYWORDS = re.compile(
    r"\b(tiny|cabin|cabins|trailer|caravan|van|vanlife|camper|motorhome|rv|mobile home|"
    r"prefab|prefabricated|modular|container|micro|small house|small home|hut|shed|"
    r"treehouse|tree house|houseboat|floating|off-grid|off grid|a-frame|pod|bothy|"
    r"tiny home|tiny house|adu|granny flat|shepherd'?s hut|yurt|dome)\b",
    re.I,
)
CREDIT = re.compile(
    r"(photo(?:graph(?:y|s|er))?s?\s*(?:by|:|©|courtesy)|courtesy of|images?\s*(?:by|:|courtesy)|©\s*\w)",
    re.I,
)


class Fetcher:
    """1ドメイン用。前回から INTERVAL 秒あける。"""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA
        self.last: dict[str, float] = {}
        self.robots: dict[str, RobotFileParser | None] = {}

    def _wait(self, host: str) -> None:
        prev = self.last.get(host)
        if prev is not None:
            gap = time.monotonic() - prev
            if gap < INTERVAL:
                time.sleep(INTERVAL - gap)
        self.last[host] = time.monotonic()

    def _raw(self, url: str) -> requests.Response:
        self._wait(urlparse(url).netloc)
        return self.session.get(url, timeout=30)

    def allowed(self, url: str) -> bool | None:
        """True/False。robots.txt が取れなければ None（＝取らない）。"""
        p = urlparse(url)
        key = f"{p.scheme}://{p.netloc}"
        if key not in self.robots:
            try:
                r = self._raw(key + "/robots.txt")
                if r.status_code == 404:
                    rp = RobotFileParser()
                    rp.parse([])
                elif r.status_code == 200:
                    rp = RobotFileParser()
                    rp.parse(r.text.splitlines())
                else:
                    rp = None
            except requests.RequestException:
                rp = None
            self.robots[key] = rp
        rp = self.robots[key]
        if rp is None:
            return None
        return rp.can_fetch(UA, url)

    def get(self, url: str) -> requests.Response | str:
        ok = self.allowed(url)
        if ok is None:
            return "robots取得不可"
        if not ok:
            return "robots Disallow"
        try:
            return self._raw(url)
        except requests.RequestException as exc:
            return f"err {type(exc).__name__}"


def _discover_feed(f: Fetcher, home: str) -> str | None:
    r = f.get(home)
    if not isinstance(r, requests.Response) or r.status_code != 200:
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    for link in soup.find_all("link", rel="alternate"):
        if "rss" in (link.get("type") or "") or "atom" in (link.get("type") or ""):
            return urljoin(home, link.get("href"))
    return None


def _entry_date(e) -> datetime | None:
    t = e.get("published_parsed") or e.get("updated_parsed")
    return datetime(*t[:6], tzinfo=UTC) if t else None


def probe(src: tuple[str, str, list[str]]) -> dict:
    name, home, candidates = src
    f = Fetcher()
    row: dict = {"name": name, "feed": "", "status": ""}
    feed = None
    for url in candidates + [None]:
        if url is None:
            url = _discover_feed(f, home)
            if url is None:
                break
        r = f.get(url)
        if isinstance(r, str):
            row["status"] = f"feed {r}"
            continue
        if r.status_code != 200:
            row["status"] = f"feed HTTP {r.status_code}"
            continue
        parsed = feedparser.parse(r.content)
        if parsed.entries:
            feed, row["feed"] = parsed, url
            break
        row["status"] = "feed 0件"
    if feed is None:
        return row

    now = datetime.now(UTC)
    dates = [d for d in (_entry_date(e) for e in feed.entries) if d]
    n = len(feed.entries)
    row["items"] = n
    if dates:
        span = max((max(dates) - min(dates)).total_seconds() / 86400, 0.5)
        row["window_days"] = round(span, 1)
        row["per_week"] = round(n / span * 7, 1)
        row["days_since_newest"] = round((now - max(dates)).total_seconds() / 86400, 1)
    hits = 0
    content_len = []
    for e in feed.entries:
        body = " ".join(c.get("value", "") for c in e.get("content", [])) or e.get("summary", "")
        text = BeautifulSoup(body, "html.parser").get_text(" ")
        content_len.append(len(text))
        if KEYWORDS.search(e.get("title", "") + " " + text[:1500]):
            hits += 1
    row["relevant"] = hits
    if dates:
        row["relevant_per_week"] = round(hits / span * 7, 1)
    row["full_text"] = "全文" if sorted(content_len)[len(content_len) // 2] > 1500 else "抜粋"

    # 最新記事のページ
    link = feed.entries[0].get("link")
    row["sample"] = link or ""
    if link:
        r = f.get(link)
        if isinstance(r, str):
            row["article"] = r
        else:
            row["article"] = f"HTTP {r.status_code}"
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                og = soup.find("meta", property="og:image")
                row["og_image_host"] = urlparse(og["content"]).netloc if og and og.get("content") else ""
                text = soup.get_text(" ")
                m = CREDIT.search(text)
                row["credit"] = re.sub(r"\s+", " ", text[m.start(): m.start() + 60]).strip() if m else "（見当たらず）"
    row["status"] = "ok"
    return row


COLS = [
    "name", "status", "feed", "items", "window_days", "per_week", "relevant",
    "relevant_per_week", "days_since_newest", "full_text", "article", "og_image_host",
    "credit", "sample",
]


def main() -> int:
    # ドメインが違うものは並べて回す。同じドメイン（Dezeen の2本）は別 Fetcher だが
    # 同時に走らないよう、ドメインごとにまとめて1スレッドで処理する。
    by_host: dict[str, list] = {}
    for s in SOURCES:
        by_host.setdefault(urlparse(s[1]).netloc, []).append(s)

    def run_group(group):
        return [probe(s) for s in group]

    rows = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(run_group, by_host.values()):
            rows.extend(res)
    print("\t".join(COLS))
    for r in rows:
        print("\t".join(str(r.get(c, "")) for c in COLS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
