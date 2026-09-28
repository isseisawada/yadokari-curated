from __future__ import annotations

from datetime import UTC, datetime

from helpers import FakeLLM, add_article, assessment
from test_collect import BODY, FakeClient

from yadokari.db.repository import decide
from yadokari.report import weekly
from yadokari.scoring.client import ScoringClient
from yadokari.scoring.prompt import SYSTEM_PROMPT
from yadokari.validation import auc, import_list, read_list, report, score_all


def _page(title):
    return f"<html><head><meta property='og:title' content='{title}'></head><body><article>{BODY}<img src='/1.jpg'></article></body></html>"


def test_read_list_skips_comments(tmp_path):
    f = tmp_path / "p.tsv"
    f.write_text("# c\nhttps://a.com/1\tメモ\n\nhttps://a.com/2\n", encoding="utf-8")
    assert read_list(f) == [("https://a.com/1", "メモ"), ("https://a.com/2", "")]


def test_import_score_report(config, db, tmp_path):
    pos = tmp_path / "p.tsv"
    pos.write_text("https://a.com/1\nhttps://a.com/2\nhttps://a.com/gone\n", encoding="utf-8")
    neg = tmp_path / "n.tsv"
    neg.write_text("https://b.com/1\n", encoding="utf-8")
    client = FakeClient({"https://a.com/1": _page("A"), "https://a.com/2": _page("B"),
                         "https://b.com/1": _page("Mansion")}, fail={"https://a.com/gone"})
    st = import_list(config, db, pos, "pos", client=client)
    assert (st.added, len(st.failed)) == (2, 1)
    assert import_list(config, db, pos, "pos", client=client).existing == 3  # 二度取らない
    import_list(config, db, neg, "neg", client=client)

    config.scoring.fallbacks = False
    llm = FakeLLM(assessment(), assessment(design_score=90),
                  assessment(relevant=False, relevant_reason="大きな邸宅", kind="other_building"))
    ok, ng = score_all(config, db, client=ScoringClient(config, SYSTEM_PROMPT, client=llm))
    assert (ok, ng) == (3, 0)
    text = report(config, db)
    assert "AUC: 1.00" in text.replace("見分けの精度（AUC）: 1.00", "AUC: 1.00")
    assert "取得失敗 1" in text


def test_auc():
    assert auc([80, 70], [10]) == 1.0
    assert auc([50], [50]) == 0.5
    assert auc([], [1]) is None


def test_weekly_report_counts(config, db):
    a = add_article(db, url="https://t.com/a")
    b = add_article(db, url="https://t.com/b")
    decide(db, a, "approved")
    decide(db, b, "rejected", reason="小さくない", tag="題材が合わない（小さくない・住まいでない）")
    db.commit()
    text = weekly(db, now=datetime.now(UTC))
    assert "承認 1 / 非承認 1（承認率 50%）" in text
    assert "題材が合わない" in text
    assert "承認済みで下書き未作成 1 件" in text
