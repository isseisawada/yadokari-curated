from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from yadokari.config import Config
from yadokari.db.connection import connect
from yadokari.db.migrate import migrate

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config() -> Config:
    """リポジトリの config.yaml そのもの。設定を壊したらテストで気づく。"""
    return Config.model_validate(yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")))


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    migrate(path)
    conn = connect(path)
    yield conn
    conn.close()
