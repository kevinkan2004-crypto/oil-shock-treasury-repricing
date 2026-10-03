"""Regenerate the data-driven parts of README.md from the CSVs in results/.

Only text between marker comments is rewritten:

    <!-- ASOF:START -->      ... <!-- ASOF:END -->
    <!-- RESULTS:START ... --> ... <!-- RESULTS:END -->
    <!-- ROBUSTNESS:START ... --> ... <!-- ROBUSTNESS:END -->

Everything else in README.md (TL;DR, methodology, interpretation) is left
untouched. If the new numbers would contradict the written conclusions,
the script prints a warning (shown as an annotation in GitHub Actions) so
the prose can be revisited by hand.

Usage:
    python src/update_readme.py            # rewrite README.md in place
    python src/update_readme.py --check    # exit 1 if README.md is out of date
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
PREPARED = ROOT / "data" / "prepared.csv"
README = ROOT / "README.md"

SIG_LEVEL = 0.05  # bold an estimate when its one-sided p is below this
REGIME_START = "2022-03-17"

LABELS = {
    "dDGS2": "2y",
    "dDGS10": "10y",
    "dDGS30": "30y",
    "dSlope_10_2": "10y − 2y slope",
    "dACMRNY10": "Policy expectations",
    "dACMTP10": "Term premium",
    "dACMY10": "Total",
}
REGIME_LABELS = {
    "dDGS2": "2y",
    "dDGS10": "10y",
    "dACMRNY10": "10y policy expectations",
    "dACMTP10": "10y term premium",
}


# ---------------------------------------------------------------- formatting

def num(x: float, signed: bool = False) -> str:
    """One decimal, true minus sign, optional leading plus."""
    s = f"{x:+.1f}" if signed else f"{x:.1f}"
    if s in ("-0.0", "+0.0"):
        s = "0.0" if not signed else "+0.0"
    return s.replace("-", "−")


def pval(p: float) -> str:
    return "< 0.01" if p < 0.01 else f"{p:.2f}"


def ci(lo: float, hi: float) -> str:
    return f"[{num(lo)}, {num(hi)}]"


def bold_if(text: str, p: float) -> str:
    return f"**{text}**" if p < SIG_LEVEL else text


def long_date(d) -> str:
    d = pd.Timestamp(d)
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |",
             "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


# ---------------------------------------------------------------- loading

def read(name: str) -> pd.DataFrame:
    path = RESULTS / name
    if not path.exists():
        sys.exit(f"update_readme: missing {path}")
    return pd.read_csv(path)


def regime_value(df: pd.DataFrame, dep: str, quantity: str) -> pd.Series:
    row = df[(df["dependent"] == dep) & (df["quantity"] == quantity)]
    if len(row) != 1:
        sys.exit(f"update_readme: expected one row for {dep} / {quantity}, got {len(row)}")
    return row.iloc[0]


# ---------------------------------------------------------------- sections

def section_vs_past(main: pd.DataFrame) -> tuple[str, list[str]]:
    rows, significant = [], []
    for dep in ["dDGS2", "dDGS10", "dDGS30", "dSlope_10_2"]:
        r = main.set_index("dependent").loc[dep]
        p = r["p one-sided (2026 > hist)"]
        is_slope = dep == "dSlope_10_2"
        excess = num(r["2026 excess vs hist (bp/10%)"], signed=True)
        rows.append([
            LABELS[dep],
            num(r["Normal (bp/10%)"]),
            num(r["Historical shocks (bp/10%)"]),
            num(r["2026 (bp/10%)"]),
            excess if is_slope else bold_if(excess, p),
            ci(r["excess 95% CI low"], r["excess 95% CI high"]),
            "—" if is_slope else pval(p),
        ])
        if not is_slope and p < SIG_LEVEL:
            significant.append(LABELS[dep])

    tbl = table(["Maturity", "Normal", "Past shocks", "2026", "2026 excess",
                 "95% CI", "p (one-sided)"], rows)

    slope = main.set_index("dependent").loc["dSlope_10_2"]
    if significant:
        note = (f"Against past supply shocks, the {' and '.join(significant)} "
                f"react{'s' if len(significant) == 1 else ''} significantly more in 2026 "
                f"(5% level, one-sided).")
    else:
        note = "No maturity reacts significantly more in 2026 than in past supply shocks (5% level, one-sided)."
    if slope["excess 95% CI high"] < 0:
        note += " Oil moves now flatten the curve."
    elif slope["excess 95% CI low"] > 0:
        note += " Oil moves now steepen the curve."
    return "### 1. 2026 vs. past supply shocks\n\n" + tbl + "\n\n" + note, significant


def section_decomposition(main: pd.DataFrame) -> tuple[str, dict]:
    m = main.set_index("dependent")
    rows = []
    for dep in ["dACMRNY10", "dACMTP10", "dACMY10"]:
        r = m.loc[dep]
        p = r["p one-sided (2026 > hist)"]
        rows.append([
            LABELS[dep],
            num(r["Normal (bp/10%)"]),
            num(r["Historical shocks (bp/10%)"]),
            num(r["2026 (bp/10%)"]),
            bold_if(num(r["2026 excess vs hist (bp/10%)"], signed=True), p),
            ci(r["excess 95% CI low"], r["excess 95% CI high"]),
            pval(p),
        ])
    tbl = table(["Component", "Normal", "Past shocks", "2026", "2026 excess",
                 "95% CI", "p (one-sided)"], rows)

    p_rny = m.loc["dACMRNY10", "p one-sided (2026 > hist)"]
    p_tp = m.loc["dACMTP10", "p one-sided (2026 > hist)"]
    p_tot = m.loc["dACMY10", "p one-sided (2026 > hist)"]
    if p_rny < SIG_LEVEL and p_tp >= SIG_LEVEL:
        note = "The excess runs through the expected policy path, not the term premium."
        if p_tot >= SIG_LEVEL:
            note += " The 10y total looks insignificant only because the two components offset."
    elif p_tp < SIG_LEVEL and p_rny >= SIG_LEVEL:
        note = "The excess runs through the term premium, not the expected policy path."
    elif p_tp < SIG_LEVEL and p_rny < SIG_LEVEL:
        note = "Both the expected policy path and the term premium show a significant excess."
    else:
        note = "Neither component shows a significant excess."
    flags = {"rny": p_rny < SIG_LEVEL, "tp": p_tp < SIG_LEVEL}
    return "### 2. Where the excess comes from (10y ACM decomposition)\n\n" + tbl + "\n\n" + note, flags


def section_regime(daily: pd.DataFrame, weekly: pd.DataFrame) -> tuple[str, list[str]]:
    daily = daily[daily["regime start"] == REGIME_START]
    weekly = weekly[(weekly["regime start"] == REGIME_START)
                    & weekly["regime source"].str.contains("main")]
    rows, significant = [], []
    for dep in ["dDGS2", "dDGS10", "dACMRNY10", "dACMTP10"]:
        pre = regime_value(daily, dep, "pre-regime normal")
        post = regime_value(daily, dep, "post-regime normal")
        y26 = regime_value(daily, dep, "2026")
        exc = regime_value(daily, dep, "2026 vs post-regime normal")
        wk = regime_value(weekly, dep, "2026 vs post-regime normal")
        rows.append([
            REGIME_LABELS[dep],
            num(pre["bp/10%"]),
            num(post["bp/10%"]),
            num(y26["bp/10%"]),
            bold_if(f"{num(exc['bp/10%'], signed=True)} {ci(exc['95% CI low'], exc['95% CI high'])}",
                    exc["p one-sided (>0)"]),
            pval(exc["p one-sided (>0)"]),
            bold_if(num(wk["bp/10%"], signed=True), wk["p one-sided (>0)"]),
            pval(wk["p one-sided (>0)"]),
        ])
        if exc["p one-sided (>0)"] < SIG_LEVEL or wk["p one-sided (>0)"] < SIG_LEVEL:
            significant.append(REGIME_LABELS[dep])

    tbl = table(["Series", "Pre-2022 normal", "Post-2022 normal", "2026",
                 "2026 vs. post-2022 normal (daily)", "p", "Same, weekly", "p"], rows)

    pre2 = regime_value(daily, "dDGS2", "pre-regime normal")["bp/10%"]
    post2 = regime_value(daily, "dDGS2", "post-regime normal")["bp/10%"]
    note = f"Since 2022 the 2y's oil sensitivity has run at about {post2 / pre2:.1f} times its earlier level."
    if significant:
        note += (f" Against that norm, 2026 shows a significant excess (5% level) for: "
                 f"{', '.join(significant)}.")
    else:
        note += " Against that norm, no series shows a significant 2026 excess at the 5% level."
    return "### 3. Overreaction or new normal?\n\n" + tbl + "\n\n" + note, significant


def section_break(breaks: pd.DataFrame) -> str:
    def get(excl: str, dep: str) -> pd.Series:
        row = breaks[(breaks["exclusion"] == excl) & (breaks["dependent"] == dep)]
        if len(row) != 1:
            sys.exit(f"update_readme: expected one break row for {excl} / {dep}")
        return row.iloc[0]

    def p_short(p: float) -> str:
        return f"{p:.3f}" if p < 0.01 else f"{p:.2f}"

    def describe(dep: str) -> str:
        b = get("None (baseline)", dep)
        return (f"{long_date(b['break date'])} "
                f"(sup-Wald {b['sup-Wald']:.1f}, p {p_short(b['p-value'])})")

    deps = ["dDGS2", "dACMRNY10"]
    survives = all(get("Excl. FOMC + CPI", d)["p-value"] < SIG_LEVEL for d in deps)
    return ("**Break test.** On daily data, the 2y breaks on " + describe("dDGS2")
            + " and the 10y policy expectations on " + describe("dACMRNY10") + "."
            + (" Both survive excluding FOMC and CPI days." if survives
               else " Not every break survives excluding FOMC and CPI days."))


ROBUST_DEPS = ["dDGS2", "dDGS10", "dACMRNY10"]


def cell(r: pd.Series, value_col: str = "bp/10%", p_col: str = "p one-sided (>0)") -> str:
    return f"{num(r[value_col], signed=True)} ({pval(r[p_col])})"


def section_robustness() -> tuple[str, list[str]]:
    """Robustness table (2026 vs. post-2022 normal) plus checks on the hand-written prose."""
    daily = read("robust_regime.csv")
    daily = daily[daily["regime start"] == REGIME_START]
    weekly = read("robust_weekly_regime.csv")
    weekly = weekly[(weekly["regime start"] == REGIME_START)
                    & weekly["regime source"].str.contains("main")]
    events = read("robust_events_regime.csv")
    events = events[(events["regime start"] == REGIME_START)
                    & (events["exclusion"] == "Excl. FOMC + CPI")]
    alt = read("robust_alt_regimes.csv")
    alt = alt[alt["freq"] == "daily"]
    phases = read("robust_phases.csv")
    phases_d = phases[(phases["freq"] == "daily")
                      & (phases["comparison"] == f"regime from {REGIME_START}")]
    phases_w = phases[(phases["freq"] == "weekly")
                      & (phases["comparison"] == f"regime from {REGIME_START}")]

    EXC = "2026 vs post-regime normal"
    specs = [  # (label, what it addresses, frame, quantity)
        ("Baseline (daily)", "—", daily, EXC),
        ("Weekly data", "London/New York close mismatch", weekly, EXC),
        ("Excl. FOMC + CPI days", "Macro news inflating the new normal", events, EXC),
    ]
    for start, why in [("2021-07-01", "Earlier start of the inflation regime"),
                       ("2023-07-27", "Start after the last hike")]:
        specs.append((f"Regime from {long_date(start)}", why,
                      alt[alt["regime start"] == start], EXC))
    specs += [
        ("2026 Phase 1 (Mar 2 – Jul 1)", "Excess hidden by averaging", phases_d, "Phase 1 vs post-2022 normal"),
        ("2026 Phase 2 (Jul 8 – now)", "Current market", phases_d, "Phase 2 vs post-2022 normal"),
    ]

    rows, sig_2y_rows = [], []
    for label, why, frame, qty in specs:
        cells = []
        for dep in ROBUST_DEPS:
            r = regime_value(frame, dep, qty)
            cells.append(cell(r))
            if dep == "dDGS2" and r["p one-sided (>0)"] < SIG_LEVEL:
                sig_2y_rows.append(label)
        rows.append([label, why] + cells)

    tbl = table(["Check", "What it addresses", "2y", "10y", "10y policy exp."], rows)
    body = ("2026 vs. the post-2022 normal, bp per 10% Brent move (one-sided p in parentheses):\n\n" + tbl)

    # ---- checks on the hand-written "What holds / signal to watch" prose
    warnings = []
    if sig_2y_rows:
        warnings.append("Robustness: 2y now shows a significant excess vs. the post-2022 norm in "
                        f"{', '.join(sig_2y_rows)}; 'What holds' needs revisiting.")

    vs_past = [
        ("weekly", read("robust_weekly_main.csv").query("dependent == 'dDGS2' and quantity == '2026 excess vs hist'"),
         "p one-sided (>0)"),
        ("excl. FOMC + CPI", read("robust_events_main.csv").query("dependent == 'dDGS2' and exclusion == 'Excl. FOMC + CPI'"),
         "p one-sided (>0)"),
        ("Phase 1", phases.query("freq == 'daily' and dependent == 'dDGS2' and quantity == 'Phase 1 vs past shocks'"),
         "p one-sided (>0)"),
        ("Phase 2", phases.query("freq == 'daily' and dependent == 'dDGS2' and quantity == 'Phase 2 vs past shocks'"),
         "p one-sided (>0)"),
    ]
    lost = [name for name, df, col in vs_past if len(df) == 1 and df.iloc[0][col] >= SIG_LEVEL]
    if lost:
        warnings.append("Robustness: 2y excess vs. past shocks is no longer significant in "
                        f"{', '.join(lost)}; 'What holds' needs revisiting.")

    p2_daily = regime_value(phases_d, "dDGS10", "Phase 2 vs post-2022 normal")["p one-sided (>0)"]
    p2_weekly = regime_value(phases_w, "dDGS10", "Phase 2 vs post-2022 normal")["p one-sided (>0)"]
    if p2_daily >= SIG_LEVEL:
        warnings.append(f"Signal to watch: 10y Phase 2 excess is no longer significant on daily data "
                        f"(p {p2_daily:.2f}); the term-premium signal may have faded.")
    if p2_weekly < SIG_LEVEL:
        warnings.append(f"Signal to watch: 10y Phase 2 excess is now significant on weekly data too "
                        f"(p {p2_weekly:.2f}); the signal may now be robust. Update TL;DR and robustness prose.")
    return body, warnings


def as_of_line() -> str:
    if PREPARED.exists():
        last = pd.read_csv(PREPARED, usecols=["date"])["date"].max()
        stamp = long_date(last)
    else:
        stamp = long_date(date.today())
    return (f"*Results as of {stamp}. Data refresh daily; "
            f"see [`results/`](results/) for the latest numbers.*")


# ---------------------------------------------------------------- main

def replace_block(text: str, start: str, end: str, body: str, pad: bool = True) -> str:
    pattern = re.compile(rf"({re.escape(start)}[^\n]*\n)(.*?)(\n{re.escape(end)})", re.S)
    if not pattern.search(text):
        sys.exit(f"update_readme: markers {start} / {end} not found in README.md")
    inner = f"\n{body}\n" if pad else body
    return pattern.sub(lambda m: m.group(1) + inner + m.group(3), text, count=1)


def build() -> tuple[str, str, str, list[str]]:
    main = read("main_regressions.csv")
    s1, sig_past = section_vs_past(main)
    s2, decomp = section_decomposition(main)
    s3, sig_regime = section_regime(read("robust_regime.csv"), read("robust_weekly_regime.csv"))
    s4 = section_break(read("robust_events_break.csv"))
    results = "\n\n".join([s1, s2, s3, s4])

    # Conclusions written by hand in the TL;DR; warn if the data stop supporting them.
    warnings = []
    if "2y" not in sig_past:
        warnings.append("2y excess vs. past shocks is no longer significant: TL;DR bullet 1 needs revisiting.")
    if not decomp["rny"] or decomp["tp"]:
        warnings.append("Decomposition pattern changed (policy path vs. term premium): TL;DR bullet 2 needs revisiting.")
    if sig_regime:
        warnings.append(f"2026 now shows a significant excess vs. the post-2022 norm for {', '.join(sig_regime)}: "
                        "the 'no overreaction' conclusion needs revisiting.")
    robustness, robust_warnings = section_robustness()
    return as_of_line(), results, robustness, warnings + robust_warnings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="do not write; exit 1 if README.md would change")
    args = parser.parse_args()

    old = README.read_text(encoding="utf-8")
    as_of, results, robustness, warnings = build()
    new = replace_block(old, "<!-- ASOF:START -->", "<!-- ASOF:END -->", as_of, pad=False)
    new = replace_block(new, "<!-- RESULTS:START", "<!-- RESULTS:END -->", results)
    new = replace_block(new, "<!-- ROBUSTNESS:START", "<!-- ROBUSTNESS:END -->", robustness)

    for w in warnings:
        # '::warning::' renders as an annotation in GitHub Actions; plain text elsewhere.
        print(f"::warning::{w}")

    if args.check:
        if new != old:
            print("README.md is out of date; run python src/update_readme.py")
            sys.exit(1)
        print("README.md is up to date.")
        return

    if new != old:
        README.write_text(new, encoding="utf-8")
        print("README.md updated.")
    else:
        print("README.md unchanged.")


if __name__ == "__main__":
    main()
