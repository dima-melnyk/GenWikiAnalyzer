"""
trend_stats.py — turns a raw Wikimedia pageviews time series into a
trustworthy trend summary.

Kept separate from analyze.py (and free of any network calls) so it can
be unit-tested against synthetic data — see tests/test_trend_stats.py.

Methodology, and why:
  - Growth is estimated on log(views), so the resulting slope is directly
    interpretable as a percentage growth rate rather than an absolute
    views-per-month number that means nothing without context.
  - The slope estimator is Theil-Sen (median of pairwise slopes), not
    ordinary least squares, because pageview series are routinely blown
    out by single-week news spikes (a celebrity dies, a topic trends on
    TikTok) that would otherwise dominate an OLS fit.
  - Statistical significance of the trend direction uses Kendall's tau
    against the time index, which is the same test statistic as the
    (nonparametric, no-normality-assumed) Mann-Kendall trend test — a
    much safer default than a t-test for noisy, non-Gaussian count data.
  - "Confidence" is a plain-language High/Medium/Low label derived from:
      * how many data points feed the estimate (longer series = safer),
      * the trend p-value,
      * how much of the series is a small number of spike months (a
        trend driven mostly by one viral week is not the same claim as
        a steady multi-year climb, even if both produce a positive slope).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy import stats


@dataclass
class TrendResult:
    n_points: int
    start: Optional[str]
    end: Optional[str]
    total_views: int
    mean_monthly_views: float
    total_change_pct: Optional[float]          # views at end of period vs start of period
    annualized_growth_pct: Optional[float]      # from the robust log-slope
    trend_direction: str                        # "up" / "down" / "flat" / "insufficient_data"
    kendall_tau: Optional[float]
    p_value: Optional[float]
    spike_months: list                           # timestamps flagged as outliers
    spike_share_of_total_views: float             # 0..1, how much of total views sits in spike months
    confidence: str                               # "high" / "medium" / "low"
    confidence_reasons: list = field(default_factory=list)
    caveats: list = field(default_factory=list)


def _parse_series(items: list[dict]) -> tuple[list[str], np.ndarray]:
    """items: [{"timestamp": "2024010100", "views": 123}, ...] (Wikimedia's
    format, YYYYMMDDHH) sorted ascending. Returns (timestamps, values)."""
    items_sorted = sorted(items, key=lambda x: x["timestamp"])
    ts = [it["timestamp"] for it in items_sorted]
    vals = np.array([float(it["views"]) for it in items_sorted], dtype=float)
    return ts, vals


def detect_spikes(values: np.ndarray, threshold_mad: float = 4.0) -> np.ndarray:
    """Flag points far from the local trend using a robust z-score
    (median absolute deviation on first differences of log-views), so a
    steady climb isn't itself flagged as a spike — only sudden jumps are.
    Returns a boolean mask, True = spike."""
    if len(values) < 4:
        return np.zeros(len(values), dtype=bool)
    safe = np.clip(values, 1, None)
    log_v = np.log(safe)
    diffs = np.diff(log_v, prepend=log_v[0])
    med = np.median(diffs)
    mad = np.median(np.abs(diffs - med)) or 1e-9
    robust_z = 0.6745 * (diffs - med) / mad
    return np.abs(robust_z) > threshold_mad


def analyze_series(items: list[dict], min_points_for_trend: int = 6) -> TrendResult:
    ts, vals = _parse_series(items)
    n = len(vals)

    if n == 0:
        return TrendResult(
            n_points=0, start=None, end=None, total_views=0, mean_monthly_views=0.0,
            total_change_pct=None, annualized_growth_pct=None, trend_direction="insufficient_data",
            kendall_tau=None, p_value=None, spike_months=[], spike_share_of_total_views=0.0,
            confidence="low", confidence_reasons=["No data returned for this period/article."],
            caveats=["No pageview data available — the article may not exist in this language, "
                     "or the date range predates Wikimedia's pageview records (from 2015-07)."],
        )

    total_views = int(vals.sum())
    mean_monthly = float(vals.mean())
    spike_mask = detect_spikes(vals)
    spike_months = [ts[i] for i in range(n) if spike_mask[i]]
    spike_views = float(vals[spike_mask].sum()) if spike_mask.any() else 0.0
    spike_share = (spike_views / total_views) if total_views > 0 else 0.0

    total_change_pct = None
    if n >= 2 and vals[0] > 0:
        total_change_pct = float((vals[-1] - vals[0]) / vals[0] * 100.0)
    elif n >= 2:
        total_change_pct = float("inf") if vals[-1] > 0 else 0.0

    if n < min_points_for_trend:
        return TrendResult(
            n_points=n, start=ts[0], end=ts[-1], total_views=total_views,
            mean_monthly_views=mean_monthly, total_change_pct=total_change_pct,
            annualized_growth_pct=None, trend_direction="insufficient_data",
            kendall_tau=None, p_value=None, spike_months=spike_months,
            spike_share_of_total_views=spike_share, confidence="low",
            confidence_reasons=[f"Only {n} data points — need at least {min_points_for_trend} "
                                 f"to estimate a trend with any reliability."],
            caveats=["Widen the date range for a meaningful trend estimate."],
        )

    x = np.arange(n, dtype=float)
    safe_vals = np.clip(vals, 1, None)  # log(0) guard; 1 view/month floor is negligible
    log_vals = np.log(safe_vals)

    # Robust slope in log-space via Theil-Sen (median of pairwise slopes)
    slope, intercept, _, _ = stats.theilslopes(log_vals, x)
    monthly_growth_rate = math.exp(slope) - 1.0
    annualized_growth_pct = ((1.0 + monthly_growth_rate) ** 12 - 1.0) * 100.0

    # Significance of the monotonic trend (Mann-Kendall == Kendall's tau
    # between the time index and the values)
    tau, p_value = stats.kendalltau(x, vals)

    if p_value is not None and p_value < 0.05 and tau > 0:
        direction = "up"
    elif p_value is not None and p_value < 0.05 and tau < 0:
        direction = "down"
    else:
        direction = "flat"

    # --- confidence rating -------------------------------------------------
    reasons = []
    score = 0
    if n >= 24:
        score += 2
        reasons.append(f"Long series ({n} data points) makes seasonal noise less likely to fool the trend.")
    elif n >= 12:
        score += 1
        reasons.append(f"Series covers {n} points — enough for one seasonal cycle, but a second year would help confirm it.")
    else:
        reasons.append(f"Only {n} data points and no full seasonal cycle — treat the direction as tentative.")

    if p_value is not None:
        if p_value < 0.01:
            score += 2
            reasons.append(f"Trend is statistically strong (p={p_value:.3f}).")
        elif p_value < 0.05:
            score += 1
            reasons.append(f"Trend is statistically significant but not strongly so (p={p_value:.3f}).")
        else:
            reasons.append(f"Trend is not statistically distinguishable from noise (p={p_value:.3f}) — "
                            f"'{direction}' is a weak signal.")

    if spike_share > 0.35:
        score -= 2
        reasons.append(f"~{spike_share*100:.0f}% of total views sit in {len(spike_months)} outlier "
                        f"month(s) — the pattern may reflect one news event, not sustained interest.")
    elif spike_share > 0.15:
        score -= 1
        reasons.append(f"~{spike_share*100:.0f}% of total views sit in outlier months — worth a sanity check "
                        f"against recent news for this topic.")

    if score >= 4:
        confidence = "high"
    elif score >= 2:
        confidence = "medium"
    else:
        confidence = "low"

    caveats = [
        "Pageviews measure reading interest, not willingness to pay — treat this as a prioritization "
        "signal, not a demand forecast.",
        "Bot/crawler traffic is excluded (agent=user), but spikes from news coverage, social-media "
        "virality, or Wikipedia being linked from a popular site are not distinguished from organic "
        "growth in the topic's audience.",
    ]
    if spike_months:
        caveats.append(f"Outlier months flagged: {', '.join(spike_months[:6])}"
                        + (" (+more)" if len(spike_months) > 6 else "") + ".")

    return TrendResult(
        n_points=n, start=ts[0], end=ts[-1], total_views=total_views,
        mean_monthly_views=mean_monthly, total_change_pct=total_change_pct,
        annualized_growth_pct=annualized_growth_pct, trend_direction=direction,
        kendall_tau=float(tau) if tau is not None else None,
        p_value=float(p_value) if p_value is not None else None,
        spike_months=spike_months, spike_share_of_total_views=spike_share,
        confidence=confidence, confidence_reasons=reasons, caveats=caveats,
    )


def to_dict(result: TrendResult) -> dict:
    return {
        "n_points": result.n_points,
        "start": result.start,
        "end": result.end,
        "total_views": result.total_views,
        "mean_monthly_views": round(result.mean_monthly_views, 1),
        "total_change_pct": None if result.total_change_pct is None else round(result.total_change_pct, 1),
        "annualized_growth_pct": None if result.annualized_growth_pct is None else round(result.annualized_growth_pct, 1),
        "trend_direction": result.trend_direction,
        "kendall_tau": None if result.kendall_tau is None else round(result.kendall_tau, 3),
        "p_value": None if result.p_value is None else round(result.p_value, 4),
        "spike_months": result.spike_months,
        "spike_share_of_total_views_pct": round(result.spike_share_of_total_views * 100, 1),
        "confidence": result.confidence,
        "confidence_reasons": result.confidence_reasons,
        "caveats": result.caveats,
    }
