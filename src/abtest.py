"""Reusable pieces of a two-arm experiment analysis.

Everything here is deliberately small and checkable. The functions are
verified against scipy/statsmodels in tests/test_abtest.py, because a helper
you wrote yourself is only trustworthy if something independent agrees with it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats
from statsmodels.stats.power import NormalIndPower
from statsmodels.stats.proportion import proportion_effectsize

Z_95 = stats.norm.ppf(0.975)


# --------------------------------------------------------------------------
# 1. Validity: did the randomisation do what we asked it to?
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SRMResult:
    counts: dict[str, int]
    observed_share: dict[str, float]
    expected_share: dict[str, float]
    chi2: float
    p_value: float

    def verdict(self, alpha: float = 0.001) -> str:
        return "FAIL - investigate" if self.p_value < alpha else "pass"

    def __str__(self) -> str:
        obs = ", ".join(f"{k}={v:,}" for k, v in self.counts.items())
        return (
            f"SRM: {obs} | observed split "
            + "/".join(f"{s:.2%}" for s in self.observed_share.values())
            + f" | chi2={self.chi2:.3f}, p={self.p_value:.5f}"
        )


def srm_check(counts: dict[str, int], expected_ratio: dict[str, float] | None = None) -> SRMResult:
    """Chi-square goodness-of-fit test on arm sizes.

    Run this before looking at any outcome. If users did not arrive in the
    proportions you asked for, the randomisation is suspect and every
    downstream number inherits that suspicion.
    """
    arms = list(counts)
    observed = np.array([counts[a] for a in arms], dtype=float)
    total = observed.sum()

    if expected_ratio is None:
        ratio = np.full(len(arms), 1 / len(arms))
    else:
        ratio = np.array([expected_ratio[a] for a in arms], dtype=float)
        ratio = ratio / ratio.sum()

    expected = ratio * total
    chi2, p_value = stats.chisquare(observed, expected)

    return SRMResult(
        counts={a: int(counts[a]) for a in arms},
        observed_share=dict(zip(arms, observed / total)),
        expected_share=dict(zip(arms, ratio)),
        chi2=float(chi2),
        p_value=float(p_value),
    )


# --------------------------------------------------------------------------
# 2. Design: how small an effect can this sample actually see?
# --------------------------------------------------------------------------


def required_n_per_arm(
    baseline: float, mde_relative: float, power: float = 0.80, alpha: float = 0.05
) -> float:
    """Users needed per arm to detect a relative change of `mde_relative`.

    `mde_relative` is signed, matching the treatment-minus-control convention
    used everywhere else here: -0.03 means "a 3% relative drop". The sign
    matters. The effect size behind this calculation is Cohen's h, which is
    not symmetric around a baseline - detecting a 3% fall and detecting a 3%
    rise need slightly different sample sizes.
    """
    effect = proportion_effectsize(baseline * (1 + mde_relative), baseline)
    return float(
        NormalIndPower().solve_power(
            effect_size=abs(effect), power=power, alpha=alpha, ratio=1, alternative="two-sided"
        )
    )


def mde_at_n(
    baseline: float,
    n_per_arm: int,
    power: float = 0.80,
    alpha: float = 0.05,
    direction: str = "decrease",
) -> tuple[float, float]:
    """Inverse of the above: the smallest effect `n_per_arm` can detect.

    Returns (absolute change, relative change), signed by `direction`. The
    default is "decrease" because the question that usually matters is how
    much harm an experiment could miss.
    """
    if direction not in {"decrease", "increase"}:
        raise ValueError("direction must be 'decrease' or 'increase'")

    effect = NormalIndPower().solve_power(
        effect_size=None, nobs1=n_per_arm, power=power, alpha=alpha, ratio=1, alternative="two-sided"
    )
    # Undo Cohen's h to get back to a proportion on the original scale.
    phi_base = 2 * np.arcsin(np.sqrt(baseline))
    sign = -1 if direction == "decrease" else 1
    detectable = np.sin((phi_base + sign * effect) / 2) ** 2
    absolute = detectable - baseline
    return float(absolute), float(absolute / baseline)


# --------------------------------------------------------------------------
# 3. Inference: the effect, its uncertainty, and only then the p-value
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ProportionTest:
    metric: str
    control_rate: float
    treatment_rate: float
    absolute_diff: float
    relative_diff: float
    ci_low: float
    ci_high: float
    rel_ci_low: float
    rel_ci_high: float
    z_stat: float
    p_value: float
    n_control: int
    n_treatment: int

    @property
    def significant(self) -> bool:
        return self.ci_low > 0 or self.ci_high < 0

    def summary(self) -> str:
        return (
            f"{self.metric}: {self.control_rate:.2%} -> {self.treatment_rate:.2%} | "
            f"{self.absolute_diff * 100:+.3f}pp "
            f"(95% CI [{self.ci_low * 100:+.3f}, {self.ci_high * 100:+.3f}]pp) | "
            f"{self.relative_diff:+.2%} relative | p={self.p_value:.5f}"
        )


def two_proportion_test(
    control_successes: int,
    control_n: int,
    treatment_successes: int,
    treatment_n: int,
    metric: str = "metric",
    alpha: float = 0.05,
) -> ProportionTest:
    """Two-proportion z-test, treatment minus control.

    The confidence interval uses unpooled standard errors (it describes the
    difference we estimate); the p-value uses the pooled standard error
    (it tests the null that both arms share one rate). Mixing these up is a
    common and invisible error - the two answers can disagree at the margin.
    """
    p_c = control_successes / control_n
    p_t = treatment_successes / treatment_n
    diff = p_t - p_c

    se_unpooled = np.sqrt(p_c * (1 - p_c) / control_n + p_t * (1 - p_t) / treatment_n)
    z_crit = stats.norm.ppf(1 - alpha / 2)
    ci_low, ci_high = diff - z_crit * se_unpooled, diff + z_crit * se_unpooled

    p_pool = (control_successes + treatment_successes) / (control_n + treatment_n)
    se_pooled = np.sqrt(p_pool * (1 - p_pool) * (1 / control_n + 1 / treatment_n))
    z_stat = diff / se_pooled
    p_value = 2 * stats.norm.sf(abs(z_stat))

    return ProportionTest(
        metric=metric,
        control_rate=float(p_c),
        treatment_rate=float(p_t),
        absolute_diff=float(diff),
        relative_diff=float(diff / p_c),
        ci_low=float(ci_low),
        ci_high=float(ci_high),
        rel_ci_low=float(ci_low / p_c),
        rel_ci_high=float(ci_high / p_c),
        z_stat=float(z_stat),
        p_value=float(p_value),
        n_control=int(control_n),
        n_treatment=int(treatment_n),
    )


def bootstrap_diff(
    control: np.ndarray,
    treatment: np.ndarray,
    n_resamples: int = 10_000,
    statistic=np.mean,
    alpha: float = 0.05,
    seed: int = 42,
) -> tuple[np.ndarray, float, float]:
    """Resample both arms with replacement and recompute the difference.

    Returns (distribution, ci_low, ci_high). This makes no normal
    approximation, which is the point: if it agrees with the analytic
    interval, the analytic interval has earned its keep.
    """
    rng = np.random.default_rng(seed)
    control = np.asarray(control)
    treatment = np.asarray(treatment)

    idx_c = rng.integers(0, len(control), size=(n_resamples, len(control)))
    idx_t = rng.integers(0, len(treatment), size=(n_resamples, len(treatment)))
    dist = statistic(treatment[idx_t], axis=1) - statistic(control[idx_c], axis=1)

    ci_low, ci_high = np.percentile(dist, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return dist, float(ci_low), float(ci_high)


# --------------------------------------------------------------------------
# 4. Variance reduction: CUPED
# --------------------------------------------------------------------------


def cuped_adjust(
    y: np.ndarray, x_pre: np.ndarray, theta: float | None = None
) -> tuple[np.ndarray, float]:
    """CUPED: subtract the part of the outcome the pre-period already explained.

    Y_adj = Y - theta * (X - mean(X)), with theta = cov(Y, X) / var(X).

    `x_pre` MUST be measured before assignment. A covariate measured during
    the experiment can itself be moved by the treatment, and adjusting for it
    conditions on a post-treatment variable - which biases the estimate rather
    than sharpening it. Theta is estimated on the pooled sample so it does not
    depend on the arm, and the adjustment leaves the difference in means
    unbiased while removing variance explained by X.
    """
    y = np.asarray(y, dtype=float)
    x_pre = np.asarray(x_pre, dtype=float)

    if theta is None:
        theta = float(np.cov(y, x_pre, ddof=1)[0, 1] / np.var(x_pre, ddof=1))

    return y - theta * (x_pre - x_pre.mean()), theta


def welch_diff_ci(
    control: np.ndarray, treatment: np.ndarray, alpha: float = 0.05
) -> tuple[float, float, float, float]:
    """Difference in means with a Welch interval and p-value.

    Returns (diff, ci_low, ci_high, p_value).
    """
    control = np.asarray(control, dtype=float)
    treatment = np.asarray(treatment, dtype=float)
    diff = treatment.mean() - control.mean()

    se = np.sqrt(control.var(ddof=1) / len(control) + treatment.var(ddof=1) / len(treatment))
    result = stats.ttest_ind(treatment, control, equal_var=False)
    df = len(control) + len(treatment) - 2
    t_crit = stats.t.ppf(1 - alpha / 2, df)

    return float(diff), float(diff - t_crit * se), float(diff + t_crit * se), float(result.pvalue)
