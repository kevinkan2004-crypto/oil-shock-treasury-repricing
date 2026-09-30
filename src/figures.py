"""
figures.py
核心圖一：滾動 beta 與歷史 10%–90% 區間（研究大綱 4.1）。

輸入：results/rolling_betas.csv、results/rolling_band.csv、data/episodes.csv、data/oil_bond_data.xlsx
輸出：figures/rolling_beta.png
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESULTS, FIGS = ROOT / "results", ROOT / "figures"

LABELS = {"dDGS2": "2-year", "dDGS10": "10-year"}


def rolling_beta_chart() -> Path:
    roll = pd.read_csv(RESULTS / "rolling_betas.csv", index_col=0, parse_dates=True)
    band = pd.read_csv(RESULTS / "rolling_band.csv", index_col=0)
    eps = pd.read_csv(ROOT / "data" / "episodes.csv", parse_dates=["start", "end"])
    eps = eps[eps.role.isin(["hist", "study"]) & ~eps.dropped_no_shock]

    raw = pd.read_excel(ROOT / "data" / "oil_bond_data.xlsx", sheet_name="data",
                        parse_dates=["date"]).set_index("date").sort_index()
    brent = raw["DCOILBRENTEU"].dropna().loc[roll.index[0]:]

    fig, axes = plt.subplots(3, 1, figsize=(11, 9.5), sharex=True,
                             gridspec_kw={"height_ratios": [1, 1, 0.7]})
    for ax, y in zip(axes[:2], LABELS):
        b = band.loc[y]
        ax.axhspan(b.p10, b.p90, color="0.88", label="Historical 10th–90th pct (ex ZLB, ex 2026)")
        ax.axhline(0, color="0.5", lw=0.6)
        for _, e in eps.iterrows():
            color = "#c0392b" if e.role == "study" else "#e8a33d"
            ax.axvspan(e.start, e.end, color=color, alpha=0.25, lw=0)
        ax.plot(roll.index, roll[y], color="#1f3864", lw=1)
        ax.set_title(f"{LABELS[y]} Treasury: rolling 1-year sensitivity to Brent", loc="left", fontsize=11)
        ax.set_ylabel("bp per 10% oil move")
    # 第三格：Brent 價格，陰影與上方一致，方便對照衝擊期間的油價走勢
    ax = axes[2]
    for _, e in eps.iterrows():
        color = "#c0392b" if e.role == "study" else "#e8a33d"
        ax.axvspan(e.start, e.end, color=color, alpha=0.25, lw=0)
    ax.plot(brent.index, brent, color="0.35", lw=0.9)
    ax.set_title("Brent crude spot price", loc="left", fontsize=11)
    ax.set_ylabel("USD per barrel")

    axes[0].legend(loc="upper left", fontsize=8, frameon=False)
    axes[2].text(0.01, 0.88, "Shaded: orange = historical supply shocks, red = 2026 US–Iran war",
                 transform=axes[2].transAxes, fontsize=8, color="0.3")
    fig.tight_layout()
    FIGS.mkdir(exist_ok=True)
    out = FIGS / "rolling_beta.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


if __name__ == "__main__":
    print(rolling_beta_chart())
