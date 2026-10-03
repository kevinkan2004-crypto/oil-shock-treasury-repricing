"""
regressions.py
主要迴歸分析（研究大綱第四部分）。

    1. 主要迴歸：DGS2、DGS10（DGS30 與曲線斜率為附帶結果）
    2. 10 年期 ACM 拆解：ACMY10、ACMRNY10、ACMTP10
    3. 各期間 beta（描述性）
    4. 滾動 beta（252 日視窗）
    5. 新常態比較：以 2022-03-17 為主要起點；結構性斷點檢定（sup-Wald）與斷點日期版本作為輔助證據

標準誤：Newey-West（Bartlett kernel，10 期落後，含 n/(n-k) 小樣本修正）。
OLS 以 numpy 實作，避免額外套件依賴；結果與 statsmodels 的
OLS(...).fit(cov_type="HAC", cov_kwds={"maxlags": 10}) 一致。

輸入：data/prepared.csv（由 prepare.py 產生）
輸出：results/main/*.csv
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
PREPARED = ROOT / "data" / "prepared.csv"
RESULTS = ROOT / "results" / "main"

HAC_LAGS = 10
ROLL_WINDOW = 252
SCALE = 10          # 報告單位：油價漲 10%，殖利率變動幾 bp


# ----------------------------------------------------------------------------
# OLS + Newey-West
# ----------------------------------------------------------------------------
@dataclass
class OLSResult:
    params: pd.Series
    cov: pd.DataFrame
    nobs: int

    @property
    def bse(self) -> pd.Series:
        return np.sqrt(pd.Series(np.diag(self.cov), index=self.params.index))

    def lincom(self, weights: dict[str, float]) -> tuple[float, float]:
        """線性組合 w'β 的估計值與標準誤。"""
        w = pd.Series(0.0, index=self.params.index)
        for k, v in weights.items():
            w[k] = v
        est = float(w @ self.params)
        se = float(np.sqrt(w @ self.cov @ w))
        return est, se


def ols_hac(y: pd.Series, X: pd.DataFrame, lags: int = HAC_LAGS) -> OLSResult:
    data = pd.concat([y, X], axis=1).dropna()
    yv, Xv = data.iloc[:, 0].to_numpy(), data.iloc[:, 1:].to_numpy()
    n, k = Xv.shape
    XtX_inv = np.linalg.pinv(Xv.T @ Xv)
    beta = XtX_inv @ Xv.T @ yv
    u = yv - Xv @ beta
    Xu = Xv * u[:, None]
    S = Xu.T @ Xu
    for l in range(1, lags + 1):
        w = 1 - l / (lags + 1)
        G = Xu[l:].T @ Xu[:-l]
        S += w * (G + G.T)
    cov = XtX_inv @ S @ XtX_inv * n / (n - k)
    cols = X.columns
    return OLSResult(pd.Series(beta, index=cols), pd.DataFrame(cov, index=cols, columns=cols), n)


# ----------------------------------------------------------------------------
# 設計矩陣
# ----------------------------------------------------------------------------
def design(df: pd.DataFrame, hist_col: str = "Dhist", oil: str = "dOil") -> pd.DataFrame:
    """
    Δy = α + δ1·D2026 + δ2·Dhist + δ3·DZLB
           + Σ_{k∈{a,b}} [β0k·Oil_k + β1k·Oil_k·D2026 + β2k·Oil_k·Dhist + β3k·Oil_k·DZLB]
           + γ·ΔEq
    a = 當天油價，b = 隔天油價（Dimson 修正）
    """
    X = pd.DataFrame(index=df.index)
    X["const"] = 1.0
    X["D2026"], X["Dhist"], X["DZLB"] = df["D2026"], df[hist_col], df["DZLB"]
    for tag, col in [("a", oil), ("b", f"{oil}_lead")]:
        X[f"oil_{tag}"] = df[col]
        X[f"oil_{tag}_x2026"] = df[col] * df["D2026"]
        X[f"oil_{tag}_xhist"] = df[col] * df[hist_col]
        X[f"oil_{tag}_xzlb"] = df[col] * df["DZLB"]
    X["dEq"] = df["dEq"]
    return X


SENSITIVITIES = {
    "Normal":            {"oil_a": 1, "oil_b": 1},
    "Historical shocks": {"oil_a": 1, "oil_b": 1, "oil_a_xhist": 1, "oil_b_xhist": 1},
    "2026":              {"oil_a": 1, "oil_b": 1, "oil_a_x2026": 1, "oil_b_x2026": 1},
    "ZLB":               {"oil_a": 1, "oil_b": 1, "oil_a_xzlb": 1, "oil_b_xzlb": 1},
}
EXCESS = {"oil_a_x2026": 1, "oil_b_x2026": 1, "oil_a_xhist": -1, "oil_b_xhist": -1}


