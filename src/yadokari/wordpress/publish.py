"""下書きを WordPress に送る・予約する・公開されたかを確かめる。

方針:
  - **予約投稿は WP に任せる**（status=future と date）。自前のワーカーは時刻に publish しない。
    だから常駐ワーカーが要らず、DB を叩き続けない（frmg で Neon の無料枠を食い潰した穴を避ける）
  - WP の予約は wp-cron（ページアクセスで動く）なので、取りこぼし（予約投稿の失敗）がありうる。
    `sync` が予定時刻を過ぎても future のままのものを見つけて知らせる
  - **二重投稿しない。** slug を `yc-<記事ID>` に固定し、送る前に同じ slug を探す。
    見つかればそれを更新する（DB に post ID を書く前に落ちても2本目を作らない）
  - **予約（future）は config の allow_schedule が true のときだけ。**
    最初は下書き（draft）だけ。WP 上で見て OK が出てから予約に進む
"""

from __future__ import annotations

import html
import json
import mimetypes
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from yadokari.config import Config
from yadokari.db.connection import DbConnection, Row
from yadokari.db.repository import (
    drafts_to_sync,
    get_article,
    get_draft,
    mark_error,
    mark_published,
    mark_pushed,
    taken_slots,
)
from yadokari.logging_setup import get_logger
from yadokari.wordpress.client import WordPressClient, WordPressError

log = get_logger(__name__)


class ScheduleNotAllowed(RuntimeError):
    pass


def slug_for(config: Config, article_id: int) -> str:
    return f"{config.wordpress.slug_prefix}{article_id}"


# ----------------------------------------------------------------------
# 予定枠
# ----------------------------------------------------------------------
def next_free_slot(config: Config, taken: set[str], now: datetime | None = None,
                   days_ahead: int = 400) -> datetime:
    """空いている次の枠（post_times の数＝1日の本数）。frmg の plan.py と同じ考え方。

    DB には UTC で持つ。現地時刻を混ぜると環境によってずれる。
    """
    tz = ZoneInfo(config.app.timezone)
    now = now or datetime.now(UTC)
    local_now = now.astimezone(tz)
    times = sorted(time.fromisoformat(t) for t in config.wordpress.post_times)
    for offset in range(days_ahead):
        day: date = local_now.date() + timedelta(days=offset)
        for t in times:
            slot = datetime.combine(day, t, tzinfo=tz)
            # 30分を切った枠は選ばない（WP に送る・人が見る時間がない）
            if slot <= local_now + timedelta(minutes=30):
                continue
            iso = slot.astimezone(UTC).isoformat()
            if iso not in taken:
                return slot.astimezone(UTC)
    raise RuntimeError(f"{days_ahead}日先まで枠が埋まっています")


