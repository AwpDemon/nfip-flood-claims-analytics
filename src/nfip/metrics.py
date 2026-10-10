"""Model-evaluation metrics used in insurance pricing (pure numpy, unit-tested)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def gini(actual: np.ndarray, pred: np.ndarray) -> float:
    """Raw Gini of ``actual`` when claims are ordered by ``pred`` (descending).

    Equivalent to 2 x the area between the "cumulative share of actual loss" curve and the
    diagonal. Tied predictions are handled by giving every member of a tie group the group's
    mean actual, so the result does not depend on the arbitrary order of tied rows (this
    matters for coarse models such as a zone-mean baseline with ~10 distinct predictions).
    """
    actual = np.asarray(actual, dtype=float)
    pred = np.asarray(pred, dtype=float)
    if actual.shape != pred.shape:
        raise ValueError("actual and pred must have the same shape")
    n = actual.size
    order = np.argsort(-pred, kind="stable")
    a_sorted, p_sorted = actual[order], pred[order]
    _, start, counts = np.unique(-p_sorted, return_index=True, return_counts=True)
    if counts.max() > 1:
        group_sums = np.add.reduceat(a_sorted, start)
        a_sorted = np.repeat(group_sums / counts, counts)
    cum_share = np.cumsum(a_sorted) / actual.sum()
    return float(cum_share.sum() / n - (n + 1) / (2 * n))


def normalized_gini(actual: np.ndarray, pred: np.ndarray) -> float:
    """Gini of the model divided by the Gini of a perfect model (1.0 = perfect ranking, 0 = random)."""
    return gini(actual, pred) / gini(actual, actual)


def lorenz_curve(actual: np.ndarray, pred: np.ndarray, points: int = 101) -> tuple[np.ndarray, np.ndarray]:
    """x = share of claims (ranked by prediction, highest first), y = share of actual loss captured."""
    actual = np.asarray(actual, dtype=float)
    order = np.argsort(-np.asarray(pred, dtype=float), kind="stable")
    cum = np.concatenate([[0.0], np.cumsum(actual[order]) / actual.sum()])
    x = np.linspace(0, 1, cum.size)
    grid = np.linspace(0, 1, points)
    return grid, np.interp(grid, x, cum)


def decile_table(actual: np.ndarray, pred: np.ndarray, n_bins: int = 10) -> pd.DataFrame:
    """Mean predicted vs mean actual by prediction decile (1 = lowest predicted)."""
    df = pd.DataFrame({"actual": np.asarray(actual, float), "pred": np.asarray(pred, float)})
    df["decile"] = pd.qcut(df["pred"].rank(method="first"), n_bins, labels=False) + 1
    out = df.groupby("decile").agg(n=("actual", "size"), mean_pred=("pred", "mean"), mean_actual=("actual", "mean")).reset_index()
    out["actual_to_expected"] = out["mean_actual"] / out["mean_pred"]
    out["lift_vs_mean"] = out["mean_actual"] / df["actual"].mean()
    return out


def top_decile_lift(actual: np.ndarray, pred: np.ndarray) -> float:
    t = decile_table(actual, pred)
    return float(t.loc[t["decile"] == t["decile"].max(), "lift_vs_mean"].iloc[0])


def gamma_deviance(actual: np.ndarray, pred: np.ndarray) -> float:
    """Mean Gamma deviance: 2 * mean(-log(y/mu) + (y - mu)/mu). Requires y, mu > 0."""
    y = np.asarray(actual, float)
    mu = np.asarray(pred, float)
    if (y <= 0).any() or (mu <= 0).any():
        raise ValueError("gamma deviance needs strictly positive actuals and predictions")
    return float(np.mean(2 * (-np.log(y / mu) + (y - mu) / mu)))


def credibility_z(n_claims: np.ndarray | float, full_standard: float = 1082.0) -> np.ndarray | float:
    """Limited-fluctuation credibility: Z = min(1, sqrt(n / n_full)).

    n_full = 1,082 claims is the classic standard for frequency (90% probability of being
    within +/-5% of the true mean under Poisson: (1.645 / 0.05)^2 ~= 1,082).
    """
    return np.minimum(1.0, np.sqrt(np.asarray(n_claims, float) / full_standard))
