from __future__ import annotations

import logging

from yadokari import logging_setup


def test_read_only_filesystem_falls_back_to_stderr(tmp_path, monkeypatch):
    """Vercel ではディレクトリがあってもファイルを開くと Read-only file system で落ちた（2026-09-29）。"""
    def _read_only(*args, **kwargs):
        raise OSError(30, "Read-only file system")

    root = logging.getLogger()
    before = list(root.handlers)
    monkeypatch.setattr(logging_setup, "_configured", False)
    monkeypatch.setattr(logging, "FileHandler", _read_only)
    try:
        logging_setup.setup_logging(tmp_path)
        added = [h for h in root.handlers if h not in before]
        assert added and all(type(h) is logging.StreamHandler for h in added)
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
