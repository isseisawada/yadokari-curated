"""Vercel の入口（Python / FastAPI）。審査画面を出す。

Vercel はリポジトリの直下の app.py から `app` を探す。パッケージは src/ にあるので、
パスに足してから読み込む。資格情報（REVIEW_UI_USER / REVIEW_UI_PASSWORD）が無ければ
起動しない（yadokari.web.asgi の歯止め）。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))
os.chdir(HERE)  # config.yaml を読むため

from yadokari.web.asgi import app  # noqa: E402

__all__ = ["app"]
