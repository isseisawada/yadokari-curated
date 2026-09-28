"""審査画面。

    /articles           採点済みの候補（点の高い順）
    /articles/{id}      詳細・承認／非承認（非承認は理由のタグと自由記述）
    /articles/{id}/draft  承認したものの下書きを作る（LLM）
    /drafts             下書きの一覧
    /drafts/{id}        下書きの編集・日時指定・WordPress に送る
    /manual             手動投入（規約で自動収集を禁じているサイト用。ページは取得しない）
    /rules              学習ループのルール候補（人が承認したものだけ採点に効く）
    /healthz            認証なし。DB に触らない

**WordPress に送るのはボタンを押したときだけ。** 予約（status=future）は
config の wordpress.allow_schedule が true のときだけ押せる。
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from yadokari.config import Config
from yadokari.db import repository as repo
from yadokari.db.connection import connect
from yadokari.logging_setup import get_logger
from yadokari.web.auth import BasicAuth, BasicAuthMiddleware

log = get_logger(__name__)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
STARTED_AT = datetime.now(UTC).isoformat(timespec="seconds")

AXIS_LABELS = {
    "design": "デザイン性",
    "story": "暮らしの物語",
    "smallness": "小さい／動く",
    "photos": "写真",
    "japan": "日本との接点",
    "facts": "事実の揃い",
    "freshness": "新しさ",
}
FACT_LABELS = {
    "name": "物件名", "builder": "ビルダー", "architect": "設計", "country": "国",
    "region": "地域", "area": "面積", "dimensions": "寸法", "price": "価格", "year": "年",
    "photographer": "撮影",
}


def create_app(config: Config, auth: BasicAuth | None = None, *, drafter=None, wp_factory=None,
               fetch=None, scorer=None) -> FastAPI:
    """drafter / wp_factory / fetch はテストで差し替える（外部に出ないように）。"""
    app = FastAPI(title="YADOKARI CURATED")
    if auth is not None:
        app.add_middleware(BasicAuthMiddleware, auth=auth)
    tz = ZoneInfo(config.app.timezone)

    @contextmanager
    def db():
        conn = connect(config.app.target())
        try:
            yield conn
        finally:
            conn.close()

    def local(iso: str | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
        if not iso:
            return ""
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(tz).strftime(fmt)

    TEMPLATES.env.filters["local"] = local
    TEMPLATES.env.filters["fromjson"] = lambda s: json.loads(s) if s else None

    def render(request: Request, name: str, **ctx) -> HTMLResponse:
        ctx.setdefault("flash", request.query_params.get("msg"))
        ctx.setdefault("error", request.query_params.get("err"))
        ctx["allow_schedule"] = config.wordpress.allow_schedule
        return TEMPLATES.TemplateResponse(request, name, ctx)

    def back(url: str, msg: str | None = None, err: str | None = None) -> RedirectResponse:
        from urllib.parse import urlencode

        q = {k: v for k, v in (("msg", msg), ("err", err)) if v}
        return RedirectResponse(url + ("?" + urlencode(q) if q else ""), status_code=303)

    # ------------------------------------------------------------------
    @app.get("/healthz")
    def healthz():
        return JSONResponse({"status": "ok", "commit": os.environ.get("RENDER_GIT_COMMIT", "")[:7],
                             "started_at": STARTED_AT})

    @app.get("/")
    def index():
        return RedirectResponse("/articles", status_code=303)

    @app.get("/articles", response_class=HTMLResponse)
    def articles(request: Request, status: str = "scored", all: int = 0):
        min_score = None if all or status != "scored" else config.scoring.review_threshold
        with db() as conn:
            rows = repo.list_articles(conn, status=status or None, min_score=min_score, limit=200)
            counts = {r["status"]: r["n"] for r in conn.execute(
                "SELECT status, COUNT(*) AS n FROM articles GROUP BY status").fetchall()}
        return render(request, "articles.html", rows=rows, status=status, all=all, counts=counts,
                      threshold=config.scoring.review_threshold)

    @app.get("/articles/{article_id}", response_class=HTMLResponse)
    def article(request: Request, article_id: int):
        with db() as conn:
            row = repo.get_article(conn, article_id)
            if row is None:
                return HTMLResponse("見つかりません", status_code=404)
            draft = repo.get_draft_by_article(conn, article_id)
            feedback = conn.execute(
                "SELECT * FROM feedback WHERE article_id = ? ORDER BY id DESC", (article_id,)
            ).fetchall()
        return render(
            request, "article.html", a=row, draft=draft, feedback=feedback,
            assessment=json.loads(row["assessment"]) if row["assessment"] else None,
            detail=json.loads(row["score_detail"]) if row["score_detail"] else None,
            images=json.loads(row["image_urls"] or "[]"),
            axis_labels=AXIS_LABELS, fact_labels=FACT_LABELS, tags=config.learning.tags,
        )

    def _next_unreviewed(conn, after_id: int) -> int | None:
        rows = repo.list_articles(conn, status="scored", min_score=config.scoring.review_threshold,
                                  limit=50)
        for r in rows:
            if r["id"] != after_id:
                return r["id"]
        return None

    @app.post("/articles/{article_id}/decide")
    def decide(article_id: int, decision: str = Form(...), tag: str = Form(""),
               reason: str = Form("")):
        if decision == "rejected" and not (tag or reason.strip()):
            return back(f"/articles/{article_id}", err="非承認には理由（タグか自由記述）を入れてください。学習ループの入力になります")
        with db() as conn:
            try:
                repo.decide(conn, article_id, decision, reason=reason, tag=tag or None)
                conn.commit()
            except ValueError as exc:
                return back(f"/articles/{article_id}", err=str(exc))
            nxt = _next_unreviewed(conn, article_id)
        label = "承認" if decision == "approved" else "非承認"
        if decision == "approved":
            return back(f"/articles/{article_id}", msg=f"{label}しました。下書きを作れます")
        return back(f"/articles/{nxt}" if nxt else "/articles", msg=f"{label}しました")

    @app.post("/articles/{article_id}/reset")
    def reset(article_id: int):
        with db() as conn:
            try:
                repo.reset_to_scored(conn, article_id)
                conn.commit()
            except ValueError as exc:
                return back(f"/articles/{article_id}", err=str(exc))
        return back(f"/articles/{article_id}", msg="審査を取り消しました")

    @app.post("/articles/{article_id}/draft")
    def make_draft(article_id: int):
        from yadokari.drafting.generate import generate_for

        with db() as conn:
            try:
                draft_id = generate_for(config, conn, article_id, client=drafter)
            except Exception as exc:
                log.exception("下書きの生成に失敗しました")
                return back(f"/articles/{article_id}", err=f"下書きを作れませんでした: {exc}")
        return back(f"/drafts/{draft_id}", msg="下書きを作りました。事実と数字を確認してください")

    # ------------------------------------------------------------------
    @app.get("/drafts", response_class=HTMLResponse)
    def drafts(request: Request, state: str = ""):
        with db() as conn:
            rows = repo.list_drafts(conn, state or None)
        return render(request, "drafts.html", rows=rows, state=state)

    @app.get("/drafts/{draft_id}", response_class=HTMLResponse)
    def draft(request: Request, draft_id: int):
        from yadokari.wordpress.publish import suggest_slot

        with db() as conn:
            d = repo.get_draft(conn, draft_id)
            if d is None:
                return HTMLResponse("見つかりません", status_code=404)
            a = repo.get_article(conn, d["article_id"])
            suggestion = None
            if not d["scheduled_at"]:
                try:
                    suggestion = suggest_slot(config, conn).isoformat()
                except RuntimeError:
                    suggestion = None
        return render(
            request, "draft.html", d=d, a=a,
            warnings=json.loads(d["warnings"] or "[]"), tags=", ".join(json.loads(d["tags"] or "[]")),
            scheduled_local=local(d["scheduled_at"] or suggestion, "%Y-%m-%dT%H:%M"),
            suggested=suggestion is not None,
            images=json.loads(a["image_urls"] or "[]"),
        )

    @app.post("/drafts/{draft_id}")
    def save(draft_id: int, title: str = Form(...), excerpt: str = Form(""),
             body_html: str = Form(...), tags: str = Form(""), featured_image: str = Form(""),
             scheduled_at: str = Form("")):
        when = None
        if scheduled_at:
            dt = datetime.fromisoformat(scheduled_at).replace(tzinfo=tz)
            when = dt.astimezone(UTC).isoformat()
        with db() as conn:
            repo.edit_draft(
                conn, draft_id, title=title.strip(), excerpt=excerpt.strip(), body_html=body_html,
                tags=[t.strip() for t in tags.replace("、", ",").split(",") if t.strip()],
                featured_image=featured_image.strip() or None, scheduled_at=when,
            )
            conn.commit()
        return back(f"/drafts/{draft_id}", msg="保存しました")

    @app.post("/drafts/{draft_id}/push")
    def push_draft(draft_id: int, mode: str = Form("draft")):
        from yadokari.wordpress.publish import ScheduleNotAllowed, push

        wp = wp_factory() if wp_factory else None
        with db() as conn:
            try:
                result = push(config, conn, draft_id, schedule=(mode == "schedule"), wp=wp,
                              fetch=fetch)
            except (ScheduleNotAllowed, ValueError, KeyError, RuntimeError) as exc:
                return back(f"/drafts/{draft_id}", err=str(exc))
            finally:
                if wp is not None:
                    wp.close()
        what = "予約しました" if result.status == "future" else "WordPress に下書きとして保存しました"
        note = " ".join(result.notes)
        return back(f"/drafts/{draft_id}", msg=f"{what}（post ID {result.post_id}）{note}")

    @app.post("/sync")
    def sync_now():
        from yadokari.wordpress.publish import sync

        wp = wp_factory() if wp_factory else None
        with db() as conn:
            try:
                res = sync(config, conn, wp=wp)
            except Exception as exc:  # noqa: BLE001 - 画面に出す
                return back("/drafts", err=f"WordPress を確認できませんでした: {exc}")
            finally:
                if wp is not None:
                    wp.close()
        msg = f"{res.checked} 件を確認。公開 {len(res.published)} 件"
        if res.missed:
            return back("/drafts", err=msg + f"。予約時刻を過ぎても公開されていないものが {len(res.missed)} 件あります（WP の予約投稿の失敗）")
        return back("/drafts", msg=msg)

    # ------------------------------------------------------------------
    manual_sources = [s.name for s in config.sources if s.manual_only]

    @app.get("/manual", response_class=HTMLResponse)
    def manual_form(request: Request):
        notes = {s.name: s.note for s in config.sources if s.manual_only}
        return render(request, "manual.html", sources=manual_sources, notes=notes)

    @app.post("/manual")
    def manual_add(source: str = Form(...), url: str = Form(...), title: str = Form(...),
                   text: str = Form(...), images: str = Form(""), credit: str = Form("")):
        from yadokari.collect.manual import add_manual

        with db() as conn:
            try:
                res = add_manual(config, conn, source=source, url=url, title=title, text=text,
                                 image_urls=images.splitlines(), credit=credit)
            except (ValueError, KeyError) as exc:
                return back("/manual", err=str(exc))
        if res.duplicate:
            return back("/manual", err="その URL は既に入っています")
        return back(f"/articles/{res.article_id}", msg="追加しました。採点ボタンで採点できます")

    @app.post("/articles/{article_id}/score")
    def score_now(article_id: int):
        from yadokari.scoring.runner import score_one

        with db() as conn:
            try:
                score = score_one(config, conn, article_id, client=scorer)
            except Exception as exc:  # noqa: BLE001 - 失敗の理由を画面に出す
                return back(f"/articles/{article_id}", err=f"採点できませんでした: {exc}")
        return back(f"/articles/{article_id}", msg=f"採点しました（{score:.1f}点）")

    # ------------------------------------------------------------------
    @app.get("/rules", response_class=HTMLResponse)
    def rules(request: Request):
        with db() as conn:
            rows = repo.list_rule_candidates(conn)
            counts = repo.tag_counts(conn)
        return render(request, "rules.html", rows=rows, counts=counts,
                      min_hits=config.learning.rule_min_hits)

    @app.post("/rules/{rule_id}")
    def set_rule(rule_id: int, state: str = Form(...), proposal: str = Form("")):
        with db() as conn:
            repo.set_rule_state(conn, rule_id, state, proposal.strip() or None)
            conn.commit()
        return back("/rules", msg="更新しました")

    return app
