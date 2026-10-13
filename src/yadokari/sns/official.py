"""公式 SNS（X・Threads @yadokari_mobi）の投稿を作って、WordPress に予約を渡す（2026-10-13 ユーザー指定）。

1日4本:
  - 08:15・16:15  その日の新着記事（08:00・16:00 に公開される記事）。無ければ過去記事
  - 12:15・20:15  yadokari.net の過去記事（全カテゴリ。公開から60日以上・1年は同じ記事を使わない）

文面は「具体的な事実＋一言の気づき」。X と Threads に同じ文面。
**元記事に無い数字が入った文面は出さない**（下書きと同じ検算）。

流れ:  plan（文面を作る）→ 承認（approval_until までは人が審査画面で）→ push（WP に予約を渡す）
       → WP の mu-plugin（yadokari-curated-sns.php）が時刻に投稿 → sync（結果を読む）→ measure（数字を測る）
伸びた型（pattern）は、次に文面を作るときの指示に入れる（hints）。
"""

from __future__ import annotations

import html
import json
import random
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from yadokari.config import Config
from yadokari.db.connection import DbConnection
from yadokari.drafting.numbers import unsupported_numbers
from yadokari.logging_setup import get_logger
from yadokari.sns.x import LIMIT, URL_WEIGHT, weight

log = get_logger(__name__)

# 文面の型。測った数字を型ごとに集計して、伸びた型を次の文面づくりに効かせる
PATTERNS = {
    "number": "数字から入る（広さ・年数・件数など、元記事の数字で始める）",
    "place": "場所から入る（国・土地・風景で始める）",
    "quote": "言葉から入る（記事に出てくる人の言葉を引く）",
    "question": "問いから入る（読む人に問いかける）",
    "contrast": "対比で見せる（AなのにB、AではなくB）",
}
TEXT_BUDGET = LIMIT - URL_WEIGHT - 2  # X: URL の前の改行2つ

# トーン＆マナーは上杉さんの X（@LIFE_W…）の投稿に合わせる（2026-10-13 ユーザー指定。画面の写しから分析）
# 2026-10-14: もっと人がコメントしている温度に。心が動いた一点だけ・AIっぽい言葉を使わない・
# 人が直した例から学ぶ（edits）・書くのは Sonnet 5.5
SYSTEM = """あなたは YADOKARI（小さな住まい・タイニーハウス・これからの暮らし方のメディア）の編集部の一人です。
記事を読んで、思わず誰かに言いたくなったことを、X と Threads に自分の言葉で投稿します。
要約や紹介文ではなく、「読んだ人のひとこと」です。

いちばん大事なこと:
- 記事全体をまとめない。読んでいて「へえ」「いいな」と引っかかった細部を一つだけ選び、そこから書く
  （例: 寝室の広さではなく「シンクの横にワインが注げる蛇口がある」のような具体）
- その細部に、あなたが素直にどう感じたかを添える。感想は短く、言い切る

形（毎回同じにしない）:
- 基本は2段落。1段落目に事実（どこの・何が。物件名や人の名前は「」）、空行、2段落目に感じたこと2〜3行（1文ごとに改行）
- ときどき、事実1文＋ひとこと1行だけの短い投稿にしてよい
- 感じたことは、借り物の決まり文句ではなく自分の言葉で書く。その記事だから出てくる言葉にする
- 文末を揃えない。最近の投稿と同じ締めの言葉・言い回しを使わない

お手本（この温度に合わせる。言い回しは真似しない）:
---
ニュージーランドの森に、夜になるとランタンのように灯る小屋がある。「Nightlight Shed」。

昼は道具小屋、夜は光の彫刻。
いつか建つ家の前に、まず小さな灯りをひとつ置く。
土地との付き合い方として、すごく美しい順番だと思う。
---
サンフランシスコ生まれの小さな家「Cosmic One」。
屋根の折れ目ごとにソーラーパネル。雨水を集めて、使った水もまた使う。

電気も水も、外から引かずに暮らせる家。
小さいからこそ、暮らしの出入りが全部見える。
それって、すごく健やかなことだと思う。
---
家は建てるもの、と誰が決めたんだろう。

置いて、動かして、また置く。
季節や人生に合わせて、暮らしを変えていい。
---

使わない言葉・型（AIの文章に見える）:
「余白」「豊か」「寄り添う」「紡ぐ」「体現」「魅力」「素敵」「〜という選択肢」「〜こそ」の多用、
「〜のかもしれない」の多用、「そんな〜」で締める、「〜してみてはいかがでしょうか」、記事の宣伝・告知の言い回し（「ぜひ」「チェック」「詳しくはこちら」）

決まり:
- 全体で60〜120字（改行を除く）。絵文字・ハッシュタグ・URL・「！」は使わない
- 事実は記事に書いてあることだけ。記事に無い数字を書かない。換算もしない（㎡→畳 など）
- 記事のタイトルをなぞらない"""

