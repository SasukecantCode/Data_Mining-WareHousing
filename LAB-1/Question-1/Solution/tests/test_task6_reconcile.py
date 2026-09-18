"""Task 6: reconciliation results, checked against the saved evidence
(requires scripts/15_task6_reconcile.py to have already run)."""
import csv
from pathlib import Path

import pytest

REPORT_CSV = Path(__file__).resolve().parent.parent / "reports" / "task6" / "reconciliation_table.csv"


@pytest.mark.integration
def test_reconciliation_report_exists_and_covers_all_12_months():
    assert REPORT_CSV.exists(), "run scripts/15_task6_reconcile.py first"
    with open(REPORT_CSV) as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 12
    assert {r["month"] for r in rows} == {f"2024-{m:02d}" for m in range(1, 13)}


@pytest.mark.integration
def test_nine_months_match_exactly():
    with open(REPORT_CSV) as f:
        rows = list(csv.DictReader(f))
    matches = [r for r in rows if r["difference_type"] == "Match"]
    assert len(matches) == 9
    for r in matches:
        assert abs(float(r["difference"])) < 0.01


@pytest.mark.integration
def test_three_differences_are_all_classified_not_bugs():
    with open(REPORT_CSV) as f:
        rows = list(csv.DictReader(f))
    differing = [r for r in rows if r["difference_type"] != "Match"]
    assert len(differing) == 3
    assert {r["month"] for r in differing} == {"2024-03", "2024-07", "2024-12"}
    for r in differing:
        assert r["difference_type"] in ("Source data issue", "Revenue definition difference", "Pipeline bug")
        assert r["difference_type"] != "Pipeline bug"  # none found in this dataset


@pytest.mark.integration
def test_yearly_difference_has_zero_unexplained_residual():
    with open(REPORT_CSV) as f:
        rows = list(csv.DictReader(f))
    total_diff = sum(float(r["finance_revenue"]) - float(r["platform_revenue"]) for r in rows)
    explained = sum(float(r["difference"]) for r in rows if r["difference_type"] != "Match")
    assert abs(total_diff - explained) < 0.01


@pytest.mark.integration
def test_march_evidence_matches_truth_bulk_invoice_field(duck_con):
    import json
    from app.config import SETTINGS

    d = SETTINGS.source_data_dir
    if not d.is_absolute():
        d = (Path(__file__).resolve().parent.parent / d).resolve()
    truth = json.loads((d / "_truth" / "truth.json").read_text())

    with open(REPORT_CSV) as f:
        rows = {r["month"]: r for r in csv.DictReader(f)}
    march = rows["2024-03"]
    assert abs(float(march["difference"]) - truth["march_bulk_invoice"]) < 0.01
