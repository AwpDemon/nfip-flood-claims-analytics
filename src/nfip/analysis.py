"""Analytics layer: reads the dbt marts (read-only) and writes figures + result tables.

Every number quoted in the README / memo comes from ``docs/RESULTS.md``, which this
module regenerates from the warehouse: ``python -m nfip analyze``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from . import config
from .plotting import DOLLARS, SERIES, TEXT_2, apply_style, plt, source_note

log = logging.getLogger(__name__)

SRC = "Source: FEMA OpenFEMA NFIP Redacted Claims v3 & Policies v3; CPI-U (BLS via FRED). Constant 2025 dollars where noted."
ZONE_ORDER = ["V (coastal high hazard)", "A (1% annual chance)", "X/B/C (moderate-minimal)"]


def connect(db_path: Path | None = None) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(db_path or config.paths().warehouse), read_only=True)


def q(con: duckdb.DuckDBPyConnection, sql: str) -> pd.DataFrame:
    return con.execute(sql).df()


# --------------------------------------------------------------------------- queries


def headline(con) -> dict:
    h = (
        q(
            con,
            """
        select count(*) as claims,
               count(*) filter (where is_paid_claim) as paid_claims,
               sum(total_paid) as total_paid_nominal,
               sum(total_paid_real) as total_paid_real,
               min(loss_year) as first_year, max(loss_year) as last_year,
               sum(building_paid) / sum(building_paid + contents_paid) as building_share_of_bc_paid,
               avg(case when contents_paid > 0 then 1.0 else 0.0 end) filter (where is_paid_claim) as share_paid_with_contents,
               avg(case when is_paid_claim then 1.0 else 0.0 end) as share_claims_paid
        from marts.fact_claims
        """,
        )
        .iloc[0]
        .to_dict()
    )
    sev = q(
        con,
        """
        select quantile_cont(total_paid_real, [0.25, 0.5, 0.75, 0.9, 0.99]) as q, avg(total_paid_real) as mean
        from marts.fact_claims where is_paid_claim
        """,
    ).iloc[0]
    h["severity_real_quantiles"] = dict(zip(["p25", "p50", "p75", "p90", "p99"], [float(x) for x in sev["q"]], strict=True))
    h["severity_real_mean"] = float(sev["mean"])
    conc = q(
        con,
        """
        with r as (select total_paid_real, ntile(100) over (order by total_paid_real desc) as pct
                   from marts.fact_claims where is_paid_claim)
        select sum(total_paid_real) filter (where pct = 1) / sum(total_paid_real) as top1,
               sum(total_paid_real) filter (where pct <= 10) / sum(total_paid_real) as top10
        from r
        """,
    ).iloc[0]
    h["share_paid_top1pct_claims"] = float(conc["top1"])
    h["share_paid_top10pct_claims"] = float(conc["top10"])
    ev = q(
        con,
        """
        select sum(total_paid_real) filter (where rank_by_real_paid <= 5) / sum(total_paid_real) as top5
        from marts.mart_event_losses
        """,
    ).iloc[0]
    h["share_paid_top5_events"] = float(ev["top5"])
    mlp = q(
        con,
        """
        select sum(multiple_loss_properties) as n, sum(nfip_repetitive_loss_properties) as rl,
               sum(nfip_severe_repetitive_loss_properties) as srl,
               sum(share_mitigated * multiple_loss_properties) / sum(multiple_loss_properties) as mitigated,
               sum(share_currently_insured * multiple_loss_properties) / sum(multiple_loss_properties) as insured,
               sum(share_in_sfha * multiple_loss_properties) / sum(multiple_loss_properties) as in_sfha
        from marts.mart_repetitive_loss_by_state
        """,
    ).iloc[0]
    h.update({f"mlp_{k}": float(v) for k, v in mlp.items()})
    return {k: (float(v) if isinstance(v, (np.floating, np.integer, float, int)) else v) for k, v in h.items()}


def annual(con) -> pd.DataFrame:
    return q(
        con,
        """
        select loss_year, count(*) filter (where is_paid_claim) as paid_claims,
               sum(total_paid) as total_paid, sum(total_paid_real) as total_paid_real
        from marts.fact_claims group by 1 order by 1
        """,
    )


def national_year(con) -> pd.DataFrame:
    return q(
        con,
        """
        select calendar_year, sum(paid_claim_count) as paid_claims, sum(total_paid) as total_paid,
               sum(total_paid_real) as total_paid_real, sum(earned_policy_years) as earned_policy_years,
               sum(earned_premium) as earned_premium, sum(earned_premium_real) as earned_premium_real,
               1000.0 * sum(paid_claim_count) / sum(earned_policy_years) as frequency_per_1000,
               sum(total_paid_real) / sum(earned_policy_years) as loss_cost_real,
               sum(total_paid) / sum(earned_premium) as paid_to_premium_proxy
        from marts.mart_loss_experience_state_year
        where is_complete_exposure_year
        group by 1 order by 1
        """,
    )


def zone_summary(con) -> pd.DataFrame:
    return q(
        con,
        """
        select flood_zone_group,
               sum(earned_policy_years) as earned_policy_years,
               sum(paid_claim_count) as paid_claims,
               sum(total_paid_real) as total_paid_real,
               1000.0 * sum(paid_claim_count) / sum(earned_policy_years) as frequency_per_1000,
               sum(total_paid_real) / nullif(sum(paid_claim_count), 0) as severity_real,
               sum(total_paid_real) / sum(earned_policy_years) as loss_cost_real,
               sum(total_paid) / sum(earned_premium) as paid_to_premium_proxy
        from marts.mart_loss_experience_zone_year
        where is_complete_exposure_year
        group by 1 order by loss_cost_real desc
        """,
    )


def events(con, n: int = 15) -> pd.DataFrame:
    return q(con, f"select * from marts.mart_event_losses where event_name <> '(no event name)' order by total_paid_real desc limit {n}")


def state_relativity(con, min_policy_years: float = 50_000) -> pd.DataFrame:
    """States ranked by real loss cost; thin states (< min_policy_years earned) excluded."""
    return q(
        con,
        f"""
        with s as (
            select state_abbr, sum(earned_policy_years) as py, sum(paid_claim_count) as n,
                   sum(total_paid_real) as paid_real, sum(total_paid) as paid, sum(earned_premium) as prem
            from marts.mart_loss_experience_state_year
            where is_complete_exposure_year
            group by 1
        ), nat as (select sum(paid_real) / sum(py) as lc from s)
        select s.state_abbr, s.py as earned_policy_years, s.n as paid_claims, s.paid_real as total_paid_real,
               1000.0 * s.n / s.py as frequency_per_1000,
               s.paid_real / s.py as loss_cost_real,
               (s.paid_real / s.py) / nat.lc as relativity,
               least(1.0, sqrt(s.n / 1082.0)) as credibility_z,
               s.paid / s.prem as paid_to_premium_proxy
        from s, nat
        where s.py >= {min_policy_years}
        order by loss_cost_real desc
        """,
    )


def severity_by_decade(con) -> pd.DataFrame:
    return q(
        con,
        """
        select (loss_year // 10) * 10 as decade,
               count(*) filter (where is_paid_claim) as paid_claims,
               median(total_paid) filter (where is_paid_claim) as median_nominal,
               median(total_paid_real) filter (where is_paid_claim) as median_real,
               median(building_coverage * cpi_factor) filter (where is_paid_claim) as median_building_coverage_real
        from marts.fact_claims group by 1 order by 1
        """,
    )


def building_contents_by_occupancy(con) -> pd.DataFrame:
    return q(
        con,
        """
        select o.occupancy_group,
               count(*) filter (where f.is_paid_claim) as paid_claims,
               sum(f.building_paid) as building_paid, sum(f.contents_paid) as contents_paid,
               sum(f.building_paid) / nullif(sum(f.building_paid + f.contents_paid), 0) as building_share,
               avg(case when f.contents_paid > 0 then 1.0 else 0.0 end) filter (where f.is_paid_claim) as share_with_contents_payment,
               avg(case when f.contents_coverage > 0 then 1.0 else 0.0 end) filter (where f.is_paid_claim) as share_with_contents_coverage
        from marts.fact_claims as f join marts.dim_occupancy as o using (occupancy_key)
        group by 1 having count(*) filter (where f.is_paid_claim) > 1000
        order by paid_claims desc
        """,
    )


def severity_samples(con) -> pd.DataFrame:
    return q(
        con,
        """
        select z.flood_zone_group, f.total_paid_real
        from marts.fact_claims as f join marts.dim_flood_zone as z using (flood_zone_key)
        where f.is_paid_claim and f.total_paid_real > 0 and f.loss_year between 2010 and 2025
          and hash(f.claim_id) % 4 = 0
        """,
    )


def cycle_time(con) -> pd.DataFrame:
    return q(
        con,
        """
        select e.event_type,
               count(*) as paid_claims,
               median(f.days_loss_to_last_payment) as median_days,
               quantile_cont(f.days_loss_to_last_payment, 0.9) as p90_days
        from marts.fact_claims as f join marts.dim_event as e using (event_key)
        where f.is_paid_claim and f.loss_year between 2010 and 2025 and f.days_loss_to_last_payment is not null
        group by 1 order by paid_claims desc
        """,
    )


def repetitive_loss(con) -> pd.DataFrame:
    return q(con, "select * from marts.mart_repetitive_loss_by_state order by multiple_loss_properties desc")


def outside_sfha(con) -> pd.DataFrame:
    return q(
        con,
        """
        select z.is_sfha,
               count(*) filter (where f.is_paid_claim) as paid_claims,
               sum(f.total_paid_real) as total_paid_real
        from marts.fact_claims as f join marts.dim_flood_zone as z using (flood_zone_key)
        where f.loss_year between 2010 and 2025 and z.flood_zone_group <> 'Unknown'
        group by 1
        """,
    )


def exposure_by_sfha(con) -> pd.DataFrame:
    return q(
        con,
        """
        select z.is_sfha, sum(e.earned_policy_years) as earned_policy_years
        from marts.fact_policy_exposure as e join marts.dim_flood_zone as z using (flood_zone_key)
        where e.is_complete_exposure_year and z.flood_zone_group <> 'Unknown'
        group by 1
        """,
    )


# --------------------------------------------------------------------------- figures


def fig_annual(df: pd.DataFrame, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4.2))
    ax.bar(df["loss_year"], df["total_paid_real"], color=SERIES[0], width=0.8)
    ax.yaxis.set_major_formatter(DOLLARS)
    ax.set_title("NFIP paid losses by year of loss (constant 2025 $)")
    ax.set_xlabel("Year of loss")
    labels = {2005: "Katrina", 2012: "Sandy", 2017: "Harvey", 2024: "Helene / Milton", 2022: "Ian", 2008: "Ike", 2016: "LA floods"}
    for yr, name in labels.items():
        row = df.loc[df["loss_year"] == yr]
        if not row.empty:
            ax.annotate(
                name, (yr, row["total_paid_real"].iloc[0]), xytext=(0, 3), textcoords="offset points", ha="center", fontsize=8, color=TEXT_2
            )
    source_note(fig, SRC + " 2025-26 losses are still developing.")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out)
    plt.close(fig)


def fig_zone(df: pd.DataFrame, out: Path) -> None:
    d = df.set_index("flood_zone_group").reindex(ZONE_ORDER)
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.6))
    specs = [
        ("frequency_per_1000", "Paid claims per 1,000 policy-years", lambda v: f"{v:.1f}"),
        ("severity_real", "Severity: paid per paid claim", lambda v: f"${v / 1e3:,.0f}k"),
        ("loss_cost_real", "Loss cost per policy-year", lambda v: f"${v:,.0f}"),
    ]
    short = ["V", "A", "X/B/C"]
    for ax, (col, title, fmt) in zip(axes, specs, strict=True):
        vals = d[col].to_numpy()
        ax.bar(short, vals, color=SERIES[0], width=0.6)
        ax.set_title(title, fontsize=10.5)
        for i, v in enumerate(vals):
            ax.annotate(fmt(v), (i, v), xytext=(0, 3), textcoords="offset points", ha="center", fontsize=9)
        ax.set_yticks([])
        ax.grid(False)
    fig.suptitle(
        "Flood zone drives frequency more than severity (2010-2025, constant 2025 $)", x=0.01, ha="left", fontsize=12, fontweight="bold"
    )
    source_note(
        fig,
        "Source: FEMA OpenFEMA NFIP Claims v3 & Policies v3; CPI-U via FRED. V = coastal high hazard, A = 1% annual chance, X/B/C = outside SFHA.",
    )
    fig.tight_layout(rect=(0, 0.04, 1, 0.95))
    fig.savefig(out)
    plt.close(fig)


def fig_severity_distribution(df: pd.DataFrame, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4.2))
    bins = np.linspace(2, 6.4, 60)
    for color, zone in zip(SERIES[:3], ZONE_ORDER, strict=True):
        vals = np.log10(df.loc[df["flood_zone_group"] == zone, "total_paid_real"].clip(lower=100))
        hist, edges = np.histogram(vals, bins=bins, density=True)
        ax.plot((edges[:-1] + edges[1:]) / 2, hist, color=color, label=f"{zone} (median ${10 ** np.median(vals) / 1e3:,.0f}k)")
    ax.set_xticks([2, 3, 4, 5, 6])
    ax.set_xticklabels(["$100", "$1k", "$10k", "$100k", "$1M"])
    ax.set_xlabel("Paid per paid claim (constant 2025 $, log scale)")
    ax.set_ylabel("Density")
    ax.set_title("Severity is heavy-tailed and right-skewed in every zone")
    ax.legend(loc="upper left")
    source_note(fig, SRC + " Paid claims 2010-2025, 25% deterministic sample.")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out)
    plt.close(fig)


def fig_events(df: pd.DataFrame, out: Path) -> None:
    d = df.sort_values("total_paid_real")
    fig, ax = plt.subplots(figsize=(10, 5.2))
    ax.barh(d["event_label"], d["total_paid_real"], color=SERIES[0], height=0.65)
    for y, (real, nom) in enumerate(zip(d["total_paid_real"], d["total_paid"], strict=True)):
        ax.annotate(
            f"{DOLLARS(real)} (nominal {DOLLARS(nom)})",
            (real, y),
            xytext=(4, 0),
            textcoords="offset points",
            va="center",
            fontsize=8,
            color=TEXT_2,
        )
    ax.xaxis.set_major_formatter(DOLLARS)
    ax.grid(axis="x")
    ax.grid(axis="y", visible=False)
    ax.set_xlim(0, d["total_paid_real"].max() * 1.35)
    ax.set_title("Largest NFIP flood events by paid losses (constant 2025 $)")
    source_note(fig, SRC)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out)
    plt.close(fig)


def fig_state(df: pd.DataFrame, out: Path, n: int = 15) -> None:
    d = df.head(n).sort_values("loss_cost_real")
    fig, ax = plt.subplots(figsize=(10, 5.2))
    ax.barh(d["state_abbr"], d["loss_cost_real"], color=SERIES[0], height=0.65)
    for y, (lc, rel, ptp) in enumerate(zip(d["loss_cost_real"], d["relativity"], d["paid_to_premium_proxy"], strict=True)):
        ax.annotate(
            f"${lc:,.0f}  ({rel:.1f}x national; paid/premium {ptp:.2f})",
            (lc, y),
            xytext=(4, 0),
            textcoords="offset points",
            va="center",
            fontsize=8,
            color=TEXT_2,
        )
    ax.xaxis.set_major_formatter(DOLLARS)
    ax.grid(axis="x")
    ax.grid(axis="y", visible=False)
    ax.set_xlim(0, d["loss_cost_real"].max() * 1.6)
    ax.set_title(f"Top {n} states by loss cost per earned policy-year, 2010-2025 (constant 2025 $)")
    source_note(
        fig,
        "Source: FEMA OpenFEMA NFIP Claims v3 & Policies v3; CPI-U via FRED. States with >= 50k earned policy-years. Paid/premium is a proxy.",
    )
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out)
    plt.close(fig)


def fig_national_trend(df: pd.DataFrame, out: Path) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    axes[0].bar(df["calendar_year"], df["frequency_per_1000"], color=SERIES[0], width=0.7)
    axes[0].set_title("Claim frequency: paid claims per 1,000 earned policy-years", fontsize=10.5)
    axes[1].bar(df["calendar_year"], df["paid_to_premium_proxy"], color=SERIES[0], width=0.7)
    axes[1].axhline(1.0, color=TEXT_2, linewidth=1, linestyle="--")
    axes[1].annotate(
        "paid = earned premium", (df["calendar_year"].min() - 0.4, 1.0), xytext=(0, 3), textcoords="offset points", fontsize=8, color=TEXT_2
    )
    axes[1].set_title("Paid losses / earned premium (proxy; undeveloped, excl. LAE and fees)", fontsize=10.5)
    axes[1].set_xticks(df["calendar_year"])
    axes[1].tick_params(axis="x", rotation=45)
    fig.suptitle(
        "Catastrophe years dominate: 2012, 2017 and 2024 each paid > 2x earned premium", x=0.01, ha="left", fontsize=12, fontweight="bold"
    )
    source_note(fig, SRC + " Exposure = pro-rata earned policy-years from Policies v3 (complete for 2010-2025).")
    fig.tight_layout(rect=(0, 0.03, 1, 0.96))
    fig.savefig(out)
    plt.close(fig)


def fig_decade(df: pd.DataFrame, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4))
    x = df["decade"].astype(int).astype(str) + "s"
    ax.plot(x, df["median_nominal"], color=SERIES[0], marker="o", markersize=7, label="Median paid, nominal $")
    ax.plot(x, df["median_real"], color=SERIES[1], marker="o", markersize=7, label="Median paid, constant 2025 $")
    ax.plot(
        x, df["median_building_coverage_real"], color=SERIES[2], marker="o", markersize=7, label="Median building coverage, constant 2025 $"
    )
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(DOLLARS)
    ax.set_title("Severity outgrew inflation: real median claim up ~4x since the 1980s")
    ax.legend(loc="upper left")
    source_note(fig, SRC + " Log scale. Paid claims only, by decade of loss.")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out)
    plt.close(fig)


def fig_building_contents(df: pd.DataFrame, out: Path) -> None:
    d = df.sort_values("building_share")
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.barh(d["occupancy_group"], d["building_share"], color=SERIES[0], height=0.6, label="Building")
    ax.barh(d["occupancy_group"], 1 - d["building_share"], left=d["building_share"] + 0.004, color=SERIES[1], height=0.6, label="Contents")
    for y, (b, w) in enumerate(zip(d["building_share"], d["share_with_contents_coverage"], strict=True)):
        ax.annotate(
            f"{b:.0%} building; {w:.0%} of paid claims had contents cover",
            (1.01, y),
            va="center",
            fontsize=8,
            color=TEXT_2,
            annotation_clip=False,
        )
    ax.set_xlim(0, 1)
    ax.xaxis.set_major_formatter(lambda v, _p: f"{v:.0%}")
    ax.grid(axis="x")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2)
    ax.set_title("Building vs contents share of paid losses by occupancy", pad=22)
    source_note(fig, SRC + " All loss years.")
    fig.tight_layout(rect=(0, 0.03, 0.72, 1))
    fig.savefig(out)
    plt.close(fig)


def fig_repetitive(df: pd.DataFrame, out: Path, n: int = 12) -> None:
    d = df[df["state_abbr"] != "Unknown"].head(n).sort_values("multiple_loss_properties")
    fig, ax = plt.subplots(figsize=(10, 4.6))
    ax.barh(d["state_abbr"], d["multiple_loss_properties"], color=SERIES[0], height=0.65)
    for y, (v, m, ins) in enumerate(zip(d["multiple_loss_properties"], d["share_mitigated"], d["share_currently_insured"], strict=True)):
        ax.annotate(
            f"{v:,.0f}  ({m:.0%} mitigated, {ins:.0%} still insured)",
            (v, y),
            xytext=(4, 0),
            textcoords="offset points",
            va="center",
            fontsize=8,
            color=TEXT_2,
        )
    ax.grid(axis="x")
    ax.grid(axis="y", visible=False)
    ax.set_xlim(0, d["multiple_loss_properties"].max() * 1.5)
    ax.set_title("Multiple-loss properties by state (NFIP Multiple Loss Properties v1)")
    source_note(fig, "Source: FEMA OpenFEMA NFIP Multiple Loss Properties v1.")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out)
    plt.close(fig)


# --------------------------------------------------------------------------- report


def _md_table(df: pd.DataFrame, fmt: dict[str, str] | None = None) -> str:
    fmt = fmt or {}
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            f = fmt.get(c)
            if f and pd.notna(v):
                cells.append(f.format(v))
            elif isinstance(v, float):
                cells.append(f"{v:,.2f}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(db_path: Path | None = None) -> dict:
    apply_style()
    p = config.paths()
    p.figures.mkdir(parents=True, exist_ok=True)
    results_dir = p.docs / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    con = connect(db_path)

    h = headline(con)
    ann = annual(con)
    nat = national_year(con)
    zone = zone_summary(con)
    ev = events(con)
    total_py = float(nat["earned_policy_years"].sum())
    st = state_relativity(con, min_policy_years=min(50_000, 0.005 * total_py))
    dec = severity_by_decade(con)
    bc = building_contents_by_occupancy(con)
    sev = severity_samples(con)
    cyc = cycle_time(con)
    rl = repetitive_loss(con)
    sfha = outside_sfha(con)
    sfha_exp = exposure_by_sfha(con)
    con.close()

    fig_annual(ann, p.figures / "annual_paid_real.png")
    fig_zone(zone, p.figures / "zone_frequency_severity.png")
    fig_severity_distribution(sev, p.figures / "severity_distribution.png")
    fig_events(ev, p.figures / "top_events.png")
    fig_state(st, p.figures / "state_loss_cost.png")
    fig_national_trend(nat, p.figures / "national_frequency_paid_to_premium.png")
    fig_decade(dec, p.figures / "severity_by_decade.png")
    fig_building_contents(bc, p.figures / "building_vs_contents.png")
    fig_repetitive(rl, p.figures / "repetitive_loss_states.png")

    for name, df in {
        "annual": ann,
        "national_year": nat,
        "zone_summary": zone,
        "top_events": ev,
        "state_loss_cost": st,
        "severity_by_decade": dec,
        "building_contents_by_occupancy": bc,
        "cycle_time_by_event_type": cyc,
        "repetitive_loss_by_state": rl,
    }.items():
        df.to_csv(results_dir / f"{name}.csv", index=False)

    s = sfha.set_index("is_sfha")
    e = sfha_exp.set_index("is_sfha")
    h["outside_sfha_share_paid_claims_2010_2025"] = float(s.loc[False, "paid_claims"] / s["paid_claims"].sum())
    h["outside_sfha_share_paid_real_2010_2025"] = float(s.loc[False, "total_paid_real"] / s["total_paid_real"].sum())
    h["outside_sfha_share_exposure_2010_2025"] = float(e.loc[False, "earned_policy_years"] / e["earned_policy_years"].sum())
    h["exposure_window_paid_to_premium"] = float(nat["total_paid"].sum() / nat["earned_premium"].sum())
    h["exposure_window_years_paid_over_premium"] = nat.loc[nat["paid_to_premium_proxy"] > 1, "calendar_year"].astype(int).tolist()
    h["earned_policy_years_2010_2025"] = float(nat["earned_policy_years"].sum())
    (results_dir / "headline.json").write_text(json.dumps(h, indent=2, default=str))

    hq = h["severity_real_quantiles"]
    md = [
        "# Results (auto-generated by `python -m nfip analyze`)",
        "",
        "Do not edit by hand: every number below is recomputed from the dbt marts.",
        "",
        "## Headline",
        "",
        f"- Claims: **{h['claims']:,.0f}** ({h['paid_claims']:,.0f} with a payment, {h['share_claims_paid']:.1%}), loss years {h['first_year']:.0f}-{h['last_year']:.0f}.",
        f"- Total paid: **${h['total_paid_nominal'] / 1e9:,.1f}B nominal = ${h['total_paid_real'] / 1e9:,.1f}B in constant 2025 dollars** (building + contents + ICC).",
        f"- Severity (paid claims, 2025 $): median ${hq['p50']:,.0f}, mean ${h['severity_real_mean']:,.0f}, p90 ${hq['p90']:,.0f}, p99 ${hq['p99']:,.0f}.",
        f"- Concentration: the top 1% of paid claims carry {h['share_paid_top1pct_claims']:.1%} of real paid dollars; top 10% carry {h['share_paid_top10pct_claims']:.1%}. The top 5 named events carry {h['share_paid_top5_events']:.1%}.",
        f"- Building share of building+contents paid: {h['building_share_of_bc_paid']:.1%}; {h['share_paid_with_contents']:.1%} of paid claims include a contents payment.",
        f"- Outside the SFHA (zones X/B/C/D), 2010-2025: {h['outside_sfha_share_exposure_2010_2025']:.1%} of earned policy-years, {h['outside_sfha_share_paid_claims_2010_2025']:.1%} of paid claims, {h['outside_sfha_share_paid_real_2010_2025']:.1%} of real paid dollars.",
        f"- 2010-2025 paid / earned premium (proxy): {h['exposure_window_paid_to_premium']:.2f}; years above 1.0: {h['exposure_window_years_paid_over_premium']}.",
        f"- Earned exposure 2010-2025: {h['earned_policy_years_2010_2025'] / 1e6:,.1f}M policy-years.",
        f"- Multiple-loss properties: {h['mlp_n']:,.0f} ({h['mlp_rl']:,.0f} NFIP repetitive-loss, {h['mlp_srl']:,.0f} severe repetitive-loss); {h['mlp_mitigated']:.1%} mitigated, {h['mlp_insured']:.1%} currently insured, {h['mlp_in_sfha']:.1%} in the SFHA.",
        "",
        "## By flood-zone group, 2010-2025 (constant 2025 $)",
        "",
        _md_table(
            zone,
            {
                "earned_policy_years": "{:,.0f}",
                "paid_claims": "{:,.0f}",
                "total_paid_real": "${:,.0f}",
                "frequency_per_1000": "{:.2f}",
                "severity_real": "${:,.0f}",
                "loss_cost_real": "${:,.0f}",
                "paid_to_premium_proxy": "{:.2f}",
            },
        ),
        "",
        "## National frequency and paid/premium by year",
        "",
        _md_table(
            nat[["calendar_year", "paid_claims", "earned_policy_years", "frequency_per_1000", "loss_cost_real", "paid_to_premium_proxy"]],
            {
                "calendar_year": "{:.0f}",
                "paid_claims": "{:,.0f}",
                "earned_policy_years": "{:,.0f}",
                "frequency_per_1000": "{:.2f}",
                "loss_cost_real": "${:,.0f}",
                "paid_to_premium_proxy": "{:.2f}",
            },
        ),
        "",
        "## Top 15 named events (constant 2025 $)",
        "",
        _md_table(
            ev[
                [
                    "event_label",
                    "paid_claim_count",
                    "total_paid",
                    "total_paid_real",
                    "severity_real",
                    "building_share_of_paid",
                    "median_days_loss_to_last_payment",
                ]
            ],
            {
                "paid_claim_count": "{:,.0f}",
                "total_paid": "${:,.0f}",
                "total_paid_real": "${:,.0f}",
                "severity_real": "${:,.0f}",
                "building_share_of_paid": "{:.1%}",
                "median_days_loss_to_last_payment": "{:.0f}",
            },
        ),
        "",
        "## Top 15 states by loss cost per earned policy-year, 2010-2025",
        "",
        _md_table(
            st.head(15),
            {
                "earned_policy_years": "{:,.0f}",
                "paid_claims": "{:,.0f}",
                "total_paid_real": "${:,.0f}",
                "frequency_per_1000": "{:.2f}",
                "loss_cost_real": "${:,.0f}",
                "relativity": "{:.2f}",
                "credibility_z": "{:.2f}",
                "paid_to_premium_proxy": "{:.2f}",
            },
        ),
        "",
        "## Severity by decade of loss",
        "",
        _md_table(
            dec,
            {
                "decade": "{:.0f}",
                "paid_claims": "{:,.0f}",
                "median_nominal": "${:,.0f}",
                "median_real": "${:,.0f}",
                "median_building_coverage_real": "${:,.0f}",
            },
        ),
        "",
        "## Building vs contents by occupancy",
        "",
        _md_table(
            bc,
            {
                "paid_claims": "{:,.0f}",
                "building_paid": "${:,.0f}",
                "contents_paid": "${:,.0f}",
                "building_share": "{:.1%}",
                "share_with_contents_payment": "{:.1%}",
                "share_with_contents_coverage": "{:.1%}",
            },
        ),
        "",
        "## Cycle time (date of loss to most recent payment), paid claims 2010-2025",
        "",
        _md_table(cyc, {"paid_claims": "{:,.0f}", "median_days": "{:.0f}", "p90_days": "{:.0f}"}),
        "",
        "## Repetitive loss (top 10 states)",
        "",
        _md_table(
            rl.head(10),
            {
                "multiple_loss_properties": "{:,.0f}",
                "nfip_repetitive_loss_properties": "{:,.0f}",
                "nfip_severe_repetitive_loss_properties": "{:,.0f}",
                "share_mitigated": "{:.1%}",
                "share_currently_insured": "{:.1%}",
                "avg_total_losses": "{:.2f}",
                "max_total_losses": "{:.0f}",
                "share_in_sfha": "{:.1%}",
            },
        ),
        "",
    ]
    (p.docs / "RESULTS.md").write_text("\n".join(md), encoding="utf-8")
    log.info("wrote %s figures and docs/RESULTS.md", len(list(p.figures.glob("*.png"))))
    return h


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    main()
