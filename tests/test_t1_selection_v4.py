import pandas as pd
import pytest
import fast_t1_scan as scan


def _api(name):
    assert hasattr(scan, name), f"missing v4 selection behavior: {name}"
    return getattr(scan, name)


def test_market_context_includes_low_price_and_restricted_board_before_selection():
    frame = pd.DataFrame([
        dict(code="600001", price=80, change_pct=2, industry="甲"),
        dict(code="600002", price=8, change_pct=-3, industry="甲"),
        dict(code="300001", price=20, change_pct=-4, industry="乙"),
        dict(code="920001", price=10, change_pct=-2, industry="乙"),
    ])
    context, industries = _api("full_market_context")(frame)
    assert context["advance_ratio"] == .25
    assert context["median"] == -2.5
    assert industries.loc["甲", "change_pct"] == -.5
    assert industries.loc["甲", "advance_ratio"] == .5


def test_duplicate_market_codes_are_not_silently_counted():
    frame = pd.DataFrame([dict(code="600001", price=80, change_pct=2, industry="甲")] * 2)
    with pytest.raises(ValueError, match="duplicate"):
        _api("full_market_context")(frame)


def test_recovery_and_momentum_survive_without_ma5_ma10_ma20_alignment():
    base = dict(price=60, ma5=57, ma10=58, ma20=59, ma60=58,
                change_pct=2, return_20d=.05, momentum_delta_1=.01,
                volume_ratio=2, rps20=80, rsi14=60, repeated_upper_shadow=False)
    channels = _api("research_channels")(pd.Series(base))
    assert "trend_recovery" in channels
    assert "stable_pullback" not in channels
    assert "high_momentum" in _api("research_channels")(pd.Series({**base, "change_pct": 5}))


def test_broken_structure_does_not_get_a_recovery_label():
    row = pd.Series(dict(price=50, ma5=57, ma10=58, ma20=59, ma60=60,
                         change_pct=-3, return_20d=-.2, momentum_delta_1=-.1,
                         volume_ratio=2, rps20=20, rsi14=30, repeated_upper_shadow=False))
    assert _api("research_channels")(row) == []


def test_relative_industry_strength_is_observation_evidence_not_execution_confirmation():
    industries = pd.DataFrame({"change_pct": [-.5, -2], "advance_ratio": [.5, .2]}, index=["甲", "乙"])
    select = _api("industry_research_eligible")
    assert select("甲", industries, -1.5)
    assert not select("乙", industries, -1.5)
    assert not select("未知", industries, -1.5)


def test_quick_report_does_not_issue_unverified_entry_instructions():
    ctx = dict(top5=[], official=[], watch=[], ind_top=[], ind_bottom=[],
               byd=dict(triggered=False, chg=0, price=50, text="预览"),
               date="2026-09-04", phase="盘中", generated_at="13:45", snap_source="fixture", snap_obs="13:45",
               total_sec=1, t_snap=0, t_ind=1, advance_ratio=.5, median=0, mode="正常", funnel="4", n_official=0, n_watch=0)
    html = scan.render_html(ctx)
    assert "快速介入" not in html
    assert "仅研究" in html
    assert "正式候选（" not in html
