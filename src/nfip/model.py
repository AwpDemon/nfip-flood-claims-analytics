"""Claim-severity model with honest, out-of-time validation.

Question: given what an underwriter knows *before* a loss (zone, occupancy, building and
coverage characteristics, state), how well can we rank the size of a flood claim?

* Target: building + contents paid on paid claims, in constant 2025 dollars (CPI-U).
  ICC is excluded (a separate coverage with its own cap).
* Split: train on loss years 2000-2019, test on 2020-2025 (out-of-time). A random split would
  leak catastrophe events across train/test (claims from the same hurricane are highly
  correlated), flattering every metric.
* Models: (1) zone-mean baseline, (2) Gamma GLM with log link - the actuarial standard, its
  coefficients are multiplicative rating relativities, (3) gradient-boosted trees with Gamma
  loss, (4) the same GBM with *post-loss* claim facts (water depth, cause, event type) to show
  how much signal only exists after the flood - those features are valid for claims triage /
  reserving, never for pricing.
* Metrics: normalized Gini (ranking), mean Gamma deviance (fit), A/E by decile (calibration),
  top-decile lift, MAE.

Run: ``python -m nfip model``. Writes docs/results/model_*.{csv,json} and docs/figures/model_*.png.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

from . import config
from .metrics import decile_table, gamma_deviance, lorenz_curve, normalized_gini, top_decile_lift
from .plotting import SERIES, TEXT_2, apply_style, plt, source_note

log = logging.getLogger(__name__)

TRAIN_YEARS = (2000, 2019)
TEST_YEARS = (2020, 2025)
SAMPLE_MOD = 3  # deterministic 1-in-3 sample of paid claims keeps RAM modest (~0.5M rows)

UNDERWRITING_CATS = [
    "flood_zone_detail",
    "occupancy_group",
    "state_grp",
    "floors",
    "basement",
    "age_band",
    "deductible",
    "primary_residence",
    "elevated",
    "post_firm",
]
UNDERWRITING_NUMS = ["log_bldg_cov", "log_cont_cov"]
TRIAGE_CATS = ["depth_band", "cause_group", "event_type"]

FEATURE_SQL = """
select
    f.claim_id,
    f.loss_year,
    f.building_contents_paid_real                               as y,
    z.flood_zone_detail,
    o.occupancy_group,
    g.state_abbr,
    coalesce(cast(least(f.number_of_floors, 4) as varchar), 'NA') as floors,
    coalesce(cast(f.basement_enclosure_type as varchar), 'NA') as basement,
    case
        when f.building_age_at_loss_years is null then 'unknown'
        when f.building_age_at_loss_years < 10 then '<10'
        when f.building_age_at_loss_years < 30 then '10-29'
        when f.building_age_at_loss_years < 50 then '30-49'
        when f.building_age_at_loss_years < 70 then '50-69'
        else '70+'
    end                                                         as age_band,
    coalesce(f.building_deductible_code, 'NA')                  as deductible,
    coalesce(cast(f.is_primary_residence as varchar), 'NA')     as primary_residence,
    coalesce(cast(f.is_elevated as varchar), 'NA')              as elevated,
    coalesce(cast(f.is_post_firm as varchar), 'NA')             as post_firm,
    ln(1 + greatest(coalesce(f.building_coverage, 0), 0) * f.cpi_factor) as log_bldg_cov,
    ln(1 + greatest(coalesce(f.contents_coverage, 0), 0) * f.cpi_factor) as log_cont_cov,
    case
        when f.water_depth_inches is null then 'NA'
        when f.water_depth_inches <= 0 then '0'
        when f.water_depth_inches < 12 then '1-11in'
        when f.water_depth_inches < 24 then '12-23in'
        when f.water_depth_inches < 48 then '24-47in'
        when f.water_depth_inches < 96 then '48-95in'
        else '96in+'
    end                                                         as depth_band,
    c.cause_group,
    e.event_type
from marts.fact_claims as f
join marts.dim_flood_zone as z using (flood_zone_key)
join marts.dim_occupancy as o using (occupancy_key)
join marts.dim_geography as g using (geography_key)
join marts.dim_cause_of_damage as c using (cause_key)
join marts.dim_event as e using (event_key)
where f.is_paid_claim
  and f.building_contents_paid_real > 0
  and f.loss_year between {y0} and {y1}
  and hash(f.claim_id) % {mod} = 0
