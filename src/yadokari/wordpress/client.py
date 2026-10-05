"""WordPress REST API（/wp-json/wp/v2/）。認証は Application Passwords（Basic）。

自分たちのサイトへの API 呼び出しなので、収集用の HttpClient（robots・3秒間隔）は通さない。
資格情報は環境変数 WP_USER / WP_APP_PASSWORD からだけ読む。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

import httpx

from yadokari.config import WordPressConfig
from yadokari.logging_setup import get_logger

log = get_logger(__name__)


class WordPressError(RuntimeError):
    pass


@dataclass
class WPPost:
    id: int
    status: str
    link: str | None
    date: str | None

    @classmethod
    def from_json(cls, data: dict) -> WPPost:
        return cls(id=int(data["id"]), status=data.get("status", ""), link=data.get("link"),
                   date=data.get("date"))


def auth_headers(user: str, password: str) -> dict[str, str]:
    """Authorization と同じ中身を X-YC-Auth でも送る。

    yadokari.net ではサーバーの手前で Authorization ヘッダーが捨てられ、REST API が匿名扱いになった
    （2026-09-30）。wordpress-plugin/yadokari-curated-auth.php（mu-plugins）がこちらを読む。
    """
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"X-YC-Auth": f"Basic {token}"}


class WordPressClient:
    def __init__(self, config: WordPressConfig, http: httpx.Client | None = None) -> None:
        self.config = config
        self.api = config.base_url.rstrip("/") + "/wp-json/wp/v2"
        if http is None:
            user, password = config.require_credentials()
            http = httpx.Client(
                auth=(user, password), timeout=config.timeout_sec,
                headers={"User-Agent": "YADOKARI-curated/0.1", **auth_headers(user, password)},
            )
        self._http = http

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> WordPressClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _check(self, r: httpx.Response) -> dict | list:
        if r.status_code >= 400:
            try:
                detail = r.json().get("message", r.text[:200])
            except ValueError:
                detail = r.text[:200]
            raise WordPressError(f"WordPress が {r.status_code} を返しました: {detail}")
        return r.json()

    # --- 確認（読み取りだけ） -----------------------------------------
    def me(self) -> dict:
        """ログインしているユーザー。名前・権限（roles）・できること（capabilities）。"""
        return self._check(self._http.get(f"{self.api}/users/me", params={"context": "edit"}))

    def get_term(self, kind: str, term_id: int) -> dict:
        """kind は categories か tags。"""
        return self._check(self._http.get(f"{self.api}/{kind}/{term_id}"))

    # --- 投稿 ---------------------------------------------------------
    def find_by_slug(self, slug: str) -> WPPost | None:
        """**二重投稿の防止。** 同じ slug の投稿がどの状態でもあれば返す。

        DB に post ID を書く前に落ちた場合でも、次の実行でこれが見つけるので
        2本目を作らない。
        """
        r = self._http.get(
            f"{self.api}/posts",
            params={"slug": slug, "status": "draft,future,publish,pending,private",
                    "context": "edit"},
        )
        data = self._check(r)
        return WPPost.from_json(data[0]) if data else None

    def get_post(self, post_id: int) -> WPPost:
        return WPPost.from_json(self._check(self._http.get(f"{self.api}/posts/{post_id}",
                                                           params={"context": "edit"})))

    def create_post(self, payload: dict) -> WPPost:
        return WPPost.from_json(self._check(self._http.post(f"{self.api}/posts", json=payload)))

    def update_post(self, post_id: int, payload: dict) -> WPPost:
        return WPPost.from_json(
            self._check(self._http.post(f"{self.api}/posts/{post_id}", json=payload))
        )

    # --- タグ ---------------------------------------------------------
    def tag_ids(self, names: list[str], fixed: dict[str, int] | None = None) -> list[int]:
        """タグ名 → ID。既存のタグに完全一致するものだけ使い、**新しいタグは作らない**
        （タグの体系は編集部のもの。勝手に増やさない）。

        必須タグ（タイニーハウス・小屋・トレーラーハウス）は ID を固定表から引く。
        検索は部分一致で「#タイニーハウス #トレーラーハウス…」のような似たタグが大量に返り、
        取り違えや取りこぼしが起きうるため。"""
        ids: list[int] = []
        for name in names:
            if fixed and name in fixed:
                if fixed[name] not in ids:
                    ids.append(fixed[name])
                continue
            data = self._check(self._http.get(f"{self.api}/tags", params={"search": name,
                                                                         "per_page": 100}))
            for t in data:
                if t.get("name") == name:
                    if int(t["id"]) not in ids:
                        ids.append(int(t["id"]))
                    break
            else:
                log.info("WP に無いタグなので付けません: %s", name)
        return ids

    # --- メディア -----------------------------------------------------
    def upload_media(self, filename: str, content: bytes, content_type: str,
                     caption: str | None = None) -> int:
        # ヘッダは ASCII しか送れない（2026-10-05: ファイル名の「–」で落ちた）。
        # ASCII に寄せた名前と、元の名前（RFC 5987 の filename*）の両方を付ける
        from urllib.parse import quote

        safe = "".join(c if c.isascii() and (c.isalnum() or c in "._-") else "-" for c in filename)
        safe = safe.strip("-") or "featured.jpg"
        r = self._http.post(
            f"{self.api}/media",
            content=content,
            headers={
                "Content-Type": content_type,
                "Content-Disposition": f"attachment; filename=\"{safe}\"; filename*=UTF-8''{quote(filename)}",
            },
        )
        data = self._check(r)
        media_id = int(data["id"])
        if caption:
            self._check(self._http.post(f"{self.api}/media/{media_id}", json={"caption": caption}))
        return media_id

    # --- 監視（認証なしで読める公開情報） -----------------------------
    def latest_published(self, category_id: int, after: str) -> list[dict]:
        r = self._http.get(
            f"{self.api}/posts",
            params={"categories": category_id, "after": after, "per_page": 10,
                    "_fields": "id,date,link,title"},
        )
        return self._check(r)
