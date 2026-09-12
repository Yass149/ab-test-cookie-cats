"""Independent checks on src/abtest.py.

A helper you wrote yourself proves nothing. Each test here either compares
against an established library (statsmodels, scipy) or checks a property that
must hold by construction - unbiasedness, calibration, coverage.
"""

import sys
from pathlib import Path

import numpy as np
import pytest
from scipy import stats
from statsmodels.stats.power import NormalIndPower
from statsmodels.stats.proportion import proportion_effectsize, proportions_ztest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from abtest import (  # noqa: E402
    bootstrap_diff,
    cuped_adjust,
    mde_at_n,
    required_n_per_arm,
    srm_check,
    two_proportion_test,
    welch_diff_ci,
)


# --------------------------------------------------------------------------
# SRM
# --------------------------------------------------------------------------


def test_srm_matches_scipy_chisquare():
    counts = {"a": 44700, "b": 45489}
    result = srm_check(counts)
    expected = np.array([sum(counts.values()) / 2] * 2)
    chi2, p = stats.chisquare(np.array([44700, 45489], dtype=float), expected)
    assert result.chi2 == pytest.approx(chi2)
    assert result.p_value == pytest.approx(p)


def test_srm_flags_a_broken_split():
    """A 60/40 landing on a 50/50 design must be caught."""
    result = srm_check({"control": 60000, "treatment": 40000})
    assert result.p_value < 1e-10
    assert result.verdict() == "FAIL - investigate"


def test_srm_passes_a_clean_split():
    result = srm_check({"control": 50010, "treatment": 49990})
    assert result.p_value > 0.05
    assert result.verdict() == "pass"


def test_srm_honours_an_uneven_intended_ratio():
    """A deliberate 90/10 holdout is not a mismatch."""
    result = srm_check({"control": 90000, "treatment": 10000}, {"control": 0.9, "treatment": 0.1})
    assert result.p_value > 0.05


# --------------------------------------------------------------------------
# Power
# --------------------------------------------------------------------------


def test_required_n_matches_statsmodels():
    baseline, mde = 0.19, 0.03
    effect = proportion_effectsize(baseline * (1 + mde), baseline)
    expected = NormalIndPower().solve_power(
        effect_size=effect, power=0.8, alpha=0.05, ratio=1, alternative="two-sided"
    )
    assert required_n_per_arm(baseline, mde) == pytest.approx(expected)


@pytest.mark.parametrize("direction", ["decrease", "increase"])
def test_mde_and_required_n_are_inverses(direction):
    """Feed the MDE back in and you should get the sample size you started with."""
    baseline, n = 0.19, 44700
    _, mde_rel = mde_at_n(baseline, n, direction=direction)
    assert required_n_per_arm(baseline, mde_rel) == pytest.approx(n, rel=0.01)


def test_mde_is_signed_by_direction():
    down, _ = mde_at_n(0.19, 44700, direction="decrease")
    up, _ = mde_at_n(0.19, 44700, direction="increase")
    assert down < 0 < up


def test_cohens_h_is_not_symmetric():
    """Detecting a 3% fall and a 3% rise are not the same problem.

    Worth a test because it is the kind of asymmetry that silently makes a
    power calculation optimistic if you quote the wrong direction.
    """
    baseline = 0.19
    n_down = required_n_per_arm(baseline, -0.03)
    n_up = required_n_per_arm(baseline, 0.03)
    assert n_down != pytest.approx(n_up, rel=1e-3)
    # From a baseline of 0.19, the rise is the harder one to see: it moves the
    # rate towards 0.5, where p(1-p) - and so the variance - is larger.
    assert n_up > n_down


def test_smaller_effects_need_more_users():
    baseline = 0.19
    assert required_n_per_arm(baseline, 0.01) > required_n_per_arm(baseline, 0.05)


# --------------------------------------------------------------------------
# Two-proportion test
# --------------------------------------------------------------------------


def test_two_proportion_pvalue_matches_statsmodels():
    result = two_proportion_test(8502, 44700, 8279, 45489, metric="retention_7")
    z_sm, p_sm = proportions_ztest([8279, 8502], [45489, 44700])
    assert result.z_stat == pytest.approx(z_sm)
    assert result.p_value == pytest.approx(p_sm)


def test_ci_is_centred_on_the_point_estimate():
    result = two_proportion_test(8502, 44700, 8279, 45489)
    assert (result.ci_low + result.ci_high) / 2 == pytest.approx(result.absolute_diff)


def test_sign_convention_is_treatment_minus_control():
    """Treatment worse than control must produce a negative difference."""
    result = two_proportion_test(5000, 10000, 4000, 10000)
    assert result.absolute_diff == pytest.approx(-0.10)
    assert result.relative_diff == pytest.approx(-0.20)


def test_no_difference_gives_a_ci_straddling_zero():
    result = two_proportion_test(5000, 10000, 5000, 10000)
    assert result.absolute_diff == pytest.approx(0.0)
    assert result.ci_low < 0 < result.ci_high
    assert not result.significant


