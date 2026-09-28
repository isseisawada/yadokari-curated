from __future__ import annotations

import pytest
from pydantic import ValidationError

from yadokari.config import HttpConfig, Source, Weights


def test_repo_config_loads(config):
    assert config.scoring.model
    assert {s.name for s in config.sources} >= {"archdaily", "dwell", "dezeen"}


def test_interval_below_three_seconds_is_rejected():
    with pytest.raises(ValidationError):
        HttpConfig(user_agent="x (+mailto:a@b)", request_interval_sec=2.9)


def test_parallel_per_domain_is_rejected():
    with pytest.raises(ValidationError):
        HttpConfig(user_agent="x (+mailto:a@b)", max_concurrency_per_domain=2)


def test_robots_cannot_be_disabled():
    with pytest.raises(ValidationError):
        HttpConfig(user_agent="x (+mailto:a@b)", respect_robots_txt=False)


def test_user_agent_needs_contact():
    with pytest.raises(ValidationError):
        HttpConfig(user_agent="yadokari-bot")


def test_weights_must_sum_to_one():
    with pytest.raises(ValidationError):
        Weights(design=0.5, story=0.5, smallness=0.1, photos=0, japan=0, facts=0, freshness=0)


def test_index_urls_need_url_include():
    with pytest.raises(ValidationError):
        Source(name="x", index_urls=["https://example.com/list"])


def test_source_needs_an_entry_point():
    with pytest.raises(ValidationError):
        Source(name="x")


def test_dezeen_does_not_fetch_article_pages(config):
    # 記事ページはデータセンターの IP から 403（docs/source-survey.md）
    assert config.source("dezeen").fetch_article is False
