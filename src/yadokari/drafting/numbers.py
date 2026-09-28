"""検算: 下書きに**元資料に無い数字**が出ていないか。

frmg の report/comment.py の unsupported_numbers をそのまま持ってきた。
言い回しで「推測するな」と縛るより確実で、frmg では誤りを何度も止めた。

元資料 = 元記事の本文・タイトル・LLM が抽出した事実。
単位換算（feet → m、ドル → 円）で生まれた数字も「元資料に無い」として拾う。
プロンプトで換算しないよう指示しているが、守られないことがあるので出たものを見る。
"""

from __future__ import annotations

import re
import unicodedata

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
# 1桁でも、これが続いていれば材料の数字を指している（「3棟」「2部屋」）
_COUNTER = re.compile(
    r"[〜～~\-–—ー、,.／/や0-9]*(本|枚|点|件|％|%|倍|割|棟|部屋|室|階|人|台|年|か月|ヶ月|日|m|メートル|㎡|平米|フィート|ドル|円|ユーロ|ポンド|万)"
)


def _norm(text: str) -> str:
    # 全角数字（１２）や ㎡ などを半角に寄せる
    return unicodedata.normalize("NFKC", text)


def numbers_in(text: str) -> set[str]:
    return {m.group(0).replace(",", "") for m in _NUMBER.finditer(_norm(text))}


def _checked(body: str) -> set[str]:
    """検算にかける数字。1桁は単位が付いているときだけ見る
    （「2つの箱」「3つの工夫」のような言い回しを全部拾うと通らない）。"""
    body = _norm(body)
    out: set[str] = set()
    for match in _NUMBER.finditer(body):
        value = match.group(0).replace(",", "")
        if len(value.split(".")[0]) >= 2 or _COUNTER.match(body, match.end()):
            out.add(value)
    return out


def unsupported_numbers(body: str, source: str) -> set[str]:
    return _checked(body) - numbers_in(source)


def plain(text: str) -> str:
    """マークダウンの記号を落とす。**WP の本文にそのまま出るため**（frmg で3回漏れた）。

    消すのは記号だけ。数字も語も変えない（検算の前に通すため）。
    """
    out = text.replace("**", "").replace("__", "").replace("`", "")
    lines = []
    for line in out.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            line = stripped.lstrip("#").lstrip()
        elif re.match(r"^[-*・]\s+", stripped):
            line = re.sub(r"^[-*・]\s+", "", stripped)
        lines.append(line)
    return "\n".join(lines).strip()