def test_false_positive_rate_is_calibrated_under_the_null():
    """1,000 A/A tests at alpha=0.05 should reject about 5% of the time.

    This is the property that makes a p-value mean anything at all.
    """
    rng = np.random.default_rng(0)
    n, rate = 5000, 0.2
    rejections = 0
    for _ in range(1000):
        c = rng.binomial(n, rate)
        t = rng.binomial(n, rate)
        if two_proportion_test(c, n, t, n).p_value < 0.05:
            rejections += 1
    assert 0.03 < rejections / 1000 < 0.07


# --------------------------------------------------------------------------
# Bootstrap
# --------------------------------------------------------------------------


def test_bootstrap_agrees_with_the_analytic_interval():
    rng = np.random.default_rng(1)
    control = rng.binomial(1, 0.20, 20000)
    treatment = rng.binomial(1, 0.18, 20000)

    analytic = two_proportion_test(control.sum(), len(control), treatment.sum(), len(treatment))
    _, boot_low, boot_high = bootstrap_diff(control, treatment, n_resamples=2000)

    assert boot_low == pytest.approx(analytic.ci_low, abs=0.004)
    assert boot_high == pytest.approx(analytic.ci_high, abs=0.004)


def test_bootstrap_is_reproducible():
    rng = np.random.default_rng(2)
    c, t = rng.binomial(1, 0.2, 1000), rng.binomial(1, 0.2, 1000)
    first = bootstrap_diff(c, t, n_resamples=500, seed=7)[1:]
    second = bootstrap_diff(c, t, n_resamples=500, seed=7)[1:]
    assert first == second


def test_bootstrap_distribution_centres_on_the_observed_difference():
    rng = np.random.default_rng(3)
    control = rng.binomial(1, 0.30, 10000)
    treatment = rng.binomial(1, 0.25, 10000)
    dist, _, _ = bootstrap_diff(control, treatment, n_resamples=2000)
    assert dist.mean() == pytest.approx(treatment.mean() - control.mean(), abs=0.003)


# --------------------------------------------------------------------------
# CUPED
# --------------------------------------------------------------------------


def _simulate(rng, n=20000, rho=0.6, effect=0.0):
    """Pre-period covariate x, outcome y correlated with it, known effect."""
    x = rng.normal(10, 3, n)
    noise = rng.normal(0, 3 * np.sqrt(1 / rho**2 - 1), n)
    y = x + noise
    assign = rng.random(n) < 0.5
    y = y + assign * effect
    return x, y, assign


def test_cuped_reduces_variance_by_roughly_rho_squared():
    rng = np.random.default_rng(11)
    x, y, _ = _simulate(rng, rho=0.6)
    adjusted, _ = cuped_adjust(y, x)
    reduction = 1 - adjusted.var(ddof=1) / y.var(ddof=1)
    rho = np.corrcoef(y, x)[0, 1]
    assert reduction == pytest.approx(rho**2, abs=0.02)


def test_cuped_theta_matches_the_ols_slope():
    rng = np.random.default_rng(12)
    x, y, _ = _simulate(rng)
    _, theta = cuped_adjust(y, x)
    slope = np.polyfit(x, y, 1)[0]
    assert theta == pytest.approx(slope, rel=1e-6)


def test_cuped_preserves_the_mean():
    rng = np.random.default_rng(13)
    x, y, _ = _simulate(rng)
    adjusted, _ = cuped_adjust(y, x)
    assert adjusted.mean() == pytest.approx(y.mean())


def test_cuped_recovers_a_known_effect_without_bias():
    """Across many simulations the average CUPED estimate must equal the truth."""
    truth = 0.5
    estimates_plain, estimates_cuped = [], []
    for seed in range(200):
        rng = np.random.default_rng(1000 + seed)
        x, y, assign = _simulate(rng, n=4000, effect=truth)
        estimates_plain.append(y[assign].mean() - y[~assign].mean())
        adjusted, _ = cuped_adjust(y, x)
        estimates_cuped.append(adjusted[assign].mean() - adjusted[~assign].mean())

    assert np.mean(estimates_cuped) == pytest.approx(truth, abs=0.05)
    assert np.mean(estimates_plain) == pytest.approx(truth, abs=0.05)
    # ...and the whole point: the CUPED estimator is tighter.
    assert np.std(estimates_cuped) < np.std(estimates_plain)


def test_cuped_stays_calibrated_under_the_null():
    """Variance reduction must not buy false positives."""
    rejections = 0
    runs = 300
    for seed in range(runs):
        rng = np.random.default_rng(5000 + seed)
        x, y, assign = _simulate(rng, n=3000, effect=0.0)
        adjusted, _ = cuped_adjust(y, x)
        _, _, _, p = welch_diff_ci(adjusted[~assign], adjusted[assign])
        rejections += p < 0.05
    assert 0.02 < rejections / runs < 0.09


def test_welch_ci_matches_scipy():
    rng = np.random.default_rng(14)
    c, t = rng.normal(0, 1, 500), rng.normal(0.2, 1, 500)
    _, _, _, p = welch_diff_ci(c, t)
    assert p == pytest.approx(stats.ttest_ind(t, c, equal_var=False).pvalue)
