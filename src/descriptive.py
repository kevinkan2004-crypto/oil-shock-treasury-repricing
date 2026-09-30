"""
descriptive.py
描述性分析（研究大綱第三部分）。

    1. 各時期的日變動統計：平均、標準差、最小、最大、樣本數
    2. 各時期油價與殖利率的相關係數
    3. 各衝擊期間的累積變動：Brent、2 年期、10 年期
    4. 長期時間序列圖：Brent 與 2 年、10 年期殖利率，標出衝擊期間

輸入：data/oil_bond_data.xlsx、data/prepared.csv、data/episodes.csv
輸出：results/descriptive_*.csv、figures/long_run.png
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESULTS, FIGS = ROOT / "results", ROOT / "figures"

VARS = {"dDGS2": "2y (bp)", "dDGS10": "10y (bp)", "dOil": "Brent (%)", "dEq": "NASDAQ (%)"}


def periods(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "Full sample": pd.Series(True, index=df.index),
        "Normal (ex ZLB, ex shocks)": (df.DZLB == 0) & (df.Dhist == 0) & (df.D2026 == 0),
        "ZLB": df.DZLB == 1,
        "Historical shocks": df.Dhist == 1,
        "2026 war": df.D2026 == 1,
    }


def summary_by_period(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, mask in periods(df).items():
        sub = df[mask]
        for col, label in VARS.items():
            s = sub[col].dropna()
            rows.append({"period": name, "variable": label, "N": len(s), "mean": s.mean(),
                         "sd": s.std(), "min": s.min(), "max": s.max()})
    return pd.DataFrame(rows)


def correlations(df: pd.DataFrame) -> pd.DataFrame:
    """同日相關與「當天 + 隔天」油價合計的相關（對應 Dimson 修正）。"""
    rows = []
    oil2 = df["dOil"] + df["dOil_lead"]
    for name, mask in periods(df).items():
        sub = df[mask]
        rows.append({
            "period": name,
            "corr(2y, oil)": sub["dDGS2"].corr(sub["dOil"]),
            "corr(10y, oil)": sub["dDGS10"].corr(sub["dOil"]),
            "corr(2y, oil t+t+1)": sub["dDGS2"].corr(oil2[mask]),
            "corr(10y, oil t+t+1)": sub["dDGS10"].corr(oil2[mask]),
            "corr(oil, NASDAQ)": sub["dOil"].corr(sub["dEq"]),
        })
    return pd.DataFrame(rows).set_index("period")


def episode_summary(raw: pd.DataFrame, eps: pd.DataFrame) -> pd.DataFrame:
    """每段期間從基準日（觸發前最後一個交易日）到結束日的累積變動。"""
    rows = []
    for _, e in eps.iterrows():
        pre = raw.loc[:e.start - pd.Timedelta(days=1)].dropna(subset=["DCOILBRENTEU", "DGS2", "DGS10"]).iloc[-1]
        win = raw.loc[e.start:e.end].dropna(subset=["DCOILBRENTEU", "DGS2", "DGS10"])
        end = win.iloc[-1]
        rows.append({
            "episode": e["name"], "start": e.start.date(), "end": e.end.date(),
            "trading days": len(win),
            "Brent start": pre.DCOILBRENTEU, "Brent end": end.DCOILBRENTEU,
            "Brent change (%)": (end.DCOILBRENTEU / pre.DCOILBRENTEU - 1) * 100,
            "Brent peak (%)": (win.DCOILBRENTEU.max() / pre.DCOILBRENTEU - 1) * 100,
            "2y change (bp)": (end.DGS2 - pre.DGS2) * 100,
            "10y change (bp)": (end.DGS10 - pre.DGS10) * 100,
        })
    return pd.DataFrame(rows).set_index("episode")


def long_run_chart(raw: pd.DataFrame, eps: pd.DataFrame) -> Path:
    fig, ax1 = plt.subplots(figsize=(11, 5))
    ax2 = ax1.twinx()
    for _, e in eps.iterrows():
        color = "#c0392b" if e.role == "study" else "#e8a33d"
        ax1.axvspan(e.start, e.end, color=color, alpha=0.25, lw=0)
    # 假日缺值會讓線段斷開，逐條序列去除缺值後再畫
    s2, s10, br = (raw[c].dropna() for c in ["DGS2", "DGS10", "DCOILBRENTEU"])
    ax1.plot(s2.index, s2, color="#2e5597", lw=0.9, label="2y yield")
    ax1.plot(s10.index, s10, color="#1f3864", lw=0.9, ls="--", label="10y yield")
    ax2.plot(br.index, br, color="0.55", lw=0.8, label="Brent (rhs)")
    ax1.set_ylabel("Yield (%)")
    ax2.set_ylabel("Brent (USD/bbl)")
    ax1.set_title("Brent and Treasury yields, supply-shock episodes shaded", loc="left", fontsize=11)
    lines = ax1.get_lines() + ax2.get_lines()
    ax1.legend(lines, [l.get_label() for l in lines], loc="upper right", fontsize=8, frameon=False)
    ax1.text(0.01, 0.02, "Orange = historical supply shocks, red = 2026 US–Iran war",
             transform=ax1.transAxes, fontsize=8, color="0.3")
    fig.tight_layout()
    FIGS.mkdir(exist_ok=True)
    out = FIGS / "long_run.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def main() -> None:
    raw = pd.read_excel(ROOT / "data" / "oil_bond_data.xlsx", sheet_name="data",
                        parse_dates=["date"]).set_index("date").sort_index()
    df = pd.read_csv(ROOT / "data" / "prepared.csv", index_col=0, parse_dates=True)
    eps = pd.read_csv(ROOT / "data" / "episodes.csv", parse_dates=["start", "end"])
    eps = eps[eps.role.isin(["hist", "study"]) & ~eps.dropped_no_shock]

    RESULTS.mkdir(exist_ok=True)
    stats = summary_by_period(df)
    corr = correlations(df)
    ep = episode_summary(raw, eps)
    stats.to_csv(RESULTS / "descriptive_by_period.csv", index=False)
    corr.to_csv(RESULTS / "descriptive_correlations.csv")
    ep.to_csv(RESULTS / "descriptive_episodes.csv")
    chart = long_run_chart(raw.loc[df.index[0]:], eps)

    pd.set_option("display.width", 200)
    print("== 各時期日變動統計 ==")
    print(stats.pivot(index="period", columns="variable", values="sd").round(2).to_string())
    print("\n== 相關係數 ==")
    print(corr.round(2).to_string())
    print("\n== 各衝擊期間累積變動 ==")
    print(ep.round(1).to_string())
    print(f"\n圖：{chart}")


if __name__ == "__main__":
    main()
