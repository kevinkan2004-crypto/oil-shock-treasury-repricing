"""
prepare.py
前處理與衝擊期間定義（研究大綱第一、二部分）。

輸入：data/oil_bond_data.xlsx（由 fetch_data.py 產生）
輸出：
    data/prepared.csv   迴歸用的日資料（變動量、虛擬變數）
    data/episodes.csv   各衝擊期間的起訖日與規則計算結果

單位
    殖利率變動：bp
    油價、NASDAQ：對數報酬 × 100（%）
    → beta 的意義是「油價漲 1%，殖利率變動幾 bp」
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_XLSX = ROOT / "data" / "oil_bond_data.xlsx"
OUT_PREPARED = ROOT / "data" / "prepared.csv"
OUT_EPISODES = ROOT / "data" / "episodes.csv"

# ----------------------------------------------------------------------------
# 衝擊期間設定（起始日皆有來源，見研究大綱第二部分）
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Episode:
    name: str
    trigger: str            # 觸發事件日期（可能是週末，程式會對應到下一個交易日）
    role: str               # "hist" = 歷史對照；"study" = 2026 研究樣本
    source: str
    exclude_short_end: bool = False   # 與零利率重疊的期間，短端分析排除


EPISODES = [
    Episode("Gulf War", "1990-08-02", "hist", "https://en.wikipedia.org/wiki/Gulf_War"),
    Episode("Venezuela strike and Iraq invasion", "2002-12-02", "hist",
            "https://en.wikipedia.org/wiki/2002%E2%80%932003_Venezuelan_general_strike"),
    Episode("Libyan civil war", "2011-02-15", "hist",
            "https://en.wikipedia.org/wiki/Libyan_civil_war_(2011)", exclude_short_end=True),
    Episode("Abqaiq attack", "2019-09-14", "hist",
            "https://en.wikipedia.org/wiki/Abqaiq%E2%80%93Khurais_attack"),
    Episode("Russia invades Ukraine", "2022-02-24", "hist",
            "https://en.wikipedia.org/wiki/Russo-Ukrainian_war_(2022%E2%80%93present)"),
    Episode("Twelve-Day War", "2025-06-13", "hist",
            "https://en.wikipedia.org/wiki/Twelve-Day_War"),
    Episode("US-Iran war", "2026-02-28", "study",
            "https://en.wikipedia.org/wiki/Timeline_of_the_2026_Iran_war"),
]

# 經規則檢驗後排除的事件（保留紀錄，方便說明）
EXCLUDED_CANDIDATES = [
    Episode("Hurricanes Katrina and Rita", "2005-08-29", "excluded", "fails criterion 4"),
    Episode("Libyan civil war 2014-2020", "2014-05-16", "excluded", "fails criteria 2-3"),
]

ZLB_PERIODS = [("2008-12-16", "2015-12-16"), ("2020-03-16", "2022-03-16")]

# 期間規則參數
MA_WINDOW = 5      # 5 日移動平均
CAP_DAYS = 126     # 上限約 6 個月


# ----------------------------------------------------------------------------
# 規則：價格回歸法
# ----------------------------------------------------------------------------
def episode_window(brent: pd.Series, trigger: str, ongoing: bool = False) -> dict:
    """
    基準 = 觸發日前最後一個交易日的 Brent 收盤價。
    從第一個完整的 5 日視窗（第 5 個交易日）起，5 日均價 <= 基準的第一天為結束日；
    上限 126 個交易日。研究樣本（ongoing=True）不套用規則，直接延伸到資料最後一天。
    """
    brent = brent.dropna()
    t = pd.Timestamp(trigger)
    base = brent[brent.index < t].iloc[-1]
    post = brent[brent.index >= t]
    start = post.index[0]

    if ongoing:
        end, how = post.index[-1], "ongoing"
    else:
        ma = post.rolling(MA_WINDOW).mean().iloc[MA_WINDOW - 1:]
        hit = ma[ma <= base]
        cap_date = post.index[min(CAP_DAYS, len(post) - 1)]
        if len(hit) and hit.index[0] <= cap_date:
            end, how = hit.index[0], "reverted"
        else:
            end, how = cap_date, "cap"

    window = post.loc[:end]
    dropped = (how == "reverted") and (end == post.index[MA_WINDOW - 1])
    return {
        "start": start, "end": end, "rule": how, "trading_days": len(window),
        "baseline": base, "peak": window.max(),
        "peak_pct": (window.max() / base - 1) * 100,
        "dropped_no_shock": dropped,
    }


def build_episodes(brent: pd.Series) -> pd.DataFrame:
    rows = []
    for ep in EPISODES + EXCLUDED_CANDIDATES:
        info = episode_window(brent, ep.trigger, ongoing=(ep.role == "study"))
        rows.append({"name": ep.name, "trigger": pd.Timestamp(ep.trigger), "role": ep.role,
                     "exclude_short_end": ep.exclude_short_end, "source": ep.source, **info})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# 資料前處理
# ----------------------------------------------------------------------------
def load_raw(path: Path = DATA_XLSX) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name="data", parse_dates=["date"]).set_index("date")
    return df.sort_index()


def log_ret(s: pd.Series) -> pd.Series:
    return np.log(s).diff() * 100


def prepare(raw: pd.DataFrame, episodes: pd.DataFrame) -> pd.DataFrame:
    """
    只保留殖利率、Brent、NASDAQ 都有資料的交易日，再計算變動量，
    因此假日前後的報酬自動跨到最近的有效交易日。
    """
    core = raw[["DGS2", "DGS10", "DGS30", "DCOILBRENTEU", "DCOILWTICO", "NASDAQCOM"]]
    df = core.dropna(subset=["DGS2", "DGS10", "DCOILBRENTEU", "NASDAQCOM"]).copy()

    out = pd.DataFrame(index=df.index)
    for col in ["DGS2", "DGS10", "DGS30"]:
        out[f"d{col}"] = df[col].diff() * 100                      # bp
    out["dSlope_10_2"] = out["dDGS10"] - out["dDGS2"]

    out["dOil"] = log_ret(df["DCOILBRENTEU"])
    out["dOil_lead"] = out["dOil"].shift(-1)                      # Dimson：隔天油價
    # WTI 在 2020-04-20 附近為負值，對數報酬無法計算，設為缺值
    wti = df["DCOILWTICO"].where(df["DCOILWTICO"] > 0)
    out["dWTI"] = log_ret(wti)
    out["dWTI_lead"] = out["dWTI"].shift(-1)
    out["dEq"] = log_ret(df["NASDAQCOM"])

    # ACM：另外對齊（更新較慢，近期可能缺值）
    for col in ["ACMY02", "ACMRNY02", "ACMTP02", "ACMY10", "ACMRNY10", "ACMTP10"]:
        s = raw[col].reindex(df.index)
        out[f"d{col}"] = s.diff() * 100

    # 30 年期停發期間的變動設為缺值（避免跨越斷層的差分）
    gap = (df.index >= "2002-02-18") & (df.index <= "2006-02-09")
    out.loc[gap, "dDGS30"] = np.nan

    # 虛擬變數
    def in_windows(rows: pd.DataFrame) -> pd.Series:
        mask = pd.Series(False, index=out.index)
        for _, r in rows.iterrows():
            mask |= (out.index >= r["start"]) & (out.index <= r["end"])
        return mask.astype(int)

    hist = episodes[(episodes.role == "hist") & ~episodes.dropped_no_shock]
    out["D2026"] = in_windows(episodes[episodes.role == "study"])
    out["Dhist"] = in_windows(hist)
    out["Dhist_short"] = in_windows(hist[~hist.exclude_short_end])   # 短端用
    zlb = pd.Series(False, index=out.index)
    for a, b in ZLB_PERIODS:
        zlb |= (out.index >= a) & (out.index <= b)
    out["DZLB"] = zlb.astype(int)

    # 每個歷史期間各自的標記（逐段比較用）
    for _, r in hist.iterrows():
        key = "E_" + "".join(ch for ch in r["name"] if ch.isalnum())[:20]
        out[key] = ((out.index >= r["start"]) & (out.index <= r["end"])).astype(int)

    return out.iloc[1:]   # 第一列沒有變動量


def main() -> None:
    raw = load_raw()
    episodes = build_episodes(raw["DCOILBRENTEU"])
    prepared = prepare(raw, episodes)

    OUT_PREPARED.parent.mkdir(parents=True, exist_ok=True)
    prepared.to_csv(OUT_PREPARED)
    episodes.to_csv(OUT_EPISODES, index=False)

    show = episodes.copy()
    for c in ["trigger", "start", "end"]:
        show[c] = show[c].dt.date
    print(show[["name", "role", "start", "end", "rule", "trading_days", "peak_pct",
                "dropped_no_shock"]].round(1).to_string(index=False))
    print(f"\n{len(prepared):,} 個交易日，{prepared.index[0].date()} ~ {prepared.index[-1].date()}")
    print("虛擬變數天數：", prepared[["D2026", "Dhist", "Dhist_short", "DZLB"]].sum().to_dict())


if __name__ == "__main__":
    main()
