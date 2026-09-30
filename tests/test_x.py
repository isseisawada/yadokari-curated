from __future__ import annotations

from yadokari.config import XConfig
from yadokari.sns.x import LIMIT, URL_WEIGHT, compose, weight

TITLE = "【海外事例】森に浮かぶ、ひと部屋の隠れ家。アメリカ・ワシントン州の森に浮かぶ高床の小屋「Blakely Cabin」"
QUOTE = "ひと部屋だけの小屋でも、季節に合わせて扉をひらき、閉じるだけで、暮らしの表情はこれほど変わる。夏は外へ、冬は火のそばへ。"


def test_weight_counts_japanese_as_two():
    assert weight("abc") == 3
    assert weight("小屋") == 4


def test_compose_drops_prefix_and_fits_with_url():
    text = compose(XConfig(), title=TITLE, prefix="【海外事例】", quote=QUOTE,
                   tags=["小屋", "タイニーハウス", "アメリカ"])
    assert not text.startswith("【海外事例】")
    assert text.startswith("森に浮かぶ")
    assert text.endswith("#タイニーハウス #小屋")
    assert "アメリカ" not in text.split("\n")[-1]
    assert weight(text) + 2 + URL_WEIGHT <= LIMIT


def test_long_quote_is_cut_not_the_hashtags():
    text = compose(XConfig(), title=TITLE, prefix="【海外事例】", quote=QUOTE * 5, tags=["小屋"])
    assert "…" in text and text.endswith("#小屋")
    assert weight(text) + 2 + URL_WEIGHT <= LIMIT


def test_post_signs_with_oauth1_and_returns_id():
    import httpx

    from yadokari.sns.x import post

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["url"] = str(request.url)
        return httpx.Response(201, json={"data": {"id": "123", "text": "x"}})

    keys = {"api_key": "ck", "api_secret": "cs", "access_token": "at", "access_secret": "as"}
    tid = post("こんにちは", keys, http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert tid == "123"
    assert seen["url"] == "https://api.x.com/2/tweets"
    assert seen["auth"].startswith("OAuth ") and 'oauth_signature="' in seen["auth"]
    assert "cs" not in seen["auth"] and "as" not in seen["auth"].replace("oauth_", "")
