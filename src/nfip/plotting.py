"""Shared matplotlib styling (validated categorical palette, recessive axes, thin marks)."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
GRID = "#e4e3df"
# Categorical slots in fixed order (CVD-validated reference palette); first three are all-pairs safe.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
MUTED = "#b9b8b2"


def apply_style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "axes.edgecolor": GRID,
            "axes.labelcolor": TEXT_2,
            "axes.titlecolor": TEXT,
            "axes.titlesize": 12,
            "axes.titleweight": "bold",
            "axes.titlelocation": "left",
            "axes.labelsize": 10,
            "axes.grid": True,
            "axes.axisbelow": True,
            "axes.grid.axis": "y",
            "grid.color": GRID,
            "grid.linewidth": 0.8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.spines.left": False,
            "xtick.color": TEXT_2,
            "ytick.color": TEXT_2,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "legend.frameon": False,
            "legend.fontsize": 9,
            "font.family": "DejaVu Sans",
            "lines.linewidth": 2,
            "figure.dpi": 110,
        }
    )


def dollars(x: float, _pos=None) -> str:
    a = abs(x)
    if a >= 1e9:
        return f"${x / 1e9:,.0f}B" if a >= 1e10 else f"${x / 1e9:,.1f}B"
    if a >= 1e6:
        return f"${x / 1e6:,.0f}M"
    if a >= 1e3:
        return f"${x / 1e3:,.0f}k"
    return f"${x:,.0f}"


DOLLARS = FuncFormatter(dollars)


def source_note(fig, text: str) -> None:
    fig.text(0.01, 0.005, text, fontsize=7.5, color=TEXT_2, ha="left", va="bottom")