def suggest_slot(config: Config, conn: DbConnection, now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return next_free_slot(config, taken_slots(conn, now.isoformat()), now)


def to_wp_local(config: Config, when_utc: str) -> str:
    """WP の `date` はサイトの現地時刻（タイムゾーン無し）。date_gmt も一緒に送る。"""
    dt = datetime.fromisoformat(when_utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ZoneInfo(config.app.timezone)).replace(tzinfo=None).isoformat(timespec="seconds")


# ----------------------------------------------------------------------
# 送信
# ----------------------------------------------------------------------
@dataclass
class PushResult:
    post_id: int
    status: str
    link: str | None
    created: bool
    notes: list[str] = field(default_factory=list)


def _payload(config: Config, draft: Row, article_id: int, wp: WordPressClient,
             status: str, media_id: int | None, facts: dict | None = None) -> dict:
    tags = json.loads(draft["tags"] or "[]")
    payload: dict = {
        "title": draft["title"],
        "content": draft["body_html"],
        "excerpt": draft["excerpt"] or "",
        "status": status,
        "slug": slug_for(config, article_id),
        "categories": config.wordpress.category_ids,
        # 記事分類「TINY HOUSE JOURNAL」と ACF の QUOTE。REST に出ていないので
        # wordpress-plugin/yadokari-curated-fields.php（mu-plugins）が受け取る（2026-09-30）
        "yc_journal": True,
    }
    if "quote" in draft.keys() and draft["quote"]:
        payload["yc_quote"] = draft["quote"]
    if tags:
        payload["tags"] = wp.tag_ids(tags, fixed=config.seo.tag_ids)
    if config.seo.send_facts_meta and facts:
        # 構造化データの材料（wordpress-plugin/ が register_post_meta した yc_facts）
        payload["meta"] = {"yc_facts": json.dumps(facts, ensure_ascii=False)}
    if media_id:
        payload["featured_media"] = media_id
    elif draft["featured_image"]:
        # 取り込めない（画像サーバの robots が自動取得を許さない）ときは、元の URL のまま
        # アイキャッチにする。プラグイン FIFU（Featured Image from URL）に渡す（2026-09-30 ユーザー判断）
        payload["yc_featured_url"] = draft["featured_image"]
    if status == "future":
        payload["date"] = to_wp_local(config, draft["scheduled_at"])
        payload["date_gmt"] = datetime.fromisoformat(draft["scheduled_at"]).astimezone(UTC).replace(
            tzinfo=None).isoformat(timespec="seconds")
    return payload


def _upload_featured(config: Config, wp: WordPressClient, draft: Row, source_url: str,
                     fetch) -> int | None:
    """アイキャッチは元写真を WP のメディアに取り込む（既存記事と同じ運用）。
    取れなくても投稿自体は止めない（人が WP 上で差し替えられる）。"""
    url = draft["featured_image"]
    if not url or not config.wordpress.upload_featured_image:
        return None
    try:
        content, content_type = fetch(url)
    except Exception as exc:  # noqa: BLE001 - アイキャッチの失敗で投稿を止めない
        log.warning("アイキャッチを取得できませんでした: %s (%s)", url, exc)
        return None
    name = urlparse(url).path.rsplit("/", 1)[-1] or "featured.jpg"
    if "." not in name:
        name += mimetypes.guess_extension(content_type or "image/jpeg") or ".jpg"
    domain = urlparse(source_url).netloc.removeprefix("www.")
    return wp.upload_media(name, content, content_type or "image/jpeg", caption=f"via: {domain}")


def default_fetch(config: Config):
    """アイキャッチの取得。収集と同じ HttpClient（robots・3秒間隔）を通す。"""
    from yadokari.net.client import HttpClient

    def fetch(url: str) -> tuple[bytes, str]:
        with HttpClient(config.http) as client:
            r = client.get(url)
            return r.content, r.headers.get("content-type", "image/jpeg").split(";")[0]

    return fetch


def push(config: Config, conn: DbConnection, draft_id: int, *, schedule: bool = False,
         wp: WordPressClient | None = None, fetch=None) -> PushResult:
    """下書きを WP に送る。schedule=True なら予約（status=future）。"""
    draft = get_draft(conn, draft_id)
    if draft is None:
        raise KeyError(f"下書き {draft_id} がありません")
    if draft["state"] == "published":
        raise ValueError("公開済みの記事は送り直しません（WP 上で直してください）")
    article = get_article(conn, draft["article_id"])
    if schedule:
        if not config.wordpress.allow_schedule:
            raise ScheduleNotAllowed(
                "予約投稿はまだ許可されていません（config.yaml の wordpress.allow_schedule）。"
                "まず下書きとして送り、WP 上で確認してから有効にしてください"
            )
        if not draft["scheduled_at"]:
            raise ValueError("公開日時が未設定です")
        if datetime.fromisoformat(draft["scheduled_at"]) <= datetime.now(UTC) + timedelta(minutes=5):
            raise ValueError("公開日時が過去か、5分以内です")
    status = "future" if schedule else "draft"

    own = wp is None
    wp = wp or WordPressClient(config.wordpress)
    try:
        post_id = draft["wp_post_id"]
        notes: list[str] = []
        if post_id is None:
            found = wp.find_by_slug(slug_for(config, draft["article_id"]))
            if found is not None:
                post_id = found.id
                notes.append(f"同じ slug の投稿（ID {found.id}）が WP にあったので、それを更新します")
        if post_id is not None:
            current = wp.get_post(post_id)
            if current.status == "publish":
                raise ValueError(f"WP 上で公開済みです（ID {post_id}）。送り直しません")
        media_id = draft["wp_media_id"]
        if media_id is None:
            media_id = _upload_featured(config, wp, draft, article["source_url"],
                                        fetch or default_fetch(config))
        assessment = json.loads(article["assessment"]) if article["assessment"] else {}
        facts = {k: v for k, v in (assessment.get("facts") or {}).items() if v}
        if assessment.get("kind"):
            facts["kind"] = assessment["kind"]
        payload = _payload(config, draft, draft["article_id"], wp, status, media_id, facts)
        if post_id is None:
            post = wp.create_post(payload)
            created = True
        else:
            post = wp.update_post(post_id, payload)
            created = False
    except (WordPressError, ValueError) as exc:
        mark_error(conn, draft_id, str(exc))
        conn.commit()
        raise
    finally:
        if own:
            wp.close()

    state = "scheduled" if post.status == "future" else "wp_draft"
    if post.status == "publish":
        state = "published"
    mark_pushed(conn, draft_id, wp_post_id=post.id, wp_status=post.status, wp_link=post.link,
                state=state, media_id=media_id)
    conn.commit()
    log.info("WP に送りました: draft=%s post=%s status=%s", draft_id, post.id, post.status)
    return PushResult(post.id, post.status, post.link, created, notes)


# ----------------------------------------------------------------------
# 公開の確認
# ----------------------------------------------------------------------
@dataclass
class SyncResult:
    published: list[tuple[int, str | None]] = field(default_factory=list)
    missed: list[tuple[int, str]] = field(default_factory=list)
    checked: int = 0


def sync(config: Config, conn: DbConnection, wp: WordPressClient | None = None,
         now: datetime | None = None, grace_minutes: int = 30) -> SyncResult:
    """WP に送ったものの状態を取り直す。公開されていたら published にする
    （SNS はここで公開を確かめてから出す）。

    予定時刻を grace_minutes 過ぎても future のままなら「予約投稿の失敗」として返す
    （wp-cron の取りこぼし）。
    """
    now = now or datetime.now(UTC)
    result = SyncResult()
    own = wp is None
    wp = wp or WordPressClient(config.wordpress)
    try:
        for d in drafts_to_sync(conn):
            result.checked += 1
            post = wp.get_post(d["wp_post_id"])
            if post.status == "publish":
                mark_published(conn, d["id"], post.link, now.isoformat())
                result.published.append((d["id"], post.link))
            elif post.status == "future" and d["scheduled_at"]:
                due = datetime.fromisoformat(d["scheduled_at"])
                if now > due + timedelta(minutes=grace_minutes):
                    result.missed.append((d["id"], d["scheduled_at"]))
            conn.execute("UPDATE drafts SET wp_status = ? WHERE id = ?", (post.status, d["id"]))
        conn.commit()
    finally:
        if own:
            wp.close()
    return result


# ----------------------------------------------------------------------
# 写真を大きい版に差し替える（2026-09-30 ArchDaily の medium_jpg が小さかった）
# ----------------------------------------------------------------------
_IMG_SRC = re.compile(r'(<img\b[^>]*?\bsrc=")([^"]+)(")')


def refresh_images(config: Config, conn: DbConnection, *, wp: WordPressClient | None = None,
                   fetch=None) -> list[tuple[int, str]]:
    """公開前の下書きの写真 URL を upgrade_image_url で直し、WP に送ってあれば同じ状態で送り直す。

    アイキャッチが変わったら取り込み直す（古いメディアは WP に残る）。(draft_id, 結果) を返す。
    """
    from yadokari.collect.page import upgrade_image_url

    out: list[tuple[int, str]] = []
    rows = conn.execute(
        "SELECT id FROM drafts WHERE state IN ('editing', 'wp_draft', 'scheduled') ORDER BY id"
    ).fetchall()
    for r in rows:
        d = get_draft(conn, r["id"])
        body = _IMG_SRC.sub(lambda m: m.group(1) + html.escape(
            upgrade_image_url(html.unescape(m.group(2))), quote=True) + m.group(3), d["body_html"])
        featured = upgrade_image_url(d["featured_image"]) if d["featured_image"] else None
        if body == d["body_html"] and featured == d["featured_image"]:
            continue
        conn.execute(
            "UPDATE drafts SET body_html = ?, featured_image = ?,"
            " wp_media_id = CASE WHEN ? THEN NULL ELSE wp_media_id END WHERE id = ?",
            (body, featured, featured != d["featured_image"], d["id"]),
        )
        conn.commit()
        if d["wp_post_id"] is None:
            out.append((d["id"], "差し替え（WP には未送信）"))
            continue
        try:
            res = push(config, conn, d["id"], schedule=d["state"] == "scheduled", wp=wp, fetch=fetch)
        except Exception as exc:  # noqa: BLE001 - 1本の失敗で残りを止めない
            out.append((d["id"], f"送り直しに失敗: {exc}"))
            continue
        out.append((d["id"], f"差し替えて送り直し（post {res.post_id} {res.status}）"))
    return out


def resend(config: Config, conn: DbConnection, *, wp: WordPressClient | None = None,
           fetch=None) -> list[tuple[int, str]]:
    """WP に送ってある公開前の下書きを、今の状態（下書き／予約）のまま全部送り直す。"""
    now = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    rows = conn.execute(
        "SELECT id, state FROM drafts WHERE wp_post_id IS NOT NULL AND state IN ('wp_draft', 'scheduled')"
        " AND (state = 'wp_draft' OR scheduled_at > ?) ORDER BY id",
        (now,),
    ).fetchall()
    out: list[tuple[int, str]] = []
    for r in rows:
        try:
            res = push(config, conn, r["id"], schedule=r["state"] == "scheduled", wp=wp, fetch=fetch)
        except Exception as exc:  # noqa: BLE001 - 1本の失敗で残りを止めない
            out.append((r["id"], f"失敗: {exc}"))
            continue
        out.append((r["id"], f"post {res.post_id} {res.status}"))
    return out
