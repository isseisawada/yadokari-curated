"""X の投稿文（2026-09-30）。URL は WP 側が公開のときに末尾へ足す（パーマリンクが決まるのはそのとき）。

X の文字数は重み付き: 半角英数などは1、日本語は2、URL は長さに関係なく23。上限280。
"""

from __future__ import annotations

import unicodedata

from yadokari.config import XConfig

LIMIT = 280
URL_WEIGHT = 23
# twitter-text の「重み1」の範囲
_LIGHT = ((0x0000, 0x10FF), (0x2000, 0x200D), (0x2010, 0x201F), (0x2032, 0x2037))


def weight(text: str) -> int:
    text = unicodedata.normalize("NFC", text)
    return sum(1 if any(a <= ord(c) <= b for a, b in _LIGHT) else 2 for c in text)


def _cut(text: str, budget: int) -> str:
    if weight(text) <= budget:
        return text
    out = ""
    for c in text:
        if weight(out + c + "…") > budget:
            break
        out += c
    return out.rstrip("、。 ") + "…"


def compose(cfg: XConfig, *, title: str, prefix: str, quote: str, tags: list[str]) -> str:
    """タイトル（【海外事例】は外す）＋ QUOTE ＋ ハッシュタグ。末尾の URL の分を空けておく。"""
    head = title.removeprefix(prefix).strip()
    tagline = " ".join(f"#{t}" for t in [t for t in cfg.hashtags if t in tags][: cfg.max_hashtags])
    budget = LIMIT - URL_WEIGHT - 2  # URL の前の改行2つ
    fixed = weight(tagline) + (2 if tagline else 0)
    head = _cut(head, max(budget - fixed, 20))
    text = head
    room = budget - fixed - weight(head) - 2
    if quote and room >= 40:
        text += "\n\n" + _cut(quote.strip(), room)
    if tagline:
        text += "\n\n" + tagline
    return text


# ----------------------------------------------------------------------
# こちらから直接投稿する（例外用。ふだんは WP の mu-plugin が公開の瞬間に投稿する）
# 2026-09-30: X を入れる前に公開済みだった記事を、ユーザーの指示で後から投稿するために追加
# ----------------------------------------------------------------------
def _oauth_header(method: str, url: str, keys: dict[str, str]) -> str:
    import base64
    import hashlib
    import hmac
    import secrets
    import time
    from urllib.parse import quote

    def enc(s: str) -> str:
        return quote(s, safe="~")

    oauth = {
        "oauth_consumer_key": keys["api_key"],
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": keys["access_token"],
        "oauth_version": "1.0",
    }
    params = "&".join(f"{enc(k)}={enc(v)}" for k, v in sorted(oauth.items()))
    base = "&".join([method, enc(url), enc(params)])
    key = f"{enc(keys['api_secret'])}&{enc(keys['access_secret'])}"
    oauth["oauth_signature"] = base64.b64encode(
        hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{enc(k)}="{enc(v)}"' for k, v in oauth.items())


def keys_from_env() -> dict[str, str]:
    import os

    names = {"api_key": "X_API_KEY", "api_secret": "X_API_SECRET",
             "access_token": "X_ACCESS_TOKEN", "access_secret": "X_ACCESS_SECRET"}
    keys = {k: os.environ.get(v, "") for k, v in names.items()}
    missing = [names[k] for k, v in keys.items() if not v]
    if missing:
        raise RuntimeError(f"X の鍵が未設定です: {', '.join(missing)}")
    return keys


def post(text: str, keys: dict[str, str], http=None) -> str:
    """X API v2 に投稿してツイート ID を返す。"""
    import httpx

    url = "https://api.x.com/2/tweets"
    client = http or httpx.Client(timeout=20)
    try:
        r = client.post(url, json={"text": text},
                        headers={"Authorization": _oauth_header("POST", url, keys)})
    finally:
        if http is None:
            client.close()
    if r.status_code != 201:
        raise RuntimeError(f"X API {r.status_code}: {r.text[:300]}")
    return str(r.json()["data"]["id"])
