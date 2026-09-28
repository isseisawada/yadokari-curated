"""学習ループ。frmg の learning/loop.py と同じ考え方。

    非承認理由 → タグ分類 → 頻出タグをルール候補として提示 → **人が承認**
                                                                ↓
                              承認されたルールだけが次回の採点プロンプトに載る

ルールは自動で適用しない。「3回同じ指摘が出た」ことと「今後それを恒久的に
基準にしてよい」ことは別なので、必ず人の承認を挟む。

審査画面で人がタグを選んだ非承認は、LLM の分類を飛ばす（人の判断が優先）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from yadokari.config import Config
from yadokari.db.connection import DbConnection, Row
from yadokari.db.repository import (
    reasons_for_tag,
    set_feedback_tag,
    tag_counts,
    untagged_feedback,
    upsert_rule_candidate,
)
from yadokari.llm import call_json, make_client
from yadokari.logging_setup import get_logger

log = get_logger(__name__)

_RULE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["proposal"],
    "properties": {"proposal": {"type": "string"}},
}


def _tagging_schema(tags: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["assignments"],
        "properties": {
            "assignments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "tag"],
                    "properties": {"id": {"type": "integer"}, "tag": {"type": "string", "enum": tags}},
                },
            }
        },
    }


@dataclass
class LearnStats:
    tagged: int = 0
    tagging_failed: int = 0
    new_candidates: list[str] = field(default_factory=list)
    updated_candidates: int = 0

    def summary(self) -> str:
        return (
            f"理由を分類 {self.tagged} 件（失敗 {self.tagging_failed}）/ "
            f"新しいルール候補 {len(self.new_candidates)} 件（既存の更新 {self.updated_candidates}）"
        )


class LearningClient:
    def __init__(self, config: Config, client=None) -> None:
        self.config = config
        self._client = client or make_client(config.anthropic_api_key)

    def _json(self, system: str, user: str, schema: dict) -> dict:
        s = self.config.scoring
        data, _ = call_json(
            self._client, model=s.model, system=system,
            messages=[{"role": "user", "content": user}], schema=schema,
            max_tokens=s.max_tokens, effort=s.effort, fallbacks=s.fallbacks,
        )
        return data

    def classify(self, rows: list[Row], tags: list[str]) -> dict[int, str]:
        listing = "\n".join(f"{r['id']}: {r['reason']}" for r in rows)
        result = self._json(
            "あなたは YADOKARI.net（世界の小さな家・動く家を紹介するメディア）の編集アシスタントです。"
            "編集者が記事候補を非承認にした理由を、決められたタグに分類します。"
            "どれにも当てはまらないものは other にしてください。",
            f"次の理由をそれぞれ分類してください。\n\n{listing}",
            _tagging_schema(tags),
        )
        return {int(a["id"]): a["tag"] for a in result.get("assignments", [])}

    def propose_rule(self, tag: str, reasons: list[str], hits: int) -> str:
        joined = "\n".join(f"- {r}" for r in reasons)
        result = self._json(
            "あなたは YADOKARI.net の編集者です。繰り返し非承認になっている理由から、"
            "今後の採点に使う指示を1文で書きます。",
            (
                f"タグ「{tag}」の理由が {hits} 件たまりました。\n{joined}\n\n"
                "これらに共通する判断基準を、採点のプロンプトに載せる1文にしてください。"
                "「〜は relevant を false にする」「〜は story_score を下げる」のように、"
                "記事を読んで判定できる形で書いてください。"
            ),
            _RULE_SCHEMA,
        )
        return str(result["proposal"]).strip()


def run_learning(config: Config, conn: DbConnection, client: LearningClient | None = None) -> LearnStats:
    stats = LearnStats()
    rows = untagged_feedback(conn, config.learning.batch_size)
    if rows:
        client = client or LearningClient(config)
        try:
            assignments = client.classify(rows, config.learning.tags)
        except Exception as exc:  # noqa: BLE001 - 分類の失敗でルール生成まで止めない
            log.error("理由の分類に失敗しました: %s", exc)
            assignments = {}
            stats.tagging_failed = len(rows)
        for r in rows:
            tag = assignments.get(int(r["id"]))
            if tag is None:
                continue
            set_feedback_tag(conn, int(r["id"]), tag)
            stats.tagged += 1
        conn.commit()

    for r in tag_counts(conn):
        if r["hits"] < config.learning.rule_min_hits:
            continue
        tag = r["tag"]
        client = client or LearningClient(config)
        try:
            proposal = client.propose_rule(tag, reasons_for_tag(conn, tag), int(r["hits"]))
        except Exception as exc:  # noqa: BLE001 - 1タグの失敗で全体を止めない
            log.error("ルール文の生成に失敗しました: tag=%s (%s)", tag, exc)
            continue
        if upsert_rule_candidate(conn, tag, int(r["hits"]), proposal):
            stats.new_candidates.append(f"{tag}（{r['hits']}件）: {proposal}")
        else:
            stats.updated_candidates += 1
        conn.commit()
    log.info(stats.summary())
    return stats
