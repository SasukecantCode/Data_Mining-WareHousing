"""Line-type revenue rules (billing_notes.md), verified against curated data."""
import pytest

from app.analytics.revenue import compute_revenue
import pandas as pd


def test_tax_and_tender_are_forced_to_zero():
    df = pd.DataFrame({
        "qty": [1, 1, 3],
        "source_unit_price": [198.01, 4158.13, 100.0],
        "line_type": ["TAX", "TENDER", "SALE"],
    })
    out = compute_revenue(df)
    assert out.iloc[0] == 0.0
    assert out.iloc[1] == 0.0
    assert out.iloc[2] == 300.0


def test_void_cancels_matching_sale():
    df = pd.DataFrame({
        "qty": [3, -3],
        "source_unit_price": [320.99, 320.99],
        "line_type": ["SALE", "VOID"],
    })
    out = compute_revenue(df)
    assert out.sum() == 0.0


def test_return_and_discount_subtract():
    df = pd.DataFrame({
        "qty": [-2, 1],
        "source_unit_price": [47.10, -21.84],
        "line_type": ["RETURN", "DISCOUNT"],
    })
    out = compute_revenue(df)
    assert out.iloc[0] == -94.20
    assert out.iloc[1] == -21.84


@pytest.mark.integration
def test_real_cancelled_bill_nets_to_zero(duck_con):
    """S01/20240102/00017: 4 SALE lines mirrored by 4 VOID lines, TAX=0,
    TENDER=0. See reports/inspection_report.md section 5."""
    total = duck_con.execute(
        "SELECT SUM(revenue_amount) FROM fact_sales WHERE bill_no = 'S01/20240102/00017'"
    ).fetchone()[0]
    assert total is not None
    assert abs(float(total)) < 1e-6


@pytest.mark.integration
def test_no_tax_or_tender_leaks_into_total_revenue(duck_con):
    tax_and_tender = duck_con.execute(
        "SELECT COALESCE(SUM(revenue_amount),0) FROM fact_sales WHERE line_type IN ('TAX','TENDER')"
    ).fetchone()[0]
    assert float(tax_and_tender) == 0.0


@pytest.mark.integration
def test_october_total_reconciles_to_supplied_truth(duck_con):
    total = duck_con.execute(
        "SELECT ROUND(SUM(revenue_amount),2) FROM fact_sales "
        "WHERE business_date >= DATE '2024-10-01' AND business_date < DATE '2024-11-01'"
    ).fetchone()[0]
    assert abs(float(total) - 56359195.92) < 0.01