# 使ったら書き直させる言葉（AIっぽさ）。検算と違って、直らなくても承認待ちにはしない
AI_WORDS = ["余白", "豊か", "寄り添", "紡", "体現", "魅力", "素敵", "という選択肢", "いかがでしょうか"]

SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "投稿の本文（URL は入れない）"},
        "pattern": {"type": "string", "enum": list(PATTERNS)},
        "usable": {"type": "boolean",
                   "description": "今読んでも成り立つ記事なら true。終わった催し・募集・期間限定、今も買える／申し込める"
                                  "ことが前提の物件や商品、古くなった制度・補助金・統計の話なら false"},
        "reason": {"type": "string", "description": "usable の理由を1文"},
    },
    "required": ["text", "pattern", "usable", "reason"],
    "additionalProperties": False,
}


@dataclass
class Source:
    wp_post_id: int
    link: str
    title: str
    text: str


@dataclass
class PlanResult:
    created: list[tuple[str, str, int]] = field(default_factory=list)  # (slot, kind, wp_post_id)
    failed: list[tuple[str, str]] = field(default_factory=list)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def plain_text(rendered: str) -> str:
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", rendered, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"via:\s*\S+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def magazine_link(config: Config, wp_post_id: int) -> str:
    return f"{config.wordpress.base_url.rstrip('/')}/magazine/{wp_post_id}/"


# ----------------------------------------------------------------------
# 枠
# ----------------------------------------------------------------------
def slots_for(config: Config, day: date) -> list[tuple[datetime, str]]:
    """その日の枠（UTC, kind）。時刻順。"""
    tz = ZoneInfo(config.app.timezone)
    out = [(datetime.combine(day, time.fromisoformat(t), tzinfo=tz).astimezone(UTC), "new")
           for t in config.sns.new_times]
    out += [(datetime.combine(day, time.fromisoformat(t), tzinfo=tz).astimezone(UTC), "archive")
            for t in config.sns.archive_times]
    return sorted(out)


def open_slots(config: Config, conn: DbConnection, now: datetime) -> list[tuple[datetime, str]]:
    tz = ZoneInfo(config.app.timezone)
    today = now.astimezone(tz).date()
    taken = {r["slot_at"] for r in conn.execute(
        "SELECT slot_at FROM sns_posts WHERE slot_at > ?", (now.isoformat(),)).fetchall()}
    out = []
    for offset in range(config.sns.lookahead_days + 1):
        for slot, kind in slots_for(config, today + timedelta(days=offset)):
            if slot <= now + timedelta(minutes=30) or slot.isoformat() in taken:
                continue
            out.append((slot, kind))
    return out


# ----------------------------------------------------------------------
# 元記事
# ----------------------------------------------------------------------
def new_article_for(config: Config, conn: DbConnection, slot: datetime) -> Source | None:
    """その枠の15分前に公開される、こちらの記事（予約済みの下書き）。"""
    publish = slot - timedelta(minutes=config.sns.new_offset_min)
    row = conn.execute(
        "SELECT d.wp_post_id, d.title, d.body_html FROM drafts d"
        " WHERE d.state IN ('scheduled', 'published') AND d.scheduled_at = ? AND d.wp_post_id IS NOT NULL",
        (publish.isoformat(),),
    ).fetchone()
    if row is None:
        return None
    return Source(wp_post_id=int(row["wp_post_id"]), link=magazine_link(config, int(row["wp_post_id"])),
                  title=row["title"], text=plain_text(row["body_html"] or ""))


def used_recently(config: Config, conn: DbConnection, now: datetime) -> set[int]:
    since = (now - timedelta(days=config.sns.repeat_after_days)).isoformat()
    return {int(r["wp_post_id"]) for r in conn.execute(
        "SELECT wp_post_id FROM sns_posts WHERE slot_at > ? AND state != 'skipped'", (since,)).fetchall()}


