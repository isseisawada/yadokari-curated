"""公開ホスティング（Render）用の入口。

    uvicorn yadokari.web.asgi:app --host 0.0.0.0 --port $PORT

**資格情報（REVIEW_UI_USER / REVIEW_UI_PASSWORD）が無ければ起動しない。**
認証なしで外向けに立ち上がる経路を作らない（frmg と同じ歯止め）。
"""

from __future__ import annotations

from yadokari.config import load_config
from yadokari.db.migrate import ensure_migrated
from yadokari.logging_setup import setup_logging
from yadokari.web.app import create_app
from yadokari.web.auth import credentials_from_env


def build_app():
    config = load_config()
    setup_logging(config.app.log_dir, config.app.log_level)
    auth = credentials_from_env()
    if auth is None:
        raise RuntimeError(
            "REVIEW_UI_USER と REVIEW_UI_PASSWORD が未設定です。"
            "公開して使う経路なので、認証なしでは起動しません"
        )
    ensure_migrated(config.app.target())
    return create_app(config, auth=auth)


app = build_app()