def summarize(name: str, res: OLSResult) -> dict:
    row = {"dependent": name, "nobs": res.nobs}
    for label, w in SENSITIVITIES.items():
        est, se = res.lincom(w)
        row[f"{label} (bp/10%)"] = est * SCALE
        row[f"{label} SE"] = se * SCALE
    est, se = res.lincom(EXCESS)
    t = est / se
    row["2026 excess vs hist (bp/10%)"] = est * SCALE
    row["excess SE"] = se * SCALE
    row["excess 95% CI low"] = (est - 1.96 * se) * SCALE
    row["excess 95% CI high"] = (est + 1.96 * se) * SCALE
    row["t"] = t
    row["p one-sided (2026 > hist)"] = 1 - stats.norm.cdf(t)
    return row


# ----------------------------------------------------------------------------
# 分析
# ----------------------------------------------------------------------------
def main_regressions(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, OLSResult]]:
    specs = [
        ("dDGS2", "Dhist_short"),   # 短端排除與零利率重疊的 2011
        ("dDGS10", "Dhist"),
        ("dDGS30", "Dhist"),
        ("dSlope_10_2", "Dhist"),
        ("dACMY10", "Dhist"),
        ("dACMRNY10", "Dhist"),
        ("dACMTP10", "Dhist"),
    ]
    rows, fits = [], {}
    for y, hist in specs:
        res = ols_hac(df[y], design(df, hist))
        fits[y] = res
        rows.append({**summarize(y, res), "hist dummy": hist})
    return pd.DataFrame(rows).set_index("dependent"), fits


# ----------------------------------------------------------------------------
# 結構性斷點檢定（sup-Wald，Andrews 1993）與新常態比較
# ----------------------------------------------------------------------------
BREAK_SAMPLE = ("2016-01-01", "2026-02-27")   # 第一段零利率結束後至開戰前最後一個交易日
BREAK_TRIM = 0.15                             # 頭尾各排除 15%
BREAK_REF = ("dDGS2", "Dhist_short")          # 以 2 年期找出的斷點作為輔助比較基準

# 新常態的主要起點：依經濟理由定義（聯準會首次升息隔天、零利率結束），不由資料估計。
# 斷點檢定挑的是讓前後差異最大的日期，用它當基準會高估斷點後的敏感度，
# 使比較偏向「沒有過度反應」，因此斷點日期只作為輔助證據。
REGIME_START = "2022-03-17"


def _break_design(d: pd.DataFrame, hist: str) -> pd.DataFrame:
    X = pd.DataFrame(index=d.index)
    X["const"] = 1.0
    X["Dhist"], X["DZLB"] = d[hist], d["DZLB"]
    for tag, col in [("a", "dOil"), ("b", "dOil_lead")]:
        X[f"oil_{tag}"] = d[col]
        X[f"oil_{tag}_xhist"] = d[col] * d[hist]
        X[f"oil_{tag}_xzlb"] = d[col] * d["DZLB"]
    X["dEq"] = d["dEq"]
    return X


