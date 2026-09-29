"""軸ごとの点数を出し、config.yaml の重みで合算する。

LLM が点を付けるのは design・story・japan の3つだけ。残りは手元の事実から決める:
  smallness … LLM が判定した「種別」を表で点にする（点そのものは LLM に付けさせない）
  photos    … 記事から数えた画像の枚数
  facts     … 抽出した事実（設計者・国・面積・価格）がいくつ揃っているか
  freshness … 元記事の公開日

relevant が false（建物でない・住まいでない）ものは合計0点。審査には残す。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from yadokari.config import Weights
from yadokari.scoring.schema import Assessment

SMALLNESS = {
    "tiny_house_on_wheels": 100,
    "trailer_caravan": 100,
    "van_camper": 100,
    "boat_floating": 90,
    "container": 90,
    "treehouse": 85,
    "cabin_hut": 80,
    "prefab_modular": 75,
    "small_house": 60,
    "other_building": 20,
    "not_a_building": 0,
}


# 「トレーラーハウス」で上位を狙えるか（2026-09-29 ユーザー指定の狙い）。
# トレーラー系を最優先、次に小屋・タイニーハウスの主キーワードで書けるもの
SEO = {
    "tiny_house_on_wheels": 100,
    "trailer_caravan": 100,
    "van_camper": 50,
    "cabin_hut": 60,
    "treehouse": 50,
    "container": 50,
    "prefab_modular": 50,
    "boat_floating": 40,
    "small_house": 40,
    "other_building": 10,
    "not_a_building": 0,
}


@dataclass
class Axis:
    name: str
    score: float
    weight: float
    note: str

    def to_dict(self) -> dict:
        return {"score": self.score, "weight": self.weight, "note": self.note}


def photos_score(n: int) -> tuple[float, str]:
    """既存記事は写真が中央値7枚・最少4枚（docs/existing-articles.md）。"""
    if n >= 7:
        return 100.0, f"{n}枚"
    if n >= 4:
        return 70.0, f"{n}枚"
    if n >= 1:
        return 30.0, f"{n}枚（記事にするには少ない）"
    return 0.0, "写真なし"


def facts_score(facts: dict[str, str]) -> tuple[float, str]:
    score = 0.0
    have = []
    if facts.get("builder") or facts.get("architect"):
        score += 40
        have.append("設計/ビルダー")
    if facts.get("country"):
        score += 30
        have.append("国")
    if facts.get("area") or facts.get("dimensions"):
        score += 20
        have.append("面積/寸法")
    if facts.get("price"):
        score += 10
        have.append("価格")
    return score, "・".join(have) or "事実が取れない"


def freshness_score(published_at: str | None, now: datetime | None = None) -> tuple[float, str]:
    if not published_at:
        return 50.0, "公開日不明"
    try:
        dt = datetime.fromisoformat(published_at)
    except ValueError:
        return 50.0, "公開日不明"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    days = ((now or datetime.now(UTC)) - dt).days
    if days <= 14:
        return 100.0, f"{days}日前"
    if days <= 30:
        return 80.0, f"{days}日前"
    if days <= 90:
        return 50.0, f"{days}日前"
    return 20.0, f"{days}日前"


def build_axes(
    weights: Weights, a: Assessment, image_count: int, published_at: str | None,
    now: datetime | None = None,
) -> list[Axis]:
    photos, photos_note = photos_score(image_count)
    facts, facts_note = facts_score(a.facts)
    fresh, fresh_note = freshness_score(published_at, now)
    return [
        Axis("design", float(a.design_score), weights.design, a.design_reason),
        Axis("story", float(a.story_score), weights.story, a.story_reason),
        Axis("smallness", float(SMALLNESS.get(a.kind, 0)), weights.smallness, a.kind),
        Axis("photos", photos, weights.photos, photos_note),
        Axis("japan", float(a.japan_score), weights.japan, a.japan_reason),
        Axis("facts", facts, weights.facts, facts_note),
        Axis("freshness", fresh, weights.freshness, fresh_note),
        Axis("seo", float(SEO.get(a.kind, 0)), weights.seo, a.kind),
    ]


# 量産型（カタログ型）は審査ライン（50）の下に止める。消さずに「採点済み」には残す。
# 2026-09-29: ルールでデザイン・物語を 40 以下にさせても、小ささ・写真・SEO が満点なので
# 61〜65点で審査待ちに残った。軸をいじるより上限で止める方が確実
CATALOG_CAP = 45.0


def total(axes: list[Axis], a: Assessment) -> float:
    if not a.relevant:
        return 0.0
    score = round(sum(ax.score * ax.weight for ax in axes), 1)
    return min(score, CATALOG_CAP) if a.catalog_model else score
