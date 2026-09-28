"""テスト用の偽物。外部（Claude API・WordPress）には一切出ない。"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import httpx

from yadokari.db.repository import NewArticle, insert_article
from yadokari.scoring.schema import FACT_FIELDS

SOURCE_TEXT = (
    "Black Clay designed the Harper tiny house in Australia. It measures 8 m long and 2.5 m wide, "
    "with about 20 square meters of living space. It was a 2025 Rising Star Award finalist. "
) * 5


def assessment(**over) -> dict:
    data = {
        "relevant": True, "relevant_reason": "", "kind": "tiny_house_on_wheels",
        "facts": {k: "" for k in FACT_FIELDS} | {"builder": "Black Clay", "country": "オーストラリア",
                                                 "name": "Harper"},
        "design_score": 80, "design_reason": "", "story_score": 70, "story_reason": "",
        "japan_score": 40, "japan_reason": "", "summary_ja": "要約",
        "highlights": ["全長8m"], "suggested_tags": ["タイニーハウス", "オーストラリア"],
    }
    data.update(over)
    return data


def add_article(conn, url="https://www.blackclay.com.au/harper", status="scored", images=9,
                score=80.0) -> int:
    article_id = insert_article(conn, NewArticle(
        source="t", source_url=url, title="Harper tiny house", content_text=SOURCE_TEXT,
        image_urls=[f"https://img.example.com/{i}.jpg" for i in range(images)],
        og_image="https://img.example.com/og.jpg",
    ))
    conn.execute(
        "UPDATE articles SET status = ?, score = ?, assessment = ?, score_detail = ? WHERE id = ?",
        (status, score, json.dumps(assessment(), ensure_ascii=False), json.dumps({}), article_id),
    )
    conn.commit()
    return article_id


def draft_json(**over) -> dict:
    data = {
        "catch": "「小さく住む」を、ここまで心地よく",
        "subject": "オーストラリア発のトレーラーハウス",
        "name": "Harper",
        "lead": ["オーストラリアのビルダー、Black Clayが手がけた「Harper」。",
                 "風景ごと住まいを考える姿勢が息づいている。"],
        "sections": [
            {"heading": "小さな家に込められた誠実さ",
             "paragraphs": ["全長8メートル、幅2.5メートル、約20㎡。", "2025年の賞の最終候補だ。"]},
            {"heading": "**内と外が溶け合う**", "paragraphs": ["- 大きな窓が景色を引き込む。"]},
        ],
        "closing": ["限られた空間を丁寧に使う日本の暮らしとも重なる。"],
        "description": "オーストラリアのビルダーBlack Clayが手がけたトレーラーハウス「Harper」。全長8メートル、約20㎡の室内に大きな窓と曲線の壁を持つ、風景にひらく小さな住まい。",
        "faq": [{"q": "Harperの広さは？", "a": "約20㎡。"}],
    }
    data.update(over)
    return data


class FakeLLM:
    """anthropic クライアントの代わり。create に来た引数を記録し、順に返す。"""

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads.pop(0)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False))],
        )


class FakeWP:
    """WordPress REST API の偽物（httpx.MockTransport）。投稿を辞書で持つ。"""

    def __init__(self, tags=None):
        self.posts: dict[int, dict] = {}
        self.media: list[dict] = []
        self.tags = tags or {"タイニーハウス": 274, "オーストラリア": 167}
        self.requests: list[httpx.Request] = []
        self._next = 1000

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/tags"):
            q = request.url.params.get("search")
            return httpx.Response(200, json=[{"id": i, "name": n} for n, i in self.tags.items() if n == q])
        if path.endswith("/media") and request.method == "POST":
            self._next += 1
            self.media.append({"id": self._next, "bytes": len(request.content)})
            return httpx.Response(201, json={"id": self._next})
        if re.search(r"/media/\d+$", path):
            return httpx.Response(200, json={"id": int(path.rsplit("/", 1)[-1])})
        if path.endswith("/posts") and request.method == "GET":
            slug = request.url.params.get("slug")
            return httpx.Response(200, json=[p for p in self.posts.values() if p["slug"] == slug])
        if path.endswith("/posts") and request.method == "POST":
            self._next += 1
            body = json.loads(request.content)
            post = {"id": self._next, **body, "link": f"https://yadokari.net/magazine/{self._next}/"}
            self.posts[post["id"]] = post
            return httpx.Response(201, json=post)
        m = re.search(r"/posts/(\d+)$", path)
        if m:
            post = self.posts[int(m.group(1))]
            if request.method == "POST":
                post.update(json.loads(request.content))
            return httpx.Response(200, json=post)
        return httpx.Response(404, json={"message": "not found"})

    def client(self, config):
        from yadokari.wordpress.client import WordPressClient

        return WordPressClient(config.wordpress, http=httpx.Client(transport=httpx.MockTransport(self.handler)))


def fake_fetch(url):
    return b"\xff\xd8jpegbytes", "image/jpeg"
