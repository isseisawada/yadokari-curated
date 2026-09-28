"""設定の読み込み。config.yaml が唯一の設定ファイル。

秘匿値（DATABASE_URL・ANTHROPIC_API_KEY）は config.yaml に置かず、
.env と実行環境の環境変数からだけ読む。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator, model_validator

DEFAULT_CONFIG = Path("config.yaml")


class AppConfig(BaseModel):
    db_path: str = "data/yadokari.db"
    log_dir: str = "logs"
    log_level: str = "INFO"
    timezone: str = "Asia/Tokyo"

    def target(self) -> str:
        """接続先。DATABASE_URL があれば PostgreSQL、無ければ SQLite。"""
        return os.environ.get("DATABASE_URL") or self.db_path


class HttpConfig(BaseModel):
    user_agent: str
    request_interval_sec: float = 3.0
    max_concurrency_per_domain: int = 1
    timeout_sec: float = 30.0
    max_retries: int = 3
    backoff_factor: float = 2.0
    respect_robots_txt: bool = True

    # 収集ポリシーの下限。設定で緩められないようにしておく。
    @field_validator("request_interval_sec")
    @classmethod
    def _min_interval(cls, v: float) -> float:
        if v < 3.0:
            raise ValueError("request_interval_sec は 3.0 秒以上にすること")
        return v

    @field_validator("max_concurrency_per_domain")
    @classmethod
    def _no_parallel_per_domain(cls, v: int) -> int:
        if v != 1:
            raise ValueError("同一ドメインへの並列アクセスは禁止（max_concurrency_per_domain は 1）")
        return v

    @field_validator("respect_robots_txt")
    @classmethod
    def _robots_required(cls, v: bool) -> bool:
        if not v:
            raise ValueError("robots.txt の尊重は無効にできない")
        return v

    @field_validator("user_agent")
    @classmethod
    def _contact_in_ua(cls, v: str) -> str:
        if "@" not in v and "http" not in v:
            raise ValueError("User-Agent に連絡先（メールアドレスかURL）を入れること")
        return v


class Source(BaseModel):
    """収集元1つ。

    feed        : RSS/Atom。あれば先に読む
    index_urls  : 一覧ページ。フィードの窓が短いソース（ArchDaily）の入口、
                  またはフィードの取りこぼしを拾うバックフィル
    url_include : 一覧ページから拾うリンクの正規表現（記事URLの形）
    fetch_article: 記事ページを取りに行くか。Dezeen はデータセンターから 403 に
                  なるので false にして、フィードの全文だけで回す
    prefilter   : タイトルと冒頭で小さな家・動く家のキーワードに当たるものだけ
                  採点に回す。対象が広いフィード（Dwell 全体など）で LLM の費用を抑える
    """

    name: str
    enabled: bool = True
    feed: str | None = None
    index_urls: list[str] = []
    url_include: str | None = None
    fetch_article: bool = True
    prefilter: bool = False
    note: str | None = None

    @model_validator(mode="after")
    def _has_entry(self) -> Source:
        if not self.feed and not self.index_urls:
            raise ValueError(f"{self.name}: feed か index_urls のどちらかが要る")
        if self.index_urls and not self.url_include:
            raise ValueError(f"{self.name}: index_urls を使うときは url_include も書く")
        return self


class CollectConfig(BaseModel):
    lookback_days: int = 30
    per_source_limit: int = 20
    min_text_chars: int = 400


class Weights(BaseModel):
    design: float
    story: float
    smallness: float
    photos: float
    japan: float
    facts: float
    freshness: float

    @model_validator(mode="after")
    def _sum_to_one(self) -> Weights:
        total = sum(self.model_dump().values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"scoring.weights の合計が 1.0 になっていない（{total}）")
        return self


class ScoringConfig(BaseModel):
    model: str = "claude-opus-5-5"
    # Opus 5.5 は effort の既定が medium。採点は判断が単純なので low で足りる。
    # effort を持たないモデル（Haiku 4.5）に切り替えるときは null にする。
    effort: str | None = "low"
    max_tokens: int = 16000
    # 安全分類器が判定を断ったとき、同じリクエストを別モデルで自動でやり直す
    # （server-side fallback）。Opus 5.5 などでは既定で入れておく
    fallbacks: bool = True
    weights: Weights
    # この点数未満は審査画面の既定の一覧に出さない（消しはしない）
    review_threshold: float = 50.0


class DraftingConfig(BaseModel):
    model: str = "claude-opus-5-5"
    # 下書きは文章の質が効くので採点より上げる
    effort: str | None = "medium"
    max_tokens: int = 16000
    fallbacks: bool = True
    # 本文に並べる写真の上限（既存記事は中央値7枚）
    max_images: int = 8
    # 本文の目安の文字数（既存記事の中央値 約1,600字）
    target_chars: int = 1600
    title_prefix: str = "【海外事例】"


class WordPressConfig(BaseModel):
    """資格情報は環境変数（WP_USER / WP_APP_PASSWORD）からだけ読む。"""

    base_url: str = "https://yadokari.net"
    category_ids: list[int] = [2184]
    # **予約投稿（status=future）を許すか。** 最初は下書きだけを作る段階から始め、
    # WP 上で見て OK が出てから true にする
    allow_schedule: bool = False
    # 既定の公開時刻（既存記事の大半が 19:00 JST）と1日の本数
    post_times: list[str] = ["19:00"]
    slug_prefix: str = "yc-"
    # アイキャッチは元写真を WP のメディアに取り込む（既存記事と同じ運用。2026-09-28 ユーザー判断）
    upload_featured_image: bool = True
    timeout_sec: float = 60.0

    @property
    def user(self) -> str:
        return os.environ.get("WP_USER", "")

    @property
    def app_password(self) -> str:
        return os.environ.get("WP_APP_PASSWORD", "")

    def require_credentials(self) -> tuple[str, str]:
        if not self.user or not self.app_password:
            raise RuntimeError(
                "WP_USER と WP_APP_PASSWORD が未設定です（WordPress の Application Password）。"
                ".env か実行環境の環境変数に入れてください"
            )
        return self.user, self.app_password


class LearningConfig(BaseModel):
    # 非承認理由のタグ。審査画面の選択肢にもなる
    tags: list[str] = [
        "題材が合わない（小さくない・住まいでない）",
        "デザインが弱い",
        "写真が少ない・質が低い",
        "物語が書けない（情報が薄い）",
        "販売・広告の色が強い",
        "既に紹介済み・似た事例がある",
        "other",
    ]
    rule_min_hits: int = 3
    batch_size: int = 20


class Config(BaseModel):
    app: AppConfig = AppConfig()
    http: HttpConfig
    collect: CollectConfig = CollectConfig()
    sources: list[Source]
    scoring: ScoringConfig
    drafting: DraftingConfig = DraftingConfig()
    wordpress: WordPressConfig = WordPressConfig()
    learning: LearningConfig = LearningConfig()

    @property
    def anthropic_api_key(self) -> str:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY が未設定です。.env.example を .env にコピーして設定してください。"
            )
        return key

    def source(self, name: str) -> Source:
        for s in self.sources:
            if s.name == name:
                return s
        raise KeyError(f"ソース {name!r} は config.yaml にありません")


def load_config(path: str | Path = DEFAULT_CONFIG) -> Config:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:  # pragma: no cover - dotenv は任意
        pass
    with open(path, encoding="utf-8") as f:
        return Config.model_validate(yaml.safe_load(f))
