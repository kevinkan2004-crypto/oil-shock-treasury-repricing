"""
robustness.py
穩健性檢驗（研究大綱第六部分）。

目前包含：
    1. 週資料（週五對週五）：
       a. 主要迴歸：2026 vs 歷史衝擊期間
       b. 結構性斷點檢定（sup-Wald，開戰前樣本）
       c. 以斷點後、開戰前的平常時期作為 2026 的比較基準
    2. 排除 FOMC 聲明日與 CPI 公布日（日資料）：
       同樣的 a、b、c，看總經消息日是否灌高了近期的油價敏感度

週資料讓 Brent（倫敦時段）與美債（紐約收盤）的收盤時間差只影響一週的頭尾，
因此不再加入隔週油價（Dimson）項。

輸入：data/oil_bond_data.xlsx、data/prepared.csv、data/fomc_dates.csv、data/cpi_release_dates.csv
輸出：results/robust_weekly_*.csv、results/robust_events_*.csv
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from prepare import ZLB_PERIODS, build_episodes, load_raw
from regressions import (BREAK_SAMPLE, BREAK_TRIM, SCALE, break_test, main_regressions, ols_hac,
                         regime_robustness, supwald_critical_values)

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"

WEEKLY_HAC_LAGS = 8
FOMC_PATH = ROOT / "data" / "fomc_dates.csv"
CPI_PATH = ROOT / "data" / "cpi_release_dates.csv"
REGIME_START = "2022-03-17"     # 經濟理由：聯準會首次升息隔天、零利率結束
SERIES = ["DGS2", "DGS10", "DCOILBRENTEU", "NASDAQCOM", "ACMRNY10", "ACMTP10", "ACMY10"]


# ----------------------------------------------------------------------------
# 週資料
# ----------------------------------------------------------------------------
def weekly_panel(raw: pd.DataFrame, episodes: pd.DataFrame) -> pd.DataFrame:
    """週五收盤（若週五休市則取該週最後一個有資料的交易日）。"""
    core = raw[SERIES].dropna(subset=["DGS2", "DGS10", "DCOILBRENTEU", "NASDAQCOM"])
    wk = core.resample("W-FRI").last()
    wk = wk[core["DGS2"].resample("W-FRI").count() > 0]      # 去掉整週休市

    out = pd.DataFrame(index=wk.index)
    for col in ["DGS2", "DGS10", "ACMRNY10", "ACMTP10", "ACMY10"]:
        out[f"d{col}"] = wk[col].diff() * 100
    out["dOil"] = np.log(wk["DCOILBRENTEU"]).diff() * 100
    out["dEq"] = np.log(wk["NASDAQCOM"]).diff() * 100

    # 一週（上週五之後到本週五）只要與期間重疊就標記為 1
    week_start = out.index - pd.Timedelta(days=6)

    def overlap(start, end) -> np.ndarray:
        return (out.index >= pd.Timestamp(start)) & (week_start <= pd.Timestamp(end))

    hist = episodes[(episodes.role == "hist") & ~episodes.dropped_no_shock]
    out["D2026"] = 0
    for _, r in episodes[episodes.role == "study"].iterrows():
        out.loc[overlap(r.start, r.end), "D2026"] = 1
    for col, rows in [("Dhist", hist), ("Dhist_short", hist[~hist.exclude_short_end])]:
        out[col] = 0
        for _, r in rows.iterrows():
            out.loc[overlap(r.start, r.end), col] = 1
    out["DZLB"] = 0
    for a, b in ZLB_PERIODS:
        out.loc[overlap(a, b), "DZLB"] = 1
    return out.iloc[1:]


def weekly_design(w: pd.DataFrame, hist: str) -> pd.DataFrame:
    X = pd.DataFrame(index=w.index)
    X["const"] = 1.0
    X["D2026"], X["Dhist"], X["DZLB"] = w["D2026"], w[hist], w["DZLB"]
    X["oil"] = w["dOil"]
    X["oil_x2026"] = w["dOil"] * w["D2026"]
    X["oil_xhist"] = w["dOil"] * w[hist]
    X["oil_xzlb"] = w["dOil"] * w["DZLB"]
    X["dEq"] = w["dEq"]
    return X


def _row(res, label: str, weights: dict[str, float], **extra) -> dict:
    est, se = res.lincom(weights)
    return {**extra, "quantity": label, "bp/10%": est * SCALE, "SE": se * SCALE,
            "95% CI low": (est - 1.96 * se) * SCALE, "95% CI high": (est + 1.96 * se) * SCALE,
            "p one-sided (>0)": 1 - stats.norm.cdf(est / se)}


SPECS = [("dDGS2", "Dhist_short"), ("dDGS10", "Dhist"), ("dACMRNY10", "Dhist"), ("dACMTP10", "Dhist")]


# ----------------------------------------------------------------------------
# a. 主要迴歸
# ----------------------------------------------------------------------------
def weekly_main(w: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for y, hist in SPECS:
        res = ols_hac(w[y], weekly_design(w, hist), lags=WEEKLY_HAC_LAGS)
        for label, wts in {
            "Normal": {"oil": 1},
            "Historical shocks": {"oil": 1, "oil_xhist": 1},
            "2026": {"oil": 1, "oil_x2026": 1},
            "2026 excess vs hist": {"oil_x2026": 1, "oil_xhist": -1},
        }.items():
            rows.append(_row(res, label, wts, dependent=y, nobs=res.nobs))
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# b. 斷點檢定（1 個受檢定參數：週資料只有當週油價項）
# ----------------------------------------------------------------------------
def weekly_break(w: pd.DataFrame, y: str, hist: str) -> pd.DataFrame:
    d = w.loc[BREAK_SAMPLE[0]:BREAK_SAMPLE[1]].dropna(subset=[y, "dOil", "dEq"])
    n = len(d)
    X0 = weekly_design(d, hist).drop(columns=["D2026", "oil_x2026"])   # 開戰前樣本沒有 2026
    rows = []
    for k in range(int(n * BREAK_TRIM), int(n * (1 - BREAK_TRIM))):
        tau = d.index[k]
        post = (d.index >= tau).astype(float)
        X = X0.copy()
        X["DPOST"], X["oil_xpost"] = post, d["dOil"] * post
        res = ols_hac(d[y], X, lags=WEEKLY_HAC_LAGS)
        b, v = res.params["oil_xpost"], res.cov.loc["oil_xpost", "oil_xpost"]
        rows.append({"date": tau, "wald": b * b / v, "shift (bp/10%)": b * SCALE})
    return pd.DataFrame(rows).set_index("date")


# ----------------------------------------------------------------------------
# c. 斷點後的比較
# ----------------------------------------------------------------------------
def weekly_regime(w: pd.DataFrame, start: str, source: str) -> pd.DataFrame:
    post = (w.index >= start).astype(int)
    rows = []
    for y, hist in SPECS:
        X = weekly_design(w, hist)
        X["DPOST"], X["oil_xpost"] = post, w["dOil"] * post
        res = ols_hac(w[y], X, lags=WEEKLY_HAC_LAGS)
        for label, wts in {
            "pre-break normal": {"oil": 1},
            "post-break normal": {"oil": 1, "oil_xpost": 1},
            "2026": {"oil": 1, "oil_xpost": 1, "oil_x2026": 1},
            "2026 vs post-break normal": {"oil_x2026": 1},
        }.items():
            rows.append(_row(res, label, wts, dependent=y, **{"break date": start, "break from": source}))
    return pd.DataFrame(rows)


def run_weekly() -> None:
    raw = load_raw()
    episodes = build_episodes(raw["DCOILBRENTEU"])
    w = weekly_panel(raw, episodes)

    main_tab = weekly_main(w)

    cv = supwald_critical_values(p=1)
    brk_rows, paths = [], {}
    for y, hist in [("dDGS2", "Dhist_short"), ("dDGS10", "Dhist")]:
        path = weekly_break(w, y, hist)
        paths[y] = path["wald"]
        top = path["wald"].idxmax()
        sup = path.loc[top, "wald"]
        brk_rows.append({"dependent": y, "sup-Wald": sup, "p-value": (cv["draws"] >= sup).mean(),
                         "break date": top.date(), "shift (bp/10%)": path.loc[top, "shift (bp/10%)"],
                         "cv 10%": cv["cv10"], "cv 5%": cv["cv5"], "cv 1%": cv["cv1"]})
    brk = pd.DataFrame(brk_rows).set_index("dependent")

    weekly_date = str(brk.loc["dDGS2", "break date"])
    regime = pd.concat([weekly_regime(w, weekly_date, "weekly test (2y)"),
                        weekly_regime(w, "2024-05-01", "daily test (2y)")])

    main_tab.to_csv(RESULTS / "robust_weekly_main.csv", index=False)
    brk.to_csv(RESULTS / "robust_weekly_break.csv")
    pd.DataFrame(paths).to_csv(RESULTS / "robust_weekly_break_path.csv")
    regime.to_csv(RESULTS / "robust_weekly_regime.csv", index=False)

    pd.set_option("display.width", 220)
    print(f"週資料：{len(w):,} 週，{w.index[0].date()} ~ {w.index[-1].date()}")
    print("\n== 週資料 a. 主要迴歸（bp/10%）==")
    print(main_tab.round(2).to_string(index=False))
    print("\n== 週資料 b. 斷點檢定 ==")
    print(brk.round(3).to_string())
    print("\n== 週資料 c. 2026 vs 斷點後平常時期 ==")
    print(regime[regime.quantity == "2026 vs post-break normal"].round(2).to_string(index=False))


# ----------------------------------------------------------------------------
# 2. 排除 FOMC 與 CPI 公布日（日資料）
# ----------------------------------------------------------------------------
def load_event_days(index: pd.DatetimeIndex) -> dict[str, pd.DatetimeIndex]:
    """
    讀取事件日期並對應到資料中的交易日（週末公布者對應到下一個交易日）。
    FOMC 只排除有對外公布的日子：1994 年 2 月起的定期會議與臨時動作，不含 pre1994。
    """
    events = {}
    if FOMC_PATH.exists():
        f = pd.read_csv(FOMC_PATH)
        f = f[f["type"].isin(["scheduled", "unscheduled"])]
        events["FOMC"] = pd.to_datetime(f["date"])
    else:
        print(f"注意：找不到 {FOMC_PATH.name}，略過 FOMC 排除")
    if CPI_PATH.exists():
        events["CPI"] = pd.to_datetime(pd.read_csv(CPI_PATH)["date"])
    else:
        print(f"注意：找不到 {CPI_PATH.name}，略過 CPI 排除（請先執行 fetch_data.py）")

    out = {}
    for name, dates in events.items():
        pos = index.searchsorted(dates)            # 第一個 >= 公布日的交易日
        pos = pos[pos < len(index)]
        out[name] = pd.DatetimeIndex(index[pos]).unique()
    if len(out) == 2:
        out["FOMC + CPI"] = out["FOMC"].union(out["CPI"])
    return out


def run_event_exclusion() -> None:
    df = pd.read_csv(ROOT / "data" / "prepared.csv", index_col=0, parse_dates=True)
    events = load_event_days(df.index)
    if not events:
        return
    variants = {"None (baseline)": df}
    for name, days in events.items():
        variants[f"Excl. {name}"] = df.drop(index=days, errors="ignore")

    keep = ["dDGS2", "dDGS10", "dACMRNY10", "dACMTP10"]
    main_rows, regime_rows, brk_rows = [], [], []
    cv = supwald_critical_values()
    for label, d in variants.items():
        dropped = len(df) - len(d)
        tab, _ = main_regressions(d)
        for y in keep:
            r = tab.loc[y]
            main_rows.append({"exclusion": label, "days dropped": dropped, "dependent": y,
                              "2026 excess vs hist (bp/10%)": r["2026 excess vs hist (bp/10%)"],
                              "95% CI low": r["excess 95% CI low"], "95% CI high": r["excess 95% CI high"],
                              "p one-sided (>0)": r["p one-sided (2026 > hist)"]})
        reg = regime_robustness(d, REGIME_START)
        reg.insert(0, "exclusion", label)
        regime_rows.append(reg)
        # 斷點檢定較耗時，只對基準與全部排除兩種情況執行
        if label in ("None (baseline)", "Excl. FOMC + CPI") or len(variants) == 2:
            for y, hist in [("dDGS2", "Dhist_short"), ("dACMRNY10", "Dhist")]:
                path = break_test(d, y, hist)
                top = path["wald"].idxmax()
                sup = path.loc[top, "wald"]
                brk_rows.append({"exclusion": label, "dependent": y, "sup-Wald": sup,
                                 "p-value": (cv["draws"] >= sup).mean(), "break date": top.date(),
                                 "shift (bp/10%)": path.loc[top, "shift (bp/10%)"]})

    main_tab = pd.DataFrame(main_rows)
    regime = pd.concat(regime_rows)
    brk = pd.DataFrame(brk_rows)
    main_tab.to_csv(RESULTS / "robust_events_main.csv", index=False)
    regime.to_csv(RESULTS / "robust_events_regime.csv", index=False)
    brk.to_csv(RESULTS / "robust_events_break.csv", index=False)

    pd.set_option("display.width", 220)
    print("\n== 排除事件日 a. 2026 vs 歷史衝擊（bp/10%）==")
    print(main_tab.round(2).to_string(index=False))
    print(f"\n== 排除事件日 b. 以 {REGIME_START} 為新常態起點 ==")
    show = regime[regime.quantity.isin(["pre-break normal", "post-break normal", "2026 vs post-break normal"])]
    print(show[["exclusion", "dependent", "quantity", "bp/10%", "95% CI low", "95% CI high",
                "p one-sided (>0)"]].round(2).to_string(index=False))
    print("\n== 排除事件日 c. 斷點檢定（開戰前樣本）==")
    print(brk.round(3).to_string(index=False))


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    run_weekly()
    run_event_exclusion()


if __name__ == "__main__":
    main()
