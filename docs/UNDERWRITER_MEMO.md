# Memo: what 2.7M NFIP flood claims say about writing flood risk

**To:** Personal-lines / flood underwriting\
**From:** Ali Askari (portfolio analysis of public FEMA data)\
**Data:** FEMA OpenFEMA NFIP Redacted Claims v3 (2,725,989 claims, 1978-2026) and Policies v3
(66.1M earned policy-years, 2010-2025), retrieved 2026-10-10; CPI-U (BLS via FRED). Dollars are
constant 2025 dollars. Paid-to-premium is a **proxy** (undeveloped paid, no LAE, premium excl. fees),
not a loss ratio. Reproduce: `python -m nfip analyze` -> `docs/RESULTS.md`.

### 1. Outside the high-risk zone is a quarter of the losses
Zones X/B/C/D (outside the SFHA, where flood insurance is optional) held **45% of earned
policy-years** and produced **25% of paid claims and 24% of paid dollars** (2010-2025). Their loss
cost is **$521 per policy-year** vs **$1,348 in A zones** and **$1,899 in V zones**; the gap is almost
entirely frequency (6.7 vs 16.1 paid claims per 1,000) - once a non-SFHA home floods, the claim is
nearly as large ($78k vs $84k).
**Recommendation:** treat non-SFHA flood as a priced, growable line, not "preferred = safe".
Frequency, not severity, should carry the zone differential in rating.

### 2. Severity is trending ~2.8% a year faster than inflation
Median real paid claim went from about $10.7k (1980s) to $45.5k (2020s). Controlling for zone,
coverage, occupancy and building characteristics, a Gamma GLM fitted on 2000-2019 losses
estimates a **+2.8% per year real severity trend**. Without it the model under-predicted 2020-2025
claims by ~26% (A/E 1.26); with it, overall A/E is 0.95.
**Recommendation:** trend severity explicitly in rate indications and coverage adequacy reviews;
CPI-only indexing of limits or rates will fall behind.

### 3. Results are driven by a handful of catastrophe years and places
Paid/premium (proxy) averaged 0.94 over 2010-2025, but exceeded 1.0 in five years (2012 Sandy 2.91,
2017 Harvey 3.24, 2024 Helene/Milton 2.37, 2022 Ian 1.54, 2016 Louisiana floods 1.35) and ran
0.11-0.56 in quiet years. The top five named events since 1978 carry **47% of all real paid dollars**.
Per policy, New Jersey and New York run **3.0-3.1x the national loss cost**; Texas is the largest book
($15.7B real paid, 1.5x national).
**Recommendation:** manage accumulation by storm footprint (Gulf and Northeast coasts) and price a
separate catastrophe load; never judge flood profitability on a single year. Use credibility
weighting (built into `mart_loss_cost_relativity`) so thin states do not swing indications.

### 4. Repetitive-loss properties are barely mitigated
FEMA's Multiple Loss Properties list has **240,651 properties**; only **5.5% are mitigated** and
76.8% sit in the SFHA. **Recommendation:** for any book with repeat-loss exposure, tie renewal terms
to mitigation (elevation, flood vents, ICC-funded work) and flag SRL properties at underwriting.

### What this analysis cannot tell you
Claims cannot be joined to policies (FEMA redaction), so frequency and paid/premium are aggregate
ratios by county, year, zone and occupancy. 2024-2026 losses are still developing. Severity model:
out-of-time normalized Gini 0.43 (GLM) / 0.57 (gradient boosting) - good for segmentation, but
year-level calibration swings with storm mix (A/E 0.50-1.43 by year), so it is not a rate filing.
