"""LLM の返答の形。structured outputs でこのスキーマを強制する。

LLM に任せるのは**抽出と、読まないと分からない判断（デザイン性・物語・日本との接点・種別）だけ**。
合算は Python 側で config の重みを使って行う（scoring/weights.py）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

KINDS = [
    "tiny_house_on_wheels",  # 車輪付きのタイニーハウス
    "trailer_caravan",       # トレーラーハウス・キャラバン
    "van_camper",            # バン・キャンピングカー
    "boat_floating",         # ボートハウス・浮かぶ家
    "container",             # コンテナハウス
    "treehouse",             # ツリーハウス
    "cabin_hut",             # 小屋・キャビン・ヒュッテ
    "prefab_modular",        # プレハブ・モジュール住宅
    "small_house",           # 地面に建つ小さめの住宅（ADU など）
    "other_building",        # 住まいだが小さくも動きもしない（邸宅・集合住宅・ホテル棟など）
    "not_a_building",        # 建物ではない（家具・プロダクト・ニュース・イベント）
]

# 抽出する事実。**記事に書いてあるものだけ**。無ければ空文字。
FACT_FIELDS = [
    "name",          # 物件名（作品名）
    "builder",       # ビルダー・メーカー
    "architect",     # 設計者・建築事務所
    "country",       # 国（日本語）
    "region",        # 州・地域・都市（日本語）
    "area",          # 面積（記事の表記のまま。単位も）
    "dimensions",    # 寸法（全長・幅など、記事の表記のまま）
    "price",         # 価格（記事の表記のまま。通貨も）
    "year",          # 完成年
    "photographer",  # 撮影者
]

_STR = {"type": "string"}
_INT = {"type": "integer"}

OUTPUT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "relevant", "relevant_reason", "kind", "facts",
        "design_score", "design_reason", "story_score", "story_reason",
        "japan_score", "japan_reason", "summary_ja", "highlights", "suggested_tags",
    ],
    "properties": {
        "relevant": {"type": "boolean"},
        "relevant_reason": _STR,
        "kind": {"type": "string", "enum": KINDS},
        "facts": {
            "type": "object",
            "additionalProperties": False,
            "required": FACT_FIELDS,
            "properties": {k: _STR for k in FACT_FIELDS},
        },
        "design_score": _INT,
        "design_reason": _STR,
        "story_score": _INT,
        "story_reason": _STR,
        "japan_score": _INT,
        "japan_reason": _STR,
        "summary_ja": _STR,
        "highlights": {"type": "array", "items": _STR},
        "suggested_tags": {"type": "array", "items": _STR},
    },
}


def _clamp(v) -> int:
    try:
        return max(0, min(100, int(v)))
    except (TypeError, ValueError):
        return 0


@dataclass
class Assessment:
    relevant: bool
    relevant_reason: str
    kind: str
    facts: dict[str, str]
    design_score: int
    design_reason: str
    story_score: int
    story_reason: str
    japan_score: int
    japan_reason: str
    summary_ja: str
    highlights: list[str] = field(default_factory=list)
    suggested_tags: list[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict) -> Assessment:
        facts = {k: str((data.get("facts") or {}).get(k) or "").strip() for k in FACT_FIELDS}
        kind = data.get("kind") if data.get("kind") in KINDS else "not_a_building"
        return cls(
            relevant=bool(data.get("relevant")),
            relevant_reason=str(data.get("relevant_reason") or ""),
            kind=kind,
            facts=facts,
            design_score=_clamp(data.get("design_score")),
            design_reason=str(data.get("design_reason") or ""),
            story_score=_clamp(data.get("story_score")),
            story_reason=str(data.get("story_reason") or ""),
            japan_score=_clamp(data.get("japan_score")),
            japan_reason=str(data.get("japan_reason") or ""),
            summary_ja=str(data.get("summary_ja") or ""),
            highlights=[str(x) for x in data.get("highlights") or []],
            suggested_tags=[str(x) for x in data.get("suggested_tags") or []],
        )

    def to_dict(self) -> dict:
        return {
            "relevant": self.relevant,
            "relevant_reason": self.relevant_reason,
            "kind": self.kind,
            "facts": self.facts,
            "design_score": self.design_score,
            "design_reason": self.design_reason,
            "story_score": self.story_score,
            "story_reason": self.story_reason,
            "japan_score": self.japan_score,
            "japan_reason": self.japan_reason,
            "summary_ja": self.summary_ja,
            "highlights": self.highlights,
            "suggested_tags": self.suggested_tags,
        }
