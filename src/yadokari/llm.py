"""Claude API の共通の呼び出し。structured outputs で JSON を返させる。

採点（scoring/client.py）は再試行やエラーの扱いが独自なので別に持っている。
下書き生成と学習ループはここを使う。
"""

from __future__ import annotations

import json

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    pass


def make_client(api_key: str):
    import anthropic

    return anthropic.Anthropic(api_key=api_key)


def call_json(
    client,
    *,
    model: str,
    system: str,
    messages: list[dict],
    schema: dict,
    max_tokens: int = 16000,
    effort: str | None = None,
    fallbacks: bool = True,
):
    """JSON を1つ返させる。(dict, response) を返す。

    effort は持たないモデルがある（Haiku 4.5）。None なら送らない。
    fallbacks が True なら、安全分類器が断ったとき別モデルで自動でやり直す。
    """
    output_config: dict = {"format": {"type": "json_schema", "schema": schema}}
    if effort:
        output_config["effort"] = effort
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
        output_config=output_config,
    )
    if fallbacks:
        response = client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
    else:
        response = client.messages.create(**kwargs)
    reason = getattr(response, "stop_reason", None)
    if reason == "max_tokens":
        raise LLMError(f"返答が max_tokens（{max_tokens}）で打ち切られました")
    if reason == "refusal":
        raise LLMError("モデルが応答を断りました（フォールバック先も含めて）")
    for block in response.content:
        if getattr(block, "type", None) == "text":
            try:
                return json.loads(block.text), response
            except json.JSONDecodeError as exc:
                raise LLMError(f"JSON として読めませんでした: {exc}") from exc
    raise LLMError("返答にテキストが含まれていません")