order by f.claim_id
"""


def load_features(db_path: Path | None = None, sample_mod: int = SAMPLE_MOD) -> pd.DataFrame:
    con = duckdb.connect(str(db_path or config.paths().warehouse), read_only=True)
    df = con.execute(FEATURE_SQL.format(y0=TRAIN_YEARS[0], y1=TEST_YEARS[1], mod=sample_mod)).df()
    con.close()
    return df


def prepare(df: pd.DataFrame, top_states: list[str] | None = None) -> tuple[pd.DataFrame, list[str]]:
    df = df.copy()
    if top_states is None:
        train = df[df["loss_year"] <= TRAIN_YEARS[1]]
        top_states = train["state_abbr"].value_counts().head(15).index.tolist()
    df["state_grp"] = np.where(df["state_abbr"].isin(top_states), df["state_abbr"], "OTHER")
    for c in UNDERWRITING_CATS + TRIAGE_CATS:
        df[c] = df[c].astype(str)
    return df, top_states


def _encode(train: pd.DataFrame, test: pd.DataFrame, cats: list[str], nums: list[str]):
    """Ordinal-encode categoricals using train categories only (unseen -> -1 -> NaN)."""
    xtr, xte = pd.DataFrame(index=train.index), pd.DataFrame(index=test.index)
    for c in cats:
        cat = pd.Categorical(train[c])
        xtr[c] = cat.codes
        xte[c] = pd.Categorical(test[c], categories=cat.categories).codes
        xte.loc[xte[c] < 0, c] = np.nan
    for c in nums:
        xtr[c], xte[c] = train[c], test[c]
    cat_mask = [True] * len(cats) + [False] * len(nums)
    return xtr, xte, cat_mask


def fit_gbm(train: pd.DataFrame, test: pd.DataFrame, cats: list[str], nums: list[str], seed: int = 7):
    xtr, xte, mask = _encode(train, test, cats, nums)
    model = HistGradientBoostingRegressor(
        loss="gamma",
        learning_rate=0.06,
        max_iter=400,
        max_leaf_nodes=31,
        min_samples_leaf=300,
        l2_regularization=1.0,
        categorical_features=mask,
        early_stopping=True,
        validation_fraction=0.1,
        n_iter_no_change=20,
        random_state=seed,
    )
    model.fit(xtr, train["y"])
    return model, model.predict(xte), model.predict(xtr)


def fit_glm(train: pd.DataFrame, test: pd.DataFrame, trend: bool = False):
    """Gamma GLM, log link. With ``trend=True`` a linear loss-year term (a calendar-year severity
    trend, exp(beta) per year) is added and extrapolated into the test years - the actuarial
    way to carry a level forward instead of assuming the training-period average."""
    terms = [f"C({c})" for c in UNDERWRITING_CATS] + UNDERWRITING_NUMS + (["trend_year"] if trend else [])
    formula = "y ~ " + " + ".join(terms)
    glm = smf.glm(formula, data=train, family=sm.families.Gamma(link=sm.families.links.Log())).fit()
    # Unseen category levels in test cannot be scored by the formula; map them to the reference level.
    test = test.copy()
    for c in UNDERWRITING_CATS:
        seen = set(train[c].unique())
        ref = sorted(seen)[0]
        test.loc[~test[c].isin(seen), c] = ref
    return glm, np.asarray(glm.predict(test)), np.asarray(glm.predict(train))


def evaluate(name: str, y: np.ndarray, pred: np.ndarray) -> dict:
    return {
        "model": name,
        "normalized_gini": normalized_gini(y, pred),
        "mean_gamma_deviance": gamma_deviance(y, pred),
        "mae": float(mean_absolute_error(y, pred)),
        "actual_to_expected": float(y.mean() / pred.mean()),
        "top_decile_lift": top_decile_lift(y, pred),
    }


def main(db_path: Path | None = None, sample_mod: int = SAMPLE_MOD, out_dir: Path | None = None) -> dict:
    apply_style()
    p = config.paths()
    results = out_dir or (p.docs / "results")
    figures = out_dir or p.figures
    results.mkdir(parents=True, exist_ok=True)
    figures.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    raw = load_features(db_path, sample_mod)
    df, top_states = prepare(raw)
    train = df[df["loss_year"].between(*TRAIN_YEARS)].reset_index(drop=True)
    test = df[df["loss_year"].between(*TEST_YEARS)].reset_index(drop=True)
    log.info("train %s rows (%s-%s), test %s rows (%s-%s)", len(train), *TRAIN_YEARS, len(test), *TEST_YEARS)
    y_te = test["y"].to_numpy()

    # 1. baseline: train mean severity by flood-zone detail
    zone_means = train.groupby("flood_zone_detail")["y"].mean()
    base_pred = test["flood_zone_detail"].map(zone_means).fillna(train["y"].mean()).to_numpy()

    # 2. Gamma GLM
    glm, glm_pred, _ = fit_glm(train, test)

    # 2b. Gamma GLM + calendar-year severity trend
    train["trend_year"] = train["loss_year"] - TRAIN_YEARS[1]
    test["trend_year"] = test["loss_year"] - TRAIN_YEARS[1]
    glm_tr, glm_tr_pred, _ = fit_glm(train, test, trend=True)
    annual_trend = float(np.exp(glm_tr.params["trend_year"]) - 1)

    # 3. GBM, underwriting features only
    gbm, gbm_pred, _ = fit_gbm(train, test, UNDERWRITING_CATS, UNDERWRITING_NUMS)

    # 4. GBM + post-loss facts (claims triage, NOT pricing)
    gbm_t, gbm_t_pred, _ = fit_gbm(train, test, UNDERWRITING_CATS + TRIAGE_CATS, UNDERWRITING_NUMS)

    metrics = [
        evaluate("Baseline: zone mean", y_te, base_pred),
        evaluate("Gamma GLM (underwriting features)", y_te, glm_pred),
        evaluate("Gamma GLM + calendar-year trend", y_te, glm_tr_pred),
        evaluate("GBM, Gamma loss (underwriting features)", y_te, gbm_pred),
        evaluate("GBM + post-loss facts (triage only)", y_te, gbm_t_pred),
    ]
    mdf = pd.DataFrame(metrics)
    mdf.to_csv(results / "model_metrics.csv", index=False)

    # stability: Gini by test loss year (GLM and GBM)
    by_year = []
    for yr, idx in test.groupby("loss_year").groups.items():
        idx = np.asarray(list(idx))
        by_year.append(
            {
                "loss_year": int(yr),
                "n": int(idx.size),
                "gini_glm": normalized_gini(y_te[idx], glm_pred[idx]),
                "gini_gbm": normalized_gini(y_te[idx], gbm_pred[idx]),
                "a_to_e_glm": float(y_te[idx].mean() / glm_pred[idx].mean()),
                "a_to_e_glm_trended": float(y_te[idx].mean() / glm_tr_pred[idx].mean()),
                "a_to_e_gbm": float(y_te[idx].mean() / gbm_pred[idx].mean()),
            }
        )
    pd.DataFrame(by_year).to_csv(results / "model_gini_by_year.csv", index=False)

    deciles = decile_table(y_te, glm_pred)
    deciles.to_csv(results / "model_glm_deciles.csv", index=False)
    deciles_gbm = decile_table(y_te, gbm_pred)
    deciles_gbm.to_csv(results / "model_gbm_deciles.csv", index=False)

    # GLM relativities (exp(coef)) - the interpretable rating factors
    rel = pd.DataFrame(
        {"term": glm.params.index, "coef": glm.params.values, "relativity": np.exp(glm.params.values), "p_value": glm.pvalues.values}
    )
    rel.to_csv(results / "model_glm_relativities.csv", index=False)

    # ---- figures
    fig, ax = plt.subplots(figsize=(7.5, 5))
    for color, (name, pred) in zip(
        SERIES[:3], [("Baseline: zone mean", base_pred), ("Gamma GLM", glm_pred), ("GBM (Gamma loss)", gbm_pred)], strict=True
    ):
        x, yv = lorenz_curve(y_te, pred)
        ax.plot(x, yv, color=color, label=f"{name}  (Gini {normalized_gini(y_te, pred):.3f})")
    ax.plot([0, 1], [0, 1], color=TEXT_2, linewidth=1, linestyle="--", label="Random ranking")
    ax.set_xlabel("Share of test claims, ranked by predicted severity (highest first)")
    ax.set_ylabel("Share of actual paid $ captured")
    ax.set_title("Out-of-time ranking power, loss years 2020-2025")
    ax.legend(loc="lower right")
    ax.grid(axis="both")
    source_note(fig, "Paid claims, constant 2025 $. Train 2000-2019 / test 2020-2025, 1-in-3 deterministic sample.")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(figures / "model_lorenz.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 4.4))
    deciles = decile_table(y_te, glm_tr_pred)
    deciles.to_csv(results / "model_glm_trended_deciles.csv", index=False)
    x = np.arange(1, 11)
    w = 0.38
    ax.bar(x - w / 2, deciles["mean_pred"], width=w, color=SERIES[0], label="Predicted (GLM + trend)")
    ax.bar(x + w / 2, deciles["mean_actual"], width=w, color=SERIES[1], label="Actual")
    ax.set_xticks(x)
    ax.set_xlabel("Predicted-severity decile (test set)")
    ax.yaxis.set_major_formatter(lambda v, _p: f"${v / 1e3:,.0f}k")
    ax.set_title("Trended GLM: lift and calibration by decile, out-of-time (2020-2025)")
    ax.legend(loc="upper left")
    for i, ae in enumerate(deciles["actual_to_expected"]):
        ax.annotate(
            f"A/E {ae:.2f}",
            (i + 1, max(deciles["mean_pred"].iloc[i], deciles["mean_actual"].iloc[i])),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            fontsize=7.5,
            color=TEXT_2,
        )
    source_note(fig, "A/E = mean actual / mean predicted. Constant 2025 $.")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(figures / "model_glm_deciles.png")
    plt.close(fig)

    summary = {
        "train_rows": int(len(train)),
        "test_rows": int(len(test)),
        "train_years": TRAIN_YEARS,
        "test_years": TEST_YEARS,
        "sample": f"hash(claim_id) % {sample_mod} = 0",
        "top_states": top_states,
        "metrics": metrics,
        "gini_by_year": by_year,
        "gbm_iterations": int(gbm.n_iter_),
        "gbm_triage_iterations": int(gbm_t.n_iter_),
        "glm_params": int(len(glm.params)),
        "glm_annual_severity_trend": annual_trend,
        "train_mean_severity": float(train["y"].mean()),
        "test_mean_severity": float(y_te.mean()),
        "runtime_seconds": round(time.time() - t0, 1),
    }
    (results / "model_summary.json").write_text(json.dumps(summary, indent=2))
    log.info("\n%s", mdf.to_string(index=False))
    return summary


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    main()
