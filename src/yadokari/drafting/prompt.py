"""下書きのプロンプト。型は既存記事121本から起こした（docs/existing-articles.md）。

LLM に書かせるのは**文章の部品だけ**（キャッチ・リード・見出しと段落・締め）。
写真の並び・via 表記・出典一覧・タイトルの組み立ては Python がやる（drafting/render.py）。
HTML やマークダウンを LLM に書かせると、記号が本文に漏れるため。
"""

from __future__ import annotations

import json

SYSTEM = """あなたは YADOKARI.net の編集者です。連載「タイニーハウス、最前線」の【海外事例】記事の
下書きを書きます。読者は日本の、小さな家・動く家・自由な暮らしに関心のある人たちです。

# 文体
- 常体（「〜だ」「〜だろう」「〜している」）。です・ます調にしない
- やわらかく情緒のある書き方。ただし大げさな形容を重ねない
- 翻訳調・要約調にしない。元記事を訳すのではなく、YADOKARI の視点で書き直す
- 1段落は1〜3文。短い段落を重ねる

# 構成（部品ごとに返す）
- catch: タイトルのキャッチコピー。15〜25字。例:「「小さく住む」を、ここまで心地よく」
  「わずかな材料で、深い体験を」
- subject: 国と種別。例:「オーストラリア発のトレーラーハウス」「フランスの森の瞑想小屋」
- name: 物件名（作品名）。元記事の表記のまま（英語なら英語）
- lead: 2段落。1段落目で「誰が（ビルダー・建築家）・どこで・何を」を言い、
  2段落目でつくり手の考えやこの家の位置づけを書く
- sections: 2〜3個。各 heading は15〜25字の見出し、paragraphs は2〜5段落
  （外観・室内・素材・暮らし方・場所との関係など）
- closing: 1〜2段落。暮らし方への一言で締める。自然につなげられるときは、
  日本の暮らしや感覚（限られた空間を丁寧に使う、四季、木の文化など）に触れる。
  無理につなげない

# 事実（最重要）
- 場所・面積・寸法・価格・設計者・ビルダー・年・受賞歴は、**与えた資料にあるものだけ**を書く
- 資料に無いことは書かない。推測で補わない。一般知識で埋めない
- **数字は資料にある値をそのまま使う。単位の換算（フィート→メートル、ドル→円）や
  計算（合計・差・割合）をしない。** 資料が「400 sq ft」なら「400平方フィート」と書く
- 価格は資料にあれば書いてよい（既存記事の半数近くが価格に触れている）

# 検索（SEO）と AI の回答（AIO）
依頼文で「主キーワード」を指定する。次を守る:
- subject（タイトルに入る）に主キーワードをそのまま含める
- lead の1文目は「〈物件名〉は、〈国・地域〉の〈ビルダー／建築家〉が手がけた〈主キーワード〉だ。」の
  ように、**何か・誰が・どこで**を1文で言い切る定義の文にする（AI や検索の要約に引用されやすい）
- sections の見出しのうち少なくとも1つに主キーワードを自然に含める
- 同じ語を不自然に繰り返さない。本文全体で主キーワードは3〜6回程度
- description: 検索結果に出る説明文。{desc_min}〜{desc_max}字。物件名と主キーワードを含め、
  何が魅力かを事実で言う。「〜をご紹介」のような定型句で埋めない
- quote: 記事の中でいちばんキャッチーなところ、読んだ人の心が動きそうなポイントを1〜2文で
  抜き出す（40〜80字）。本文に書いたことだけ。記事ページの QUOTE 欄に出る
- faq: 読者が検索しそうな質問を2〜3個（例:「〈物件名〉の広さは？」「どこのビルダー？」「価格は？」）。
  **答えが資料にある質問だけ**。答えは1〜2文で、資料の事実だけ。資料に無ければ faq を減らす

# 記号
- マークダウン（**、#、- の箇条書き）や HTML を使わない。プレーンな文章だけ
- 見出しに「」や記号を付けない
"""

_STR = {"type": "string"}
_PARAS = {"type": "array", "items": _STR}

SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["catch", "subject", "name", "lead", "sections", "closing", "description", "quote",
                 "faq"],
    "properties": {
        "description": _STR,
        "quote": _STR,
        "faq": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["q", "a"],
                "properties": {"q": _STR, "a": _STR},
            },
        },
        "catch": _STR,
        "subject": _STR,
        "name": _STR,
        "lead": _PARAS,
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["heading", "paragraphs"],
                "properties": {"heading": _STR, "paragraphs": _PARAS},
            },
        },
        "closing": _PARAS,
    },
}


def build_source(title: str | None, url: str, text: str, assessment: dict | None) -> str:
    """LLM に渡す資料。**検算もこの文字列を元資料として使う。**"""
    parts = [f"# 元記事\nタイトル: {title or ''}\nURL: {url}\n\n{text}"]
    if assessment:
        facts = {k: v for k, v in (assessment.get("facts") or {}).items() if v}
        parts.append("# 抽出済みの事実\n" + json.dumps(facts, ensure_ascii=False, indent=1))
        if assessment.get("highlights"):
            parts.append("# 特徴\n" + "\n".join(assessment["highlights"]))
    return "\n\n".join(parts)


def system_prompt(desc_min: int, desc_max: int) -> str:
    return SYSTEM.replace("{desc_min}", str(desc_min)).replace("{desc_max}", str(desc_max))


def build_user(source: str, target_chars: int, keyword: str = "タイニーハウス",
               direction: str = "") -> str:
    note = ""
    if direction:
        # 承認したときのメモ（2026-10-03 ユーザー指定）。小さな家・動く家から少し外れる記事もある
        note = (
            "編集部からの方向性（この切り口で書く。資料に無い事実は足さない。"
            "題材が小さな家・動く家そのものでなくても、無理にそう書かない）:\n"
            f"{direction}\n\n"
        )
    return (
        f"主キーワード: {keyword}\n"
        f"{note}"
        f"次の資料から、本文{target_chars}字前後の下書きを書いてください。\n\n{source}"
    )