def excluded_title(config: Config, title: str) -> bool:
    return any(re.search(p, title) for p in config.sns.archive_exclude_title)


def archive_article(config: Config, wp, used: set[int], now: datetime,
                    rng: random.Random | None = None, tries: int = 4,
                    skip: set[int] | None = None) -> Source | None:
    """yadokari.net の過去記事から1本。

    archive_pools を上から順に試す（2026-10-14 ユーザー指定: インタビュー記事を増やす）。
    どれも使い切ったら全記事から。ページを無作為に選んで、まだ使っていない・外す条件に当たらないものを取る。
    """
    rng = rng or random.Random()
    skip = skip or set()
    before = (now - timedelta(days=config.sns.archive_min_age_days)).strftime("%Y-%m-%dT%H:%M:%S")
    base = {"before": before, "per_page": 20, "_fields": "id,link,title,content"}
    if config.sns.archive_after:
        base["after"] = f"{config.sns.archive_after}T00:00:00"
    if config.sns.archive_exclude_categories:
        base["categories_exclude"] = ",".join(map(str, config.sns.archive_exclude_categories))
    if config.sns.archive_exclude_tags:
        base["tags_exclude"] = ",".join(map(str, config.sns.archive_exclude_tags))
    for pool in [*config.sns.archive_pools, {}]:
        params = {**base, **pool}
        _, total = wp.list_posts({**params, "per_page": 1, "page": 1})
        pages = max(1, (total + 19) // 20)  # per_page=1 のページ数 = 件数
        for _ in range(min(tries, pages * 2)):
            data, _ = wp.list_posts({**params, "page": rng.randint(1, pages)})
            rng.shuffle(data)
            for p in data:
                pid = int(p["id"])
                title = html.unescape(p.get("title", {}).get("rendered", ""))
                if pid in used or pid in skip or excluded_title(config, title):
                    continue
                text = plain_text(p.get("content", {}).get("rendered", ""))
                if len(text) < 400:
                    continue
                return Source(wp_post_id=pid, link=p.get("link") or magazine_link(config, pid),
                              title=title, text=text)
    return None


# ----------------------------------------------------------------------
# 文面
# ----------------------------------------------------------------------
def hints(conn: DbConnection, now: datetime, days: int = 28) -> str:
    """最近の投稿の数字を型ごとに平均して、伸びた型・伸びなかった型を返す（指示に入れる）。"""
    rows = conn.execute(
        "SELECT pattern, metrics FROM sns_posts WHERE state = 'posted' AND metrics IS NOT NULL"
        " AND slot_at > ? AND slot_at < ?",
        ((now - timedelta(days=days)).isoformat(), (now - timedelta(days=2)).isoformat()),
    ).fetchall()
    score: dict[str, list[float]] = {}
    for r in rows:
        m = json.loads(r["metrics"])
        views = (m.get("x") or {}).get("impressions", 0) + (m.get("threads") or {}).get("views", 0)
        if r["pattern"] in PATTERNS:
            score.setdefault(r["pattern"], []).append(views)
    ranked = sorted(((sum(v) / len(v), k, len(v)) for k, v in score.items() if len(v) >= 3), reverse=True)
    if len(ranked) < 2:
        return ""
    best = "、".join(f"{PATTERNS[k].split('（')[0]}（平均{avg:.0f}表示）" for avg, k, _ in ranked[:2])
    worst = PATTERNS[ranked[-1][1]].split("（")[0]
    return f"最近よく読まれた型: {best}。伸びなかった型: {worst}。よく読まれた型を優先してよいが、毎回同じにはしない。"


def edits(conn: DbConnection, limit: int = 5) -> list[tuple[str, str]]:
    """人が審査画面で直した文面（AI の案, 直した文）。新しい順。"""
    rows = conn.execute(
        "SELECT ai_text, text FROM sns_posts WHERE ai_text IS NOT NULL AND text <> ai_text"
        " AND state IN ('approved', 'queued', 'posted') ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [(r["ai_text"], r["text"]) for r in rows]


def write(config: Config, src: Source, client, *, hint: str = "", recent: list[str] | None = None,
          fixes: list[tuple[str, str]] | None = None) -> dict:
    """文面を書かせて検算する。{"text","pattern","warnings"}。"""
    from yadokari.llm import call_json, make_client

    s = config.sns
    client = client or make_client(config.anthropic_api_key)
    check = f"{src.title}\n{src.text}"
    user = f"記事のタイトル: {src.title}\n\n記事の本文:\n{src.text[:6000]}\n\n型の候補:\n" + "\n".join(
        f"- {k}: {v}" for k, v in PATTERNS.items())
    if hint:
        user += f"\n\n{hint}"
    if recent:
        user += "\n\n最近の投稿（書き出しや言い回しを重ねない）:\n" + "\n".join(f"- {t}" for t in recent[:6])
    if fixes:
        # 人が直した例から、編集部の声に寄せる（2026-10-14 ユーザー指定）
        user += "\n\n編集部が AI の案をこう直した（この直し方の方向に寄せる）:\n" + "\n".join(
            f"--- AI の案\n{a}\n--- 直した文\n{b}" for a, b in fixes)
    messages = [{"role": "user", "content": user}]
    data: dict = {}
    problems: list[str] = []
    for attempt in range(2):
        data, _ = call_json(client, model=s.model, system=SYSTEM, messages=messages, schema=SCHEMA,
                            max_tokens=4000, effort=s.effort, fallbacks=s.fallbacks)
        text = str(data.get("text", "")).strip()
        problems = []
        stray = unsupported_numbers(text, check)
        if stray:
            problems.append(f"元資料に無い数字: {'、'.join(sorted(stray))}")
        if weight(text) > TEXT_BUDGET:
            problems.append(f"長すぎる（X の重みで {weight(text)}／上限 {TEXT_BUDGET}）")
        if re.search(r"https?://|#", text):
            problems.append("URL かハッシュタグが入っている")
        style = [w for w in AI_WORDS if w in text]
        if style and attempt == 0:
            problems.append(f"使わない言葉が入っている: {'、'.join(style)}")
        if not problems:
            break
        if attempt == 0:
            messages += [{"role": "assistant", "content": json.dumps(data, ensure_ascii=False)},
                         {"role": "user", "content": "直してください: " + "／".join(problems)}]
    problems = [p for p in problems if not p.startswith("使わない言葉")]
    return {"text": str(data.get("text", "")).strip(), "pattern": data.get("pattern"),
            "usable": data.get("usable", True) is not False, "reason": str(data.get("reason", "")),
            "warnings": problems}


def needs_approval(config: Config, slot: datetime) -> bool:
    until = config.sns.approval_until
    if not until:
        return False
    return slot.astimezone(ZoneInfo(config.app.timezone)).date() <= date.fromisoformat(until)


def plan(config: Config, conn: DbConnection, *, wp=None, client=None, now: datetime | None = None,
         limit: int = 8, rng: random.Random | None = None) -> PlanResult:
    """空いている枠に文面を作る（lookahead_days 先まで）。1回に limit 本まで。"""
    res = PlanResult()
    if not config.sns.enabled:
        return res
    now = now or datetime.now(UTC)
    used = used_recently(config, conn, now)
    hint = hints(conn, now)
    fixes = edits(conn)
    for slot, kind in open_slots(config, conn, now)[:limit]:
        try:
            src = new_article_for(config, conn, slot) if kind == "new" else None
            recent = [r["text"] for r in conn.execute(
                "SELECT text FROM sns_posts ORDER BY slot_at DESC LIMIT 6").fetchall()]
            out = None
            if src is not None:
                out = write(config, src, client, hint=hint, recent=recent, fixes=fixes)
            else:
                # 過去記事は、AI が「今読んでも成り立たない」と言ったら別の記事にする（3本まで）
                kind = "archive"
                rejected: set[int] = set()
                for _ in range(3):
                    src = archive_article(config, wp, used, now, rng=rng, skip=rejected)
                    if src is None:
                        break
                    out = write(config, src, client, hint=hint, recent=recent, fixes=fixes)
                    if out["usable"]:
                        break
                    log.info("過去記事を外しました: wp#%s %s", src.wp_post_id, out["reason"])
                    rejected.add(src.wp_post_id)
                    out = None
            if src is None or out is None:
                res.failed.append((slot.isoformat(), "使える過去記事が見つかりません"))
                continue
        except Exception as exc:  # noqa: BLE001 - 1枠の失敗で残りを止めない
            conn.rollback()
            res.failed.append((slot.isoformat(), str(exc)))
            log.warning("SNS の文面に失敗しました: %s %s", slot, exc)
            continue
        state = "draft" if (out["warnings"] or needs_approval(config, slot)) else "approved"
        conn.execute(
            "INSERT INTO sns_posts (kind, slot_at, wp_post_id, link, title, text, ai_text, pattern, warnings, model,"
            " state, created_at, approved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, slot.isoformat(), src.wp_post_id, src.link, src.title, out["text"], out["text"], out["pattern"],
             json.dumps(out["warnings"], ensure_ascii=False), config.sns.model, state, now_iso(),
             now_iso() if state == "approved" else None),
        )
        conn.commit()
        used.add(src.wp_post_id)
        res.created.append((slot.isoformat(), kind, src.wp_post_id))
    return res


# ----------------------------------------------------------------------
# WordPress に予約を渡す・結果を読む
# ----------------------------------------------------------------------
def tagged(config: Config, link: str, kind: str) -> str:
    """計測タグ（utm_source は WP が x / threads に付け替える）。"""
    campaign = config.sns.utm_campaign_new if kind == "new" else config.sns.utm_campaign_archive
    sep = "&" if "?" in link else "?"
    return f"{link}{sep}utm_medium=social&utm_campaign={campaign}"


def push(config: Config, conn: DbConnection, wp, now: datetime | None = None) -> tuple[int, list[str]]:
    """承認済みでまだ渡していないものを WP に渡す。(渡した本数, エラー)。"""
    now = now or datetime.now(UTC)
    rows = conn.execute(
        "SELECT * FROM sns_posts WHERE state = 'approved' AND slot_at > ? ORDER BY slot_at",
        ((now + timedelta(minutes=5)).isoformat(),),
    ).fetchall()
    errors: list[str] = []
    n = 0
    for r in rows:
        try:
            wp.sns_upsert({"key": r["id"], "kind": r["kind"], "text": r["text"],
                           "url": tagged(config, r["link"], r["kind"]), "at": r["slot_at"],
                           "wp_post_id": r["wp_post_id"]})
        except Exception as exc:  # noqa: BLE001
            errors.append(f"#{r['id']}: {exc}")
            continue
        conn.execute("UPDATE sns_posts SET state = 'queued', queued_at = ?, error = NULL WHERE id = ?",
                     (now_iso(), r["id"]))
        conn.commit()
        n += 1
    return n, errors


def clear_drafts(conn: DbConnection, now: datetime | None = None) -> int:
    """これからの枠の承認待ちを消す（次の plan で作り直す）。"""
    now = now or datetime.now(UTC)
    n = conn.execute("DELETE FROM sns_posts WHERE state = 'draft' AND slot_at > ?", (now.isoformat(),)).rowcount
    conn.commit()
    return n


def cancel(conn: DbConnection, wp, post_id: int) -> None:
    """外す。WP に渡していれば取り消す。"""
    row = conn.execute("SELECT state FROM sns_posts WHERE id = ?", (post_id,)).fetchone()
    if row and row["state"] == "queued":
        wp.sns_upsert({"key": post_id, "cancel": True})
    conn.execute("UPDATE sns_posts SET state = 'skipped', queued_at = NULL WHERE id = ?", (post_id,))
    conn.commit()


def sync(conn: DbConnection, wp) -> int:
    """WP 側の結果（投稿 ID・エラー）を読む。"""
    rows = conn.execute("SELECT id FROM sns_posts WHERE state = 'queued'").fetchall()
    if not rows:
        return 0
    status = wp.sns_status([r["id"] for r in rows])
    n = 0
    for key, st in status.items():
        if st.get("state") not in ("posted", "failed"):
            continue
        conn.execute(
            "UPDATE sns_posts SET state = ?, posted_at = ?, x_id = ?, threads_id = ?, error = ? WHERE id = ?",
            (st["state"], st.get("posted_at"), st.get("x_id"), st.get("threads_id"),
             st.get("error") or None, int(key)),
        )
        n += 1
    conn.commit()
    return n


# ----------------------------------------------------------------------
# 測る
# ----------------------------------------------------------------------
def measure(conn: DbConnection, wp, x_keys: dict | None, now: datetime | None = None, days: int = 30,
            http=None) -> dict:
    """投稿から30日以内のものの数字と、フォロワー数を記録する。"""
    from yadokari.sns import x as xapi

    now = now or datetime.now(UTC)
    rows = conn.execute(
        "SELECT id, x_id, threads_id, metrics FROM sns_posts WHERE state = 'posted' AND slot_at > ?",
        ((now - timedelta(days=days)).isoformat(),),
    ).fetchall()
    by_id = {r["id"]: json.loads(r["metrics"]) if r["metrics"] else {} for r in rows}
    out = {"posts": len(rows), "errors": []}
    if x_keys:
        ids = [r["x_id"] for r in rows if r["x_id"]]
        for i in range(0, len(ids), 100):
            try:
                data = xapi.get("/2/tweets", {"ids": ",".join(ids[i:i + 100]),
                                              "tweet.fields": "public_metrics,non_public_metrics"},
                                x_keys, http=http)
            except Exception as exc:  # noqa: BLE001
                out["errors"].append(f"X: {exc}")
                break
            for t in data.get("data", []):
                pm, npm = t.get("public_metrics", {}), t.get("non_public_metrics", {})
                for r in rows:
                    if r["x_id"] == t["id"]:
                        by_id[r["id"]]["x"] = {
                            "impressions": npm.get("impression_count", pm.get("impression_count", 0)),
                            "likes": pm.get("like_count", 0), "reposts": pm.get("retweet_count", 0),
                            "replies": pm.get("reply_count", 0),
                            "clicks": npm.get("url_link_clicks", 0)}
        try:
            me = xapi.get("/2/users/me", {"user.fields": "public_metrics"}, x_keys, http=http)
            conn.execute("INSERT INTO sns_stats (measured_at, platform, followers) VALUES (?, 'x', ?)",
                         (now_iso(), me["data"]["public_metrics"]["followers_count"]))
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"X followers: {exc}")
    th_ids = [r["threads_id"] for r in rows if r["threads_id"]]
    if th_ids:
        try:
            data = wp.sns_metrics(th_ids)
            for r in rows:
                if r["threads_id"] in data.get("posts", {}):
                    by_id[r["id"]]["threads"] = data["posts"][r["threads_id"]]
            if data.get("followers") is not None:
                conn.execute("INSERT INTO sns_stats (measured_at, platform, followers) VALUES (?, 'threads', ?)",
                             (now_iso(), data["followers"]))
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"Threads: {exc}")
    for pid, m in by_id.items():
        if m:
            m["at"] = now_iso()
            conn.execute("UPDATE sns_posts SET metrics = ? WHERE id = ?", (json.dumps(m), pid))
    conn.commit()
    return out


