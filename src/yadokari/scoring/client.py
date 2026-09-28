"""Claude API の呼び出し。frmg の scoring/client.py を元にしている。

structured outputs（output_config.format）で JSON スキーマを渡し、返答の形を保証する。
パースの失敗はここで吸収し、上位には Assessment だけを渡す。
"""

from __future__ import annotations

import json
import time

from yadokari.config import Config
from yadokari.logging_setup import get_logger
from yadokari.scoring.schema import OUTPUT_SCHEMA, Assessment

log = get_logger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ScoringError(RuntimeError):
    """採点に失敗した。呼び出し側で1件スキップして続行する。"""


class ScoringClient:
    def __init__(self, config: Config, system_prompt: str, client=None) -> None:
        self.config = config
        self.system_prompt = system_prompt
        self.model = config.scoring.model
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        self._client = client

    def assess(self, user_prompt: str, max_attempts: int = 3) -> Assessment:
        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return self._assess_once(user_prompt)
            except Exception as exc:  # 再試行できるかを判定して分ける
                last_error = exc
                if not _is_retryable(exc):
                    raise ScoringError(f"再試行しても解決しない失敗です: {exc}") from exc
                if attempt == max_attempts:
                    break
                wait = 2.0 ** attempt
                log.warning("採点に失敗（%d/%d）: %s — %.0f秒後に再試行", attempt, max_attempts, exc, wait)
                time.sleep(wait)
        raise ScoringError(f"{max_attempts}回試して判定できませんでした: {last_error}")

    def _output_config(self) -> dict:
        """effort は持たないモデルがある（Haiku 4.5）。渡すと 400 で全件落ちるので、
        config で null にしたときは送らない（frmg で踏んだ）。"""
        out: dict = {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}}
        if self.config.scoring.effort:
            out["effort"] = self.config.scoring.effort
        return out

    def _assess_once(self, user_prompt: str) -> Assessment:
        kwargs = dict(
            model=self.model,
            max_tokens=self.config.scoring.max_tokens,
            system=self.system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            output_config=self._output_config(),
            cache_control={"type": "ephemeral"},
        )
        if self.config.scoring.fallbacks:
            response = self._client.beta.messages.create(
                betas=[FALLBACK_BETA], fallbacks="default", **kwargs
            )
        else:
            response = self._client.messages.create(**kwargs)
        _check_stop_reason(response, self.config.scoring.max_tokens)
        return Assessment.from_json(json.loads(_text_of(response)))


def _is_retryable(exc: Exception) -> bool:
    """鍵の不備（401）・権限（403）・リクエストの誤り（400）は何度送っても同じ。"""
    import anthropic

    if isinstance(exc, ScoringError):
        return False
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    return isinstance(exc, anthropic.APIConnectionError)


def _check_stop_reason(response, max_tokens: int) -> None:
    reason = getattr(response, "stop_reason", None)
    if reason == "max_tokens":
        raise ScoringError(
            f"返答が max_tokens（{max_tokens}）で打ち切られました。"
            "config.yaml の scoring.max_tokens を増やすか effort を下げてください"
        )
    if reason == "refusal":
        raise ScoringError("モデルが判定を断りました（フォールバック先も含めて）")


def _text_of(response) -> str:
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise ScoringError("返答にテキストが含まれていません")
