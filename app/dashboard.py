"""Interactive NFIP loss dashboard (Streamlit).

Reads the exported star schema (``exports/powerbi/*.parquet``, produced by
``python -m nfip export-powerbi``) through an in-memory DuckDB connection, so the app needs
no warehouse file and the same tables back both this app and the Power BI model.

Run:  streamlit run app/dashboard.py
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

EXPORT_DIR = Path(os.environ.get("NFIP_EXPORT_DIR", Path(__file__).resolve().parents[1] / "exports" / "powerbi"))
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"

st.set_page_config(page_title="NFIP Flood Loss Explorer", layout="wide")


@st.cache_resource
def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect()
    for f in EXPORT_DIR.glob("*.parquet"):
        c.execute(f"create view {f.stem} as select * from read_parquet('{f.as_posix()}')")
    return c


def q(sql: str, params: list | None = None) -> pd.DataFrame:
    return con().execute(sql, params or []).df()


if not EXPORT_DIR.exists() or not any(EXPORT_DIR.glob("*.parquet")):
    st.error(f"No exported star schema found in {EXPORT_DIR}. Run `python -m nfip export-powerbi` first.")
    st.stop()

st.title("NFIP Flood Loss Explorer")
st.caption(
    "FEMA OpenFEMA NFIP Redacted Claims v3 + Policies v3 (redacted public data). Constant 2025 dollars use CPI-U (BLS via FRED). "
    "Paid/premium is a proxy, not a statutory loss ratio. Recent loss years are still developing."
)

states = q("select distinct state_abbr from dim_geography where member_source <> 'unknown_member' order by 1")["state_abbr"].tolist()
c1, c2, c3 = st.columns([2, 2, 3])
state_sel = c1.multiselect("States (empty = all)", states, default=[])
years = c2.slider("Loss years", 1978, 2026, (1990, 2025))
zone_sel = c3.multiselect(
    "Flood-zone group",
    ["A (1% annual chance)", "V (coastal high hazard)", "X/B/C (moderate-minimal)", "D (undetermined)", "Unknown"],
    default=[],
)

where = ["f.loss_year between ? and ?"]
params: list = [years[0], years[1]]
if state_sel:
    where.append(f"g.state_abbr in ({','.join('?' * len(state_sel))})")
    params += state_sel
if zone_sel:
    where.append(f"z.flood_zone_group in ({','.join('?' * len(zone_sel))})")
    params += zone_sel
W = " and ".join(where)
JOIN = "from fact_claims f join dim_geography g using (geography_key) join dim_flood_zone z using (flood_zone_key)"

k = q(
    f"""select count(*) filter (where f.is_paid_claim) as paid_claims, sum(f.total_paid) as paid,
               sum(f.total_paid_real) as paid_real, median(f.total_paid_real) filter (where f.is_paid_claim) as med_sev,
               sum(f.building_paid) / nullif(sum(f.building_paid + f.contents_paid), 0) as bshare
        {JOIN} where {W}""",
    params,
).iloc[0]
m1, m2, m3, m4 = st.columns(4)
m1.metric("Paid claims", f"{k.paid_claims:,.0f}")
m2.metric("Paid (constant 2025 $)", f"${(k.paid_real or 0) / 1e9:,.2f}B", help=f"Nominal ${(k.paid or 0) / 1e9:,.2f}B")
m3.metric("Median severity (2025 $)", f"${(k.med_sev or 0):,.0f}")
m4.metric("Building share of paid", f"{(k.bshare or 0):.0%}")

tab1, tab2, tab3, tab4 = st.tabs(["Trend", "Flood zones", "Events", "Frequency & loss cost"])

with tab1:
    d = q(
        f"select f.loss_year, sum(f.total_paid_real) as paid_real, sum(f.total_paid) as paid {JOIN} where {W} group by 1 order by 1", params
    )
    fig = px.bar(
        d,
        x="loss_year",
        y="paid_real",
        labels={"loss_year": "Year of loss", "paid_real": "Paid, constant 2025 $"},
        color_discrete_sequence=[BLUE],
        hover_data={"paid": ":$,.0f", "paid_real": ":$,.0f"},
    )
    st.plotly_chart(fig, use_container_width=True)

with tab2:
    d = q(
        f"""select z.flood_zone_group, count(*) filter (where f.is_paid_claim) as paid_claims,
                   sum(f.total_paid_real) / nullif(count(*) filter (where f.is_paid_claim), 0) as severity_real
            {JOIN} where {W} group by 1 order by paid_claims desc""",
        params,
    )
    a, b = st.columns(2)
    a.plotly_chart(
        px.bar(d, x="flood_zone_group", y="paid_claims", color_discrete_sequence=[BLUE], title="Paid claims"), use_container_width=True
    )
    b.plotly_chart(
        px.bar(d, x="flood_zone_group", y="severity_real", color_discrete_sequence=[BLUE], title="Severity (paid per paid claim, 2025 $)"),
        use_container_width=True,
    )

with tab3:
    d = q(
        f"""select e.event_label, e.event_type, count(*) filter (where f.is_paid_claim) as paid_claims,
                   sum(f.total_paid_real) as paid_real, sum(f.total_paid) as paid
            {JOIN} join dim_event e using (event_key) where {W} and e.event_type <> 'No event name'
            group by 1, 2 order by paid_real desc limit 20""",
        params,
    )
    fig = px.bar(
        d.sort_values("paid_real"),
        x="paid_real",
        y="event_label",
        orientation="h",
        color_discrete_sequence=[BLUE],
        labels={"paid_real": "Paid, constant 2025 $", "event_label": ""},
        hover_data=["event_type", "paid_claims"],
    )
    fig.update_layout(height=600)
    st.plotly_chart(fig, use_container_width=True)

with tab4:
    st.caption("Exposure = pro-rata earned policy-years from NFIP Policies v3; complete for calendar years 2010-2025 only.")
    sw = ["calendar_year between ? and ?", "is_complete_exposure_year"]
    sp: list = [max(years[0], 2010), min(years[1], 2025)]
    if state_sel:
        sw.append(f"state_abbr in ({','.join('?' * len(state_sel))})")
        sp += state_sel
    d = q(
        f"""select calendar_year, 1000.0 * sum(paid_claim_count) / sum(earned_policy_years) as frequency_per_1000,
                   sum(total_paid_real) / sum(earned_policy_years) as loss_cost_real,
                   sum(total_paid) / sum(earned_premium) as paid_to_premium_proxy
            from mart_loss_experience_state_year where {" and ".join(sw)} group by 1 order by 1""",
        sp,
    )
    labels = {
        "calendar_year": "Calendar year",
        "frequency_per_1000": "Paid claims / 1,000 policy-years",
        "paid_to_premium_proxy": "Paid / earned premium",
    }
    a, b = st.columns(2)
    a.plotly_chart(
        px.bar(
            d,
            x="calendar_year",
            y="frequency_per_1000",
            color_discrete_sequence=[BLUE],
            labels=labels,
            title="Paid claims per 1,000 earned policy-years",
        ),
        use_container_width=True,
    )
    b.plotly_chart(
        px.bar(
            d,
            x="calendar_year",
            y="paid_to_premium_proxy",
            color_discrete_sequence=[BLUE],
            labels=labels,
            title="Paid / earned premium (proxy)",
        ),
        use_container_width=True,
    )
    rel = q(
        """select state_abbr, flood_zone_group, earned_policy_years, paid_claim_count, frequency_per_1000, loss_cost_real,
                  raw_relativity, credibility_z, credibility_weighted_relativity
           from mart_loss_cost_relativity
           where flood_zone_group not in ('Unknown', 'D (undetermined)') and earned_policy_years >= 1000
           order by credibility_weighted_relativity desc"""
    )
    if state_sel:
        rel = rel[rel["state_abbr"].isin(state_sel)]
    st.subheader("Credibility-weighted loss-cost relativities (state x zone group, 2010-2025)")
    st.caption(
        "Relativity = cell loss cost / national loss cost for the same zone group; Z = min(1, sqrt(paid claims / 1,082)). "
        "Cells with < 1,000 earned policy-years hidden."
    )
    st.dataframe(
        rel,
        use_container_width=True,
        hide_index=True,
        column_config={
            "earned_policy_years": st.column_config.NumberColumn("Earned policy-years", format="localized"),
            "paid_claim_count": st.column_config.NumberColumn("Paid claims", format="localized"),
            "frequency_per_1000": st.column_config.NumberColumn("Freq / 1,000", format="%.1f"),
            "loss_cost_real": st.column_config.NumberColumn("Loss cost (2025 $)", format="dollar"),
            "raw_relativity": st.column_config.NumberColumn("Raw relativity", format="%.2f"),
            "credibility_z": st.column_config.NumberColumn("Credibility Z", format="%.2f"),
            "credibility_weighted_relativity": st.column_config.NumberColumn("Credibility-weighted", format="%.2f"),
        },
    )