def measure_due(conn: DbConnection, now: datetime | None = None, days: float = 7) -> bool:
    """前回測ってから days 日たったか（投稿が1本も無ければ測らない）。"""
    now = now or datetime.now(UTC)
    if conn.execute("SELECT 1 FROM sns_posts WHERE state = 'posted' LIMIT 1").fetchone() is None:
        return False
    last = conn.execute("SELECT MAX(measured_at) AS t FROM sns_stats").fetchone()["t"]
    return last is None or datetime.fromisoformat(last) < now - timedelta(days=days)


def weekly_summary(conn: DbConnection, now: datetime | None = None) -> str:
    """直近7日の数字を1段落に。"""
    now = now or datetime.now(UTC)
    rows = conn.execute(
        "SELECT kind, pattern, metrics FROM sns_posts WHERE state = 'posted' AND slot_at > ?",
        ((now - timedelta(days=7)).isoformat(),),
    ).fetchall()
    tot = {"x": [0, 0, 0], "threads": [0, 0, 0]}  # 表示・いいね・クリック
    for r in rows:
        m = json.loads(r["metrics"]) if r["metrics"] else {}
        for p, keys in (("x", ("impressions", "likes", "clicks")), ("threads", ("views", "likes", "clicks"))):
            for i, k in enumerate(keys):
                tot[p][i] += (m.get(p) or {}).get(k, 0) or 0
    fol = {}
    for p in ("x", "threads"):
        r = conn.execute("SELECT followers FROM sns_stats WHERE platform = ? ORDER BY measured_at DESC LIMIT 1",
                         (p,)).fetchone()
        fol[p] = r["followers"] if r else None
    n = len(rows) or 1
    return (f"直近7日 {len(rows)}本／X 表示{tot['x'][0]}（1本平均{tot['x'][0] // n}）・いいね{tot['x'][1]}"
            f"・クリック{tot['x'][2]}・フォロワー{fol['x']}／Threads 表示{tot['threads'][0]}"
            f"・いいね{tot['threads'][1]}・フォロワー{fol['threads']}")