def supwald_critical_values(p: int = 2, trim: float = BREAK_TRIM, reps: int = 20000,
                            grid: int = 1000, seed: int = 1) -> dict[float, float]:
    """以模擬 Brownian bridge 取得 sup-Wald 的臨界值（p 個受檢定參數）。"""
    rng = np.random.default_rng(seed)
    t = np.arange(1, grid + 1) / grid
    lo, hi = int(grid * trim), int(grid * (1 - trim))
    sups = []
    for _ in range(reps // 1000):
        W = (rng.standard_normal((1000, grid, p)) / np.sqrt(grid)).cumsum(axis=1)
        B = W - t[None, :, None] * W[:, -1:, :]
        stat = (B[:, lo:hi] ** 2).sum(axis=2) / (t[lo:hi] * (1 - t[lo:hi]))[None, :]
        sups.append(stat.max(axis=1))
    sups = np.concatenate(sups)
    return {"cv10": np.quantile(sups, 0.90), "cv5": np.quantile(sups, 0.95),
            "cv1": np.quantile(sups, 0.99), "draws": sups}


def break_test(df: pd.DataFrame, y: str, hist: str, sample: tuple[str, str] = BREAK_SAMPLE,
               trim: float = BREAK_TRIM) -> pd.DataFrame:
    """
    逐日嘗試切點 τ，檢定油價敏感度（當天 + 隔天兩個交乘項）在 τ 前後是否不同。
    回傳每個切點的 Wald 統計量與斷點後的敏感度變化（bp/10%）。
    """
    d = df.loc[sample[0]:sample[1]].dropna(subset=[y, "dOil", "dOil_lead", "dEq"])
    n = len(d)
    X0 = _break_design(d, hist)
    keys = ["oil_a_xpost", "oil_b_xpost"]
    rows = []
    for k in range(int(n * trim), int(n * (1 - trim))):
        tau = d.index[k]
        post = (d.index >= tau).astype(float)
        X = X0.copy()
        X["DPOST"] = post
        X["oil_a_xpost"], X["oil_b_xpost"] = d["dOil"] * post, d["dOil_lead"] * post
        res = ols_hac(d[y], X)
        b = res.params[keys].to_numpy()
        V = res.cov.loc[keys, keys].to_numpy()
        rows.append({"date": tau, "wald": float(b @ np.linalg.solve(V, b)), "shift (bp/10%)": b.sum() * SCALE})
    return pd.DataFrame(rows).set_index("date")


def break_summary(paths: dict[str, pd.DataFrame], cv: dict) -> pd.DataFrame:
    rows = []
    for y, path in paths.items():
        top = path["wald"].idxmax()
        sup = path.loc[top, "wald"]
        rows.append({"dependent": y, "sup-Wald": sup, "p-value": (cv["draws"] >= sup).mean(),
                     "break date": top.date(), "shift (bp/10%)": path.loc[top, "shift (bp/10%)"],
                     "cv 10%": cv["cv10"], "cv 5%": cv["cv5"], "cv 1%": cv["cv1"],
                     "search from": path.index[0].date(), "search to": path.index[-1].date()})
    return pd.DataFrame(rows).set_index("dependent")


def regime_robustness(df: pd.DataFrame, start: str = REGIME_START) -> pd.DataFrame:
    """
    以 start 為新常態起點，讓之後的平常時期有自己的敏感度；
    2026 的比較基準改為同一環境下（起點之後、開戰前）的平常水準。
    """
    specs = [("dDGS2", "Dhist_short"), ("dDGS10", "Dhist"), ("dACMRNY10", "Dhist"), ("dACMTP10", "Dhist")]
    quantities = {
        "pre-regime normal": {"oil_a": 1, "oil_b": 1},
        "post-regime normal": {"oil_a": 1, "oil_b": 1, "oil_a_xpost": 1, "oil_b_xpost": 1},
        "2026": {"oil_a": 1, "oil_b": 1, "oil_a_xpost": 1, "oil_b_xpost": 1,
                 "oil_a_x2026": 1, "oil_b_x2026": 1},
        "2026 vs post-regime normal": {"oil_a_x2026": 1, "oil_b_x2026": 1},
    }
    post = (df.index >= start).astype(int)
    rows = []
    for y, hist in specs:
        X = design(df, hist)
        X["DPOST"] = post
        for tag, col in [("a", "dOil"), ("b", "dOil_lead")]:
            X[f"oil_{tag}_xpost"] = df[col] * post
        res = ols_hac(df[y], X)
        for label, w in quantities.items():
            est, se = res.lincom(w)
            rows.append({"regime start": start, "dependent": y, "quantity": label,
                         "bp/10%": est * SCALE, "SE": se * SCALE,
                         "95% CI low": (est - 1.96 * se) * SCALE,
                         "95% CI high": (est + 1.96 * se) * SCALE,
                         "p one-sided (>0)": 1 - stats.norm.cdf(est / se)})
    return pd.DataFrame(rows)


def episode_betas(df: pd.DataFrame) -> pd.DataFrame:
    """每段期間內單獨估計（描述性，不做正式比較）。"""
    ep_cols = [c for c in df.columns if c.startswith("E_")] + ["D2026"]
    rows = []
    for c in ep_cols:
        sub = df[df[c] == 1]
        row = {"episode": c.replace("E_", ""), "days": len(sub)}
        for y in ["dDGS2", "dDGS10"]:
            X = pd.DataFrame({"const": 1.0, "oil_a": sub["dOil"], "oil_b": sub["dOil_lead"],
                              "dEq": sub["dEq"]}, index=sub.index)
            res = ols_hac(sub[y], X, lags=min(HAC_LAGS, max(1, len(sub) // 10)))
            est, se = res.lincom({"oil_a": 1, "oil_b": 1})
            row[f"{y} (bp/10%)"], row[f"{y} SE"] = est * SCALE, se * SCALE
        rows.append(row)
    return pd.DataFrame(rows).set_index("episode")


ROLL_SERIES = ["dDGS2", "dDGS10", "dDGS30", "dACMRNY10"]   # ACMRNY10 = 10 年期政策預期


def rolling_betas(df: pd.DataFrame, window: int = ROLL_WINDOW) -> pd.DataFrame:
    """簡化式滾動 beta（不含虛擬變數），總 beta = 當天 + 隔天。"""
    out = {}
    base = df[["dOil", "dOil_lead", "dEq"]]
    for y in ROLL_SERIES:
        data = pd.concat([df[y], base], axis=1).dropna()
        Y = data[y].to_numpy()
        X = np.column_stack([np.ones(len(data)), data[["dOil", "dOil_lead", "dEq"]].to_numpy()])
        # 以累積和快速計算每個視窗的 X'X 與 X'y
        XX = np.einsum("ti,tj->tij", X, X).cumsum(axis=0)
        XY = (X * Y[:, None]).cumsum(axis=0)
        betas = np.full(len(data), np.nan)
        for t in range(window - 1, len(data)):
            a = XX[t] - (XX[t - window] if t >= window else 0)
            b = XY[t] - (XY[t - window] if t >= window else 0)
            coef = np.linalg.solve(a, b)
            betas[t] = (coef[1] + coef[2]) * SCALE
        out[y] = pd.Series(betas, index=data.index)
    roll = pd.DataFrame(out)
    roll["D2026"] = df["D2026"].reindex(roll.index)
    roll["DZLB"] = df["DZLB"].reindex(roll.index)
    return roll


def rolling_band(roll: pd.DataFrame) -> pd.DataFrame:
    """歷史 10%–90% 區間：排除零利率期間與 2026 年。"""
    hist = roll[(roll.DZLB == 0) & (roll.D2026 == 0)]
    rows = []
    for y in ROLL_SERIES:
        s = hist[y].dropna()
        latest = roll[y].dropna().iloc[-1]       # ACM 更新較慢，各序列取自己的最新值
        rows.append({"dependent": y, "p10": s.quantile(0.1), "p50": s.median(), "p90": s.quantile(0.9),
                     "latest": latest, "latest percentile": (s < latest).mean()})
    return pd.DataFrame(rows).set_index("dependent")


def main() -> None:
    df = pd.read_csv(PREPARED, index_col=0, parse_dates=True)
    RESULTS.mkdir(parents=True, exist_ok=True)

    table, _ = main_regressions(df)
    eps = episode_betas(df)
    roll = rolling_betas(df)
    band = rolling_band(roll)
    cv = supwald_critical_values()
    paths = {y: break_test(df, y, h) for y, h in [BREAK_REF, ("dDGS10", "Dhist")]}
    brk = break_summary(paths, cv)
    break_date = str(brk.loc[BREAK_REF[0], "break date"])
    regime = regime_robustness(df, REGIME_START)            # 主要設定
    regime_break = regime_robustness(df, break_date)        # 輔助：斷點日期

    table.to_csv(RESULTS / "main_regressions.csv")
    eps.to_csv(RESULTS / "episode_betas.csv")
    roll.to_csv(RESULTS / "rolling_betas.csv")
    band.to_csv(RESULTS / "rolling_band.csv")
    brk.to_csv(RESULTS / "break_test.csv")
    pd.concat({y: p["wald"] for y, p in paths.items()}, axis=1).to_csv(RESULTS / "break_wald_path.csv")
    regime.to_csv(RESULTS / "robust_regime.csv", index=False)
    regime_break.to_csv(RESULTS / "robust_regime_breakdate.csv", index=False)

    pd.set_option("display.width", 200)
    cols = ["Normal (bp/10%)", "Historical shocks (bp/10%)", "2026 (bp/10%)",
            "2026 excess vs hist (bp/10%)", "excess 95% CI low", "excess 95% CI high",
            "p one-sided (2026 > hist)", "nobs"]
    print("== 主要迴歸：油價漲 10% 對應的殖利率變動（bp）==")
    print(table[cols].round(2).to_string())
    print("\n== 各期間 beta（描述性）==")
    print(eps.round(2).to_string())
    print("\n== 滾動 beta 歷史區間（排除零利率與 2026）==")
    print(band.round(2).to_string())
    print("\n== 結構性斷點檢定（sup-Wald，開戰前樣本）==")
    print(brk.round(3).to_string())
    print(f"\n== 新常態比較（主要設定）：以 {REGIME_START} 為起點，2026 vs 起點後平常時期 ==")
    print(regime.round(2).to_string(index=False))
    print(f"\n== 新常態比較（輔助）：以斷點 {break_date} 為起點 ==")
    key = regime_break[regime_break.quantity == "2026 vs post-regime normal"]
    print(key.round(2).to_string(index=False))


if __name__ == "__main__":
    main()
