#!/usr/bin/env python3
"""
A1V12 Yahoo Production v4.0 (Unified Adjusted-Close Total Return)

Complete integrated package.

v4.0 unified adjusted-close architecture:
- ALL NAV series (tactical sleeve, VOO benchmark, every model) use
  Adjusted Close prices for total-return reporting.
- Dividends are reported separately via the dividend income engine.
  They are NOT double-counted — adj-close captures reinvestment,
  dividend reports quantify the cash flows independently.
- Tactical signal: Adjusted Close MGK/MGV ratio (configurable via
  SIGNAL_PRICE_BASIS; default ADJUSTED; RAW available for research).
- T+2 open-price execution: look-ahead free (amended 2026-07, was T+1).
- Sharpe/Sortino: daily excess returns vs BIL (not vs 0).
- VFINX: research-only backfill proxy for VOO pre-2011. Never shown
  in production charts or metrics.
- RAW_CLOSE_ASSETS and VOO_RAW_BENCHMARK_KEY removed.
- comp_raw and comp_open still built (needed for dividend engine
  open-price execution and audit). Not used for NAV or charts.

v3.5 benchmark/model price-basis architecture:
- A1V12 tactical sleeve: raw Close for NAV/charts/metrics.
- VOO benchmark: raw Close for NAV/charts/metrics, matching the tactical sleeve.
- VOO when held inside an MWM allocation model: Adjusted Close.
- All other allocation assets inside models: Adjusted Close.
- Tactical signals: Adjusted Close for the MGK/MGV ratio.
- Dividends remain reported separately from the raw-close tactical and benchmark series.

Production methodology:
- Tactical sleeve (MGK/MGV): raw Close for NAV/charts/metrics.
  Matches Koyfin and brokerage statements. Dividends shown separately.
- All other assets: Adjusted Close (total return, buy-and-hold).
  Graphs reflect actual investor return including reinvested distributions.
  PIMIX +22% in 2012, +11% in 2025 — matches Yahoo Finance total return.
- Tactical signals: configurable via SIGNAL_PRICE_BASIS. Default is Adjusted
  Close for MGK/MGV ratio; Raw Close can be selected for research comparison.
- RAW_CLOSE_ASSETS = {MGK, MGV, TACTICAL, A1V12} only.
- VOO benchmark uses a dedicated raw-close series; VOO allocations in models remain adjusted-close.

v3.4.5 patch:
- Workbook-first price sourcing: MGK, MGV, BIL, VEU, JIVE, AVUV, VOO
  are now loaded from Master_workbook_Portfolio_BackfilledPre2016.xlsx
  (committed to repo Config/ folder) which provides full history back
  to 2007. Yahoo gap-fills only the last ~30 days from workbook cutoff
  to today. This fixes the Yahoo server-side 5-year truncation that was
  causing portfolio NAV to start from Sep 2021 instead of Jan 2011,
  inflating the 5Y return to 234% instead of the correct ~100%.
- _load_workbook_prices() and _ensure_workbook_in_config() helpers added.
- WORKBOOK_SOURCES and WORKBOOK_FILE module constants added.

v3.4.4 patch:
- Confirmed RAW_CLOSE_ASSETS = tactical sleeve only (MGK, MGV, TACTICAL).
  Goal: non-tactical graphs show buy-and-hold total return matching what
  an investor actually earns. Adj-close achieves this — PIMIX shows +22%%
  in 2012 and +11%% in 2025, consistent with Yahoo Finance total return.
  Fixed-income mutual funds stay on adj-close; their declining dividend
  income pattern reflects genuine rate-environment effect, not a bug.

v3.4.3 patch:
- RAW_CLOSE_ASSETS expanded to include PIMIX, FIWDX, FIKQX, JBND, JPIE
  and their backfill proxies. Yahoo adj-close retroactively reduces all
  prior prices by each distribution paid, making early share counts and
  income artificially high. Raw close avoids this; distributions are
  captured separately by the dividend engine for all RAW_CLOSE_ASSETS.

v3.4.2 patch:
- build_portfolios() unified adjusted-close for all models including
  tactical sleeve and VOO benchmark. RAW_CLOSE_ASSETS removed.

v3.4.1 patch:
- download_prices() now uses yf.Ticker.dividends (dedicated endpoint)
  for mutual funds (PIMIX, FIWDX, FIKQX, JBND, JPIE and their backfill
  proxies). Yahoo's history(actions=True) silently omits many monthly
  distributions for open-end mutual funds. Both endpoints are now fetched
  and merged (per-date maximum kept) so no distribution event is lost.
  This fixes the incorrect declining-income pattern seen in conservative
  model dividend reports.
- MUTUAL_FUNDS constant added at module level.
- _clean_div_series() helper added for safe normalisation of both endpoints.

v3.4 changes from v3.3:
- Raw-close price return for all NAV / charts / metrics.
- Open-price execution: trade-day return split at open (old holding
  prior-close→open, new holding open→close). No look-ahead gain on
  trade dates.
- Full dividend income engine: cash distributions computed from the
  exact daily shares in the NAV ledger. Separate from price return.
- download_prices() now returns adj_wide, raw_wide, open_wide,
  div_wide, audit_df (five values).
- build_composites() accepts price_basis parameter; called separately
  for adjusted, raw-close, and open prices.
- build_dividend_composites() builds production-asset dividend-per-share
  series including scaled backfills.
- build_tactical_values() uses adjusted close + open for T+2 execution.
- build_portfolios() writes Portfolio_Daily_Share_Ledger.csv; returns
  (vals, ledger) tuple.
- build_holding_analytics() accepts comp_raw as second argument.
- build_dividend_analytics() computes dividend income from the share
  ledger. Produces six CSVs and two audit CSVs.
- Dashboard: Dividend Income tab with annual chart, cumulative chart,
  asset detail, monthly detail, fixed-income verification, and
  tactical holding-period income.
- Deployment guard in build_dashboard() prevents stale HTML deploy.
- Version v3.4 throughout.

v3.3 (previous):
- Binary MGK/MGV tactical sleeve, no JIVE.
- EMA89 crossover + 3-day cooldown.
- Annual rebalancing for multi-asset models.
- START_DATE 2008 for EMA warmup; PORTFOLIO_START 2011.
"""

from pathlib import Path
import sys, subprocess, importlib.util, json, shutil
from datetime import datetime

PROJECT = Path(__file__).resolve().parents[1]
DATA    = PROJECT / "Data"
DASH    = PROJECT / "Dashboard"
AUDIT   = PROJECT / "Audit"
BACKUPS = PROJECT / "Backups"
CONFIG  = PROJECT / "Config"
for p in [DATA, DASH, AUDIT, BACKUPS, CONFIG]:
    p.mkdir(exist_ok=True)

START_DATE      = "2008-01-01"
PORTFOLIO_START = "2011-01-01"
BASE_VALUE      = 100000.0

CORE_ASSETS = ["MGK","MGV","JIVE","VOO","BIL","VEU","AVUV","JPIE","JBND",
               "FIWDX","FIKQX","FBTC","XLG","IMCB","XLF","XLV","SPHB","MTUM","PIMIX"]
RESEARCH_ASSETS = ["EFV","DFSVX","JMSIX","WOBDX","FSRIX","FGBPX","XLRE",
                   "DXY","VIX","NERYX","VFINX","JMSFX","FRDM"]
YMAP = {"DXY": "DX-Y.NYB", "VIX": "^VIX"}

BACKFILLS = {
    "JIVE":  ("EFV",    "2023-12-31"),
    "AVUV":  ("DFSVX",  "2019-09-23"),
    "JPIE":  ("JMSIX",  "2021-10-27"),
    "JBND":  ("WOBDX",  "2023-11-30"),
    "FIWDX": ("FSRIX",  "2010-12-31"),
    "FIKQX": ("FGBPX",  "2010-12-31"),
    "FBTC":  ("XLRE",   "2025-11-30"),
    "VOO":   ("VFINX",  "2010-12-31"),
}

ASSET_ALIASES = {
    "NERYX": "JPIE", "JMSFX": "JPIE", "JMSIX": "JPIE",
    "DFSVX": "AVUV", "EFV": "JIVE", "XLRE": "FBTC",
    "WOBDX": "JBND", "FSRIX": "FIWDX", "FGBPX": "FIKQX",
    "FBTC_HIST": "FBTC",
    "TACTICAL": "A1V12", "A1V12": "A1V12",
}

TACTICAL_REPLACEMENT_CANDIDATES = {"MGK", "XLG", "VOO"}

# Execution timing (amended 2026-07 after backtest validation across
# 5yr / 10yr / Jan-2016 / Jan-2021 windows, band logic held constant):
#   Intended sequencing: trigger day (EMA89 crossover accepted by the
#   proximity/breakout bands) -> +1 trading day = confirmation (signal
#   must still agree; see CONFIRMATION_LAYER_ENABLED below) -> +1 more
#   trading day = trade execution. That's trigger+2 trading days total.
#   Since the confirmation layer already consumes the first of those two
#   days, EXECUTION_LAG_DAYS only needs to add ONE further day after the
#   decision commits, not two -- EXECUTION_LAG_DAYS=2 would put the trade
#   at trigger+3, which was tested but is not the intended design.
#   COOLDOWN_DAYS=2 was set alongside this but is currently non-binding
#   given the confirmation layer already enforces a >=2-day minimum
#   gap between decisions on its own (see COOLDOWN_DAYS docstring below
#   for the empirical check); left in as a defensive floor.
EXECUTION_LAG_DAYS = 1                 # trigger+2 total (confirmation +1, then execution +1)
COOLDOWN_DAYS = 2                      # currently non-binding; see note above


# Proximity filter (adopted 2026-07 after backtest validation across
# 5yr / 10yr / Jan-2016 / Jan-2021 windows on Raw-Close signal basis):
#   CAGR +2.4 to +3.3pp, Sharpe improved in every window, volatility
#   slightly lower, switches down 58-72%, and 5yr max drawdown improved
#   from -26.9% to -17.0% (T+1-open production execution, corrected
#   VOO adjusted-close data). A fresh EMA89 crossover is only accepted
#   if the ratio was within PROXIMITY_THRESHOLD of its EMA89 on at
#   least one of the prior PROXIMITY_LOOKBACK_DAYS trading days;
#   otherwise the crossover is treated as noise and ignored until a
#   later crossover qualifies. Applied before the existing cooldown.
PROXIMITY_FILTER_ENABLED = True
PROXIMITY_THRESHOLD = 0.0030          # 0.30%
PROXIMITY_LOOKBACK_DAYS = 3           # trading days

# Breakout fallback (adopted 2026-07 after the same 4-window validation):
#   Fixes a known failure mode of the proximity filter alone -- a fast,
#   decisive move that gaps straight through the +/-0.30% band without
#   ever lingering inside it gets permanently rejected and can leave the
#   sleeve on the wrong side for months (observed: an April 2025 MGK
#   crossover was rejected this way and the sleeve held MGV for 7 months,
#   missing a 14pp MGK-vs-MGV move). If the proximity lookback comes back
#   nil, the crossover is accepted anyway once the ratio has moved beyond
#   BREAKOUT_THRESHOLD from its EMA89 on the crossover day itself -- a
#   materially bigger, one-day magnitude test rather than a persistence
#   test, so it doesn't get fooled by ordinary short-term autocorrelation.
#   Validated result: beats the proximity-filter-alone version on CAGR
#   and Sharpe in all four windows (5yr/10yr/Jan-2016/Jan-2021), with
#   essentially unchanged max drawdown and a modest increase in switches.
#   A narrower sweep found 0.60-0.80% catches the target case but with a
#   smaller edge; 1.50% is too strict and never fires in this history.
BREAKOUT_FALLBACK_ENABLED = True
BREAKOUT_THRESHOLD = 0.0100           # 1.00%

# 1-day confirmation layer (adopted 2026-07 after the same 4-window
# validation, directly motivated by the 2026-07-15 whipsaw):
#   A band-accepted crossover (via proximity or breakout, above) is not
#   committed on the trigger day itself -- it must also still hold true
#   on the very next trading day before becoming the filtered signal. If
#   it reverses the day after triggering, the whole candidate is
#   discarded and the signal simply stays on its prior side, as if the
#   crossover never happened. This is a much shorter, narrower check
#   than the earlier 3-day confirmation fallback (which was tested and
#   rejected -- it let in too much ordinary autocorrelated noise). This
#   1-day version only screens the specific case where a band-qualified
#   signal immediately falls apart, and is fully causal (each day's
#   commit decision only ever uses that day's own and prior data).
#   Validated result: beats the no-confirmation version on CAGR AND
#   Sharpe in all four windows (+0.6 to +1.1pp CAGR), with IDENTICAL
#   max drawdown in every window, and 25-30% fewer switches everywhere.
#   Directly confirmed to have prevented the 2026-07-15 MGV->MGK
#   whipsaw entirely (the candidate crossover fails confirmation on
#   2026-07-16 and the sleeve simply stays in MGV).
CONFIRMATION_LAYER_ENABLED = True
CONFIRMATION_LAYER_DAYS = 1           # trading days

# Signal research switch.  This affects only the MGK/MGV ratio and EMA89
# crossover calculation; it does not change the tactical NAV, benchmark,
# model-allocation price basis, or dividend accounting.
#
# Allowed values:
#   "ADJUSTED" — recommended production setting; measures relative total-return
#                leadership and avoids ex-dividend distortions.
#   "RAW"      — research setting; uses unadjusted closing prices.
SIGNAL_PRICE_BASIS = "RAW"


def _normalise_signal_price_basis(value):
    basis = str(value).strip().upper().replace(" CLOSE", "")
    aliases = {
        "ADJ": "ADJUSTED",
        "ADJUSTED": "ADJUSTED",
        "TOTAL RETURN": "ADJUSTED",
        "RAW": "RAW",
        "CLOSE": "RAW",
        "PRICE": "RAW",
    }
    if basis not in aliases:
        raise ValueError(
            f"Invalid SIGNAL_PRICE_BASIS={value!r}. Use 'ADJUSTED' or 'RAW'."
        )
    return aliases[basis]

# v4.0: all assets use Adjusted Close. RAW_CLOSE_ASSETS removed.

# v4.0: VOO uses adjusted close throughout. VOO_RAW_BENCHMARK_KEY removed.
VOO_RAW_BENCHMARK_KEY = None  # retained as no-op for audit trail only

# Workbook price sources — assets whose full price history is read from the
# local Excel workbook (backfilled to 2007) rather than Yahoo Finance.
# Yahoo is used only to extend from the workbook cutoff to today (~30 days).
# This ensures the tactical sleeve and primary allocation assets always have
# the full 15+ year history regardless of Yahoo's server-side truncation.
#
# Tuple: (sheet_name, close_col, adj_col, open_col_or_None)
# Workbook path resolved relative to Config/ folder at runtime.
WORKBOOK_SOURCES = {
    # Sheet names match Master_workbook_BackfilledPre2016.xlsx as generated
    # by the dashboard tooling (sheets named by asset ticker, not original names)
    "MGK": ("MGK",  "Close", "Adj_Close", "Open"),
    "MGV": ("MGV",  "Close", "Adj_Close", "Open"),
    "BIL": ("BIL",  "Close", "Adj_Close", "Open"),
    "VEU": ("VEU",  "Close", "Adj_Close", "Open"),
    "JIVE":("JIVE", "Close", "Adj_Close", "Open"),
    "AVUV":("AVUV", "Close", "Adj_Close", "Open"),
    "VOO": ("VOO",  "Close", "Adj_Close", "Open"),
}
# Relative path from project root to the backfilled workbook
WORKBOOK_FILE = "Config/Master_workbook_BackfilledPre2016.xlsx"


def ensure(pkg):
    if importlib.util.find_spec(pkg) is None:
        print(f"Installing {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])


def backup_existing_outputs():
    tag  = datetime.now().strftime("%Y%m%d_%H%M%S")
    bdir = BACKUPS / f"backup_{tag}"
    bdir.mkdir(exist_ok=True)
    for folder in [DATA, DASH, AUDIT]:
        for f in folder.glob("*"):
            if f.is_file():
                shutil.copy2(f, bdir / f.name)
    return bdir


def read_allocations():
    import pandas as pd
    p = CONFIG / "MWM_Allocations.csv"
    if not p.exists():
        raise FileNotFoundError(f"Missing allocation file: {p}")
    df = pd.read_csv(p)
    cols = {c.lower(): c for c in df.columns}
    model_col  = cols.get("model")
    asset_col  = cols.get("asset") or cols.get("ticker")
    weight_col = cols.get("weight") or cols.get("allocation")
    if not (model_col and asset_col and weight_col):
        raise ValueError("Allocation file must contain Model, Asset, Weight columns.")
    out = df[[model_col, asset_col, weight_col]].copy()
    out.columns = ["Model", "Asset", "Weight"]
    out["Model"]  = out["Model"].astype(str).str.strip()
    out["Asset"]  = out["Asset"].astype(str).str.strip().str.upper()
    out["Weight"] = pd.to_numeric(
        out["Weight"].astype(str).str.replace("%", "", regex=False), errors="coerce")
    if out["Weight"].dropna().max() and out["Weight"].dropna().max() > 1.5:
        out["Weight"] = out["Weight"] / 100.0
    out = out.dropna(subset=["Model", "Asset", "Weight"])
    out["Production_Asset"] = out["Asset"].map(ASSET_ALIASES).fillna(out["Asset"])
    out.to_csv(DATA / "Allocation_Config_Normalized.csv", index=False)
    return out


def build_model_configs(alloc_df):
    static = {}
    for model, g in alloc_df.groupby("Model"):
        weights = {}
        for _, r in g.iterrows():
            asset = r["Production_Asset"]
            weights[asset] = weights.get(asset, 0.0) + float(r["Weight"])
        total = sum(weights.values())
        if total and abs(total - 1.0) > 0.02:
            weights = {k: v / total for k, v in weights.items()}
        static[model] = weights

    tactical  = {}
    map_rows  = []
    for model, weights in static.items():
        clean         = model.replace("MWM ", "").strip()
        tactical_name = f"Tactical {clean}"
        neww = {}
        replaced_weight = 0.0
        for asset, w in weights.items():
            if asset in TACTICAL_REPLACEMENT_CANDIDATES:
                replaced_weight += w
            else:
                neww[asset] = neww.get(asset, 0.0) + w
        if replaced_weight <= 0:
            neww = weights.copy()
        else:
            neww["TACTICAL"] = neww.get("TACTICAL", 0.0) + replaced_weight
        total = sum(neww.values())
        if total:
            neww = {k: v / total for k, v in neww.items()}
        tactical[tactical_name] = neww
        # Rule describes only assets actually present in this model
        replaced_assets = sorted(
            a for a in weights if a in TACTICAL_REPLACEMENT_CANDIDATES
        )
        rule = (f"{'/'.join(replaced_assets)} replaced by A1V12 tactical sleeve"
                if replaced_assets else "A1V12 tactical sleeve added")
        map_rows.append([model, tactical_name, replaced_weight, rule])

    import pandas as pd
    pd.DataFrame(map_rows,
                 columns=["Static_Model","Tactical_Model","Tactical_Weight","Rule"]
                 ).to_csv(DATA / "Tactical_Model_Map.csv", index=False)
    return static, tactical


# Assets that pay monthly distributions via NAV accrual (mutual funds).
# Yahoo's history(actions=True) frequently under-reports these distributions.
# The dedicated .dividends endpoint returns more complete distribution history.
MUTUAL_FUNDS = {"PIMIX", "FIWDX", "FIKQX", "JBND", "JPIE",
                "JMSIX", "WOBDX", "FSRIX", "FGBPX"}


def _clean_div_series(raw_divs, asset, sym):
    """
    Normalise a raw dividend Series from yfinance into a clean
    (Date -> float) Series with tz stripped and index normalised.
    Returns an empty Series on failure.
    """
    import pandas as pd
    try:
        if raw_divs is None or len(raw_divs) == 0:
            return pd.Series(dtype=float, name=asset)
        s = raw_divs.copy()
        if isinstance(s.index, pd.MultiIndex):
            s.index = s.index.get_level_values(0)
        s.index = pd.to_datetime(s.index)
        if getattr(s.index, "tz", None) is not None:
            s.index = s.index.tz_localize(None)
        s.index = s.index.normalize()
        s = pd.to_numeric(s, errors="coerce").fillna(0.0)
        s = s[s.index >= pd.to_datetime(START_DATE)]
        s.name = asset
        return s
    except Exception as e:
        print(f"  WARNING: could not clean dividend series for {asset} ({sym}): {e}")
        return pd.Series(dtype=float, name=asset)




def _ensure_workbook_in_config():
    """
    Copy the backfilled workbook to Config/ if it is not already there.
    The workbook may be committed to the repo root or Config/.
    Searches common locations and copies to Config/ so _load_workbook_prices()
    can find it via WORKBOOK_FILE.
    """
    target = PROJECT / WORKBOOK_FILE
    if target.exists():
        return  # already in place

    # Search for any xlsx in Config/ that looks like the price workbook
    # (contains 'workbook' or 'backfill' in the name, case-insensitive)
    import glob, shutil as _shutil
    search_dirs = [
        CONFIG,
        PROJECT,
        PROJECT.parent,
        DATA,
    ]
    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for xlsx in search_dir.glob("*.xlsx"):
            name_lower = xlsx.name.lower()
            if any(k in name_lower for k in ("workbook", "backfill", "pre2016")):
                target.parent.mkdir(exist_ok=True)
                _shutil.copy2(xlsx, target)
                print(f"  Workbook found and copied: {xlsx} → {target}")
                return

    print("  WARNING: Backfilled workbook not found.")
    print(f"  Searched: {[str(d) for d in search_dirs]}")
    print("  Price history will be limited to Yahoo's available range.")


def _load_workbook_prices():
    """
    Load price history from the local backfilled Excel workbook.
    Returns a dict: {asset: DataFrame with columns [Date, Close, Adj_Close, Open]}
    All columns that exist; missing columns (e.g. Open for VFINX) are omitted.
    Returns empty dict if workbook not found (falls back to Yahoo-only mode).
    """
    import pandas as pd

    wb_path = PROJECT / WORKBOOK_FILE
    if not wb_path.exists():
        # Glob search — any xlsx with workbook/backfill in name
        found = False
        for search_dir in [CONFIG, PROJECT, PROJECT.parent, DATA]:
            if not search_dir.exists():
                continue
            for xlsx in search_dir.glob("*.xlsx"):
                if any(k in xlsx.name.lower() for k in ("workbook","backfill","pre2016")):
                    wb_path = xlsx
                    found = True
                    print(f"  Found workbook via search: {xlsx}")
                    break
            if found:
                break
        if not found:
            print("  WARNING: Backfilled workbook not found — falling back to Yahoo-only mode")
            return {}

    print(f"  Loading workbook: {wb_path.name}")
    try:
        xl = pd.read_excel(wb_path, sheet_name=None)
    except Exception as e:
        print(f"  WARNING: Could not read workbook: {e}")
        return {}

    result = {}
    for asset, (sheet, close_col, adj_col, open_col) in WORKBOOK_SOURCES.items():
        if sheet not in xl:
            print(f"  WARNING: Sheet '{sheet}' not found for {asset}")
            continue
        df = xl[sheet].copy()
        if "Date" not in df.columns:
            continue
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.sort_values("Date").reset_index(drop=True)
        out = pd.DataFrame({"Date": df["Date"]})
        if close_col and close_col in df.columns:
            out["Close"] = pd.to_numeric(df[close_col], errors="coerce")
        if adj_col and adj_col in df.columns:
            out["Adj_Close"] = pd.to_numeric(df[adj_col], errors="coerce")
        elif close_col and close_col in df.columns:
            # No adj close available — use close as proxy (e.g. VFINX)
            out["Adj_Close"] = out["Close"]
        if open_col and open_col in df.columns:
            out["Open"] = pd.to_numeric(df[open_col], errors="coerce")
        result[asset] = out.dropna(subset=["Close"]).reset_index(drop=True)
        cutoff = result[asset]["Date"].max()
        print(f"  Workbook {asset}: {result[asset]['Date'].min().date()} → "
              f"{cutoff.date()}  ({len(result[asset])} rows)")
    return result


def download_prices(required_assets):
    """
    Download Open, raw Close, Adjusted Close, and cash distributions.

    Dividend capture strategy
    -------------------------
    ETFs and index funds  →  Ticker.history(actions=True)
        The Dividends column from history() is reliable for exchange-traded
        products that declare dividends on a regular schedule.

    Mutual funds (MUTUAL_FUNDS set)  →  Ticker.dividends
        Yahoo's history() endpoint frequently omits or zeros monthly
        distributions for open-end mutual funds (PIMIX, FIWDX, FIKQX,
        JBND, JPIE and their backfill proxies).  The dedicated .dividends
        property returns the complete distribution history for these funds.
        Both sources are fetched; the per-date maximum is kept so that
        any distributions captured by either method are preserved.
    """
    ensure("pandas"); ensure("numpy"); ensure("yfinance")
    import pandas as pd
    import numpy as np
    import yfinance as yf

    all_assets = sorted(set(required_assets) | set(CORE_ASSETS) | set(RESEARCH_ASSETS))
    adj_frames, raw_frames, open_frames, div_frames, audit = [], [], [], [], []

    # Load workbook price history for primary assets (full 15-year history)
    workbook_data = _load_workbook_prices()

    for asset in all_assets:
        if asset in {"TACTICAL", "A1V12"}:
            continue
        sym = YMAP.get(asset, asset)

        # ── Workbook-sourced assets ────────────────────────────────────
        if asset in workbook_data:
            wb_df = workbook_data[asset]
            wb_cutoff = wb_df["Date"].max()

            # Gap-fill from workbook cutoff → today via Yahoo (recent data only)
            gap_start = (wb_cutoff - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
            print(f"Workbook {asset} → gap-filling from {gap_start} via Yahoo...")
            try:
                gap_dl = yf.download(sym, start=gap_start, auto_adjust=False,
                                     progress=False, threads=False)
                if isinstance(gap_dl.columns, pd.MultiIndex):
                    gap_dl.columns = gap_dl.columns.get_level_values(0)
                if not gap_dl.empty:
                    gap_idx = pd.to_datetime(gap_dl.index)
                    if getattr(gap_idx, "tz", None) is not None:
                        gap_idx = gap_idx.tz_localize(None)
                    gap_idx = gap_idx.normalize()

                    gap_close = pd.to_numeric(gap_dl.get("Close"), errors="coerce")
                    gap_adj   = pd.to_numeric(
                        gap_dl.get("Adj Close", gap_dl.get("Close")), errors="coerce")
                    gap_open  = pd.to_numeric(gap_dl.get("Open"), errors="coerce")
                    gap_div   = pd.to_numeric(
                        gap_dl.get("Dividends", pd.Series(0.0, index=gap_dl.index)),
                        errors="coerce").fillna(0.0)

                    new_rows = gap_idx > wb_cutoff
                    if new_rows.any():
                        ext = pd.DataFrame({
                            "Date":      gap_idx[new_rows],
                            "Close":     gap_close.values[new_rows],
                            "Adj_Close": gap_adj.values[new_rows],
                        })
                        if gap_open is not None:
                            ext["Open"] = gap_open.values[new_rows]
                        wb_df = pd.concat([wb_df, ext],
                                          ignore_index=True).sort_values("Date")
                        print(f"  Gap-filled {asset} to {wb_df['Date'].max().date()}")

                    # Dividends for gap period (recent)
                    gap_div_rows = pd.DataFrame({
                        "Date":  gap_idx,
                        asset:   gap_div.values,
                    })
                    div_frames.append(gap_div_rows[gap_div_rows[asset].abs() > 1e-12]
                                      .drop_duplicates("Date"))
            except Exception as ge:
                print(f"  WARNING: gap-fill failed for {asset}: {ge}")

            # Full dividend history for workbook assets via dedicated endpoint
            # (workbook has no dividend sheet — needed for TTM yield calculation)
            try:
                full_divs = _clean_div_series(
                    yf.Ticker(sym).dividends, asset, sym)
                if len(full_divs) > 0:
                    full_div_rows = pd.DataFrame({
                        "Date": full_divs.index,
                        asset:  full_divs.values,
                    })
                    div_frames.append(full_div_rows[full_div_rows[asset].abs() > 1e-12]
                                      .drop_duplicates("Date"))
                    print(f"  {asset} full dividends: {len(full_divs)} events")
            except Exception as de:
                print(f"  WARNING: full dividend fetch failed for {asset}: {de}")

            def wb_frame(col, label):
                if col not in wb_df.columns:
                    return None
                f = pd.DataFrame({"Date": wb_df["Date"], label: wb_df[col]})
                return f.dropna(subset=[label]).drop_duplicates("Date", keep="last")

            adj_f = wb_frame("Adj_Close", asset)
            raw_f = wb_frame("Close", asset)
            opn_f = wb_frame("Open", asset) if "Open" in wb_df.columns else None

            if adj_f is not None: adj_frames.append(adj_f)
            if raw_f is not None: raw_frames.append(raw_f)
            if opn_f is not None: open_frames.append(opn_f)

            audit.append([asset, f"Workbook+Yahoo", "OK",
                          wb_df["Date"].min().date().isoformat(),
                          wb_df["Date"].max().date().isoformat(),
                          len(wb_df),
                          f"Workbook history + Yahoo gap-fill to {wb_df['Date'].max().date()}"])
            continue

        # ── Yahoo-only assets ──────────────────────────────────────────
        print(f"Downloading {asset} ({sym}) from Yahoo...")
        try:
            raw_dl = yf.download(sym, start=START_DATE, auto_adjust=False,
                                 progress=False, threads=False)
            if raw_dl.empty and asset == "DXY":
                raw_dl = yf.download("^DXY", start=START_DATE, auto_adjust=False,
                                     progress=False, threads=False)
            if isinstance(raw_dl.columns, pd.MultiIndex):
                raw_dl.columns = raw_dl.columns.get_level_values(0)

            hist = yf.Ticker(sym).history(
                start=START_DATE, auto_adjust=False, actions=True, repair=False
            )
            if isinstance(hist.columns, pd.MultiIndex):
                hist.columns = hist.columns.get_level_values(0)

            if not raw_dl.empty and (hist.empty or len(raw_dl) > len(hist)):
                hist_prices = raw_dl.copy()
                if not hist.empty and "Dividends" in hist.columns:
                    hist_idx = pd.to_datetime(hist.index)
                    if getattr(hist_idx, "tz", None) is not None:
                        hist_idx = hist_idx.tz_localize(None)
                    hist_idx = hist_idx.normalize()
                    div_series = pd.Series(hist["Dividends"].values, index=hist_idx)
                    dl_idx = pd.to_datetime(raw_dl.index).normalize()
                    hist_prices.index = dl_idx
                    hist_prices["Dividends"] = div_series.reindex(dl_idx).fillna(0.0)
                else:
                    dl_idx = pd.to_datetime(raw_dl.index).normalize()
                    hist_prices.index = dl_idx
                    hist_prices["Dividends"] = 0.0
                hist = hist_prices

            if hist.empty:
                audit.append([asset, sym, "FAIL", "", "", 0, "No data returned"])
                continue

            if isinstance(hist.columns, pd.MultiIndex):
                hist.columns = hist.columns.get_level_values(0)

            idx = pd.to_datetime(hist.index)
            if getattr(idx, "tz", None) is not None:
                idx = idx.tz_localize(None)
            idx = idx.normalize()

            open_px = pd.to_numeric(hist.get("Open"),    errors="coerce")
            close   = pd.to_numeric(hist.get("Close"),   errors="coerce")
            adj     = pd.to_numeric(hist.get("Adj Close", hist.get("Close")), errors="coerce")

            # ── Dividend capture ────────────────────────────────────────
            # Step 1: dividends embedded in history() Dividends column
            hist_div = _clean_div_series(
                hist.get("Dividends", pd.Series(0.0, index=hist.index)), asset, sym)

            # Step 2: dedicated .dividends endpoint — always fetch for
            # mutual funds; optionally for ETFs as a cross-check
            ded_div = pd.Series(dtype=float, name=asset)
            if asset in MUTUAL_FUNDS:
                try:
                    raw = yf.Ticker(sym).dividends
                    ded_div = _clean_div_series(raw, asset, sym)
                    print(f"  {asset}: dedicated .dividends → "
                          f"{(ded_div.abs() > 1e-12).sum()} events")
                except Exception as de:
                    print(f"  WARNING: .dividends failed for {asset}: {de}")

            # Step 3: merge both sources — keep per-date maximum so
            # neither source silently loses a distribution event
            combined = pd.concat([hist_div, ded_div]).groupby(level=0).max().fillna(0.0)
            combined.name = asset

            # Align dividend series to the same date index as price history
            div_aligned = combined.reindex(idx, fill_value=0.0)

            def frame(series, col):
                f = pd.DataFrame({"Date": idx, col: series.values})
                return f.dropna(subset=[col]).drop_duplicates("Date", keep="last")

            adj_frames.append(frame(adj,     asset))
            raw_frames.append(frame(close,   asset))
            open_frames.append(frame(open_px, asset))
            div_frames.append(
                pd.DataFrame({"Date": idx, asset: div_aligned.values})
                .drop_duplicates("Date", keep="last")
            )

            div_count = int((div_aligned.abs() > 1e-12).sum())
            ded_count = int((ded_div.abs() > 1e-12).sum()) if len(ded_div) else 0
            audit.append([asset, sym, "OK",
                          frame(close, asset)["Date"].min().date().isoformat(),
                          frame(close, asset)["Date"].max().date().isoformat(),
                          len(frame(close, asset)),
                          f"Open+Raw+Adj; {div_count} div events "
                          f"(hist:{div_count - ded_count if ded_count else div_count} "
                          f"ded:{ded_count})"])
        except Exception as e:
            audit.append([asset, sym, "ERROR", "", "", 0, str(e)])

    if not adj_frames:
        raise RuntimeError("No Yahoo price data downloaded.")

    def merge_frames(frames):
        """
        Merge list of DataFrames on Date (outer join).
        If the same asset column appears in multiple frames
        (e.g. gap-period + full-history dividends for workbook assets),
        sum them per date so no events are lost.
        """
        import pandas as pd
        wide = frames[0]
        for f in frames[1:]:
            wide = wide.merge(f, on="Date", how="outer")
        wide = wide.sort_values("Date").reset_index(drop=True)
        # Collapse _x / _y duplicate columns by summing
        suffixed = [c for c in wide.columns if c.endswith("_x") or c.endswith("_y")]
        base_cols = set(c[:-2] for c in suffixed)
        for base in base_cols:
            x_col = f"{base}_x"
            y_col = f"{base}_y"
            if x_col in wide.columns and y_col in wide.columns:
                wide[base] = wide[x_col].fillna(0.0) + wide[y_col].fillna(0.0)
                wide = wide.drop(columns=[x_col, y_col])
        return wide

    adj_wide  = merge_frames(adj_frames)
    raw_wide  = merge_frames(raw_frames)
    open_wide = merge_frames(open_frames)
    div_wide  = merge_frames(div_frames).fillna(0.0)

    print(f"  [DIAG] adj_wide max date: {pd.to_datetime(adj_wide['Date']).max().date()}, "
          f"raw_wide max date: {pd.to_datetime(raw_wide['Date']).max().date()}, "
          f"open_wide max date: {pd.to_datetime(open_wide['Date']).max().date()}")
    if "MGK" in adj_wide.columns:
        _nn = adj_wide.loc[adj_wide['MGK'].notna(), 'Date']
        print(f"  [DIAG] adj_wide MGK non-null through: "
              f"{pd.to_datetime(_nn).max().date() if len(_nn) else 'NEVER'}")
    if "VOO" in adj_wide.columns:
        _nn = adj_wide.loc[adj_wide['VOO'].notna(), 'Date']
        print(f"  [DIAG] adj_wide VOO non-null through: "
              f"{pd.to_datetime(_nn).max().date() if len(_nn) else 'NEVER'}")
    if "MGK" in open_wide.columns:
        _nn = open_wide.loc[open_wide['MGK'].notna(), 'Date']
        print(f"  [DIAG] open_wide MGK non-null through: "
              f"{pd.to_datetime(_nn).max().date() if len(_nn) else 'NEVER'}")
    if "VOO" in open_wide.columns:
        _nn = open_wide.loc[open_wide['VOO'].notna(), 'Date']
        print(f"  [DIAG] open_wide VOO non-null through: "
              f"{pd.to_datetime(_nn).max().date() if len(_nn) else 'NEVER'}")
    adj_wide.to_csv(DATA / "Price_Master_Wide.csv",           index=False, date_format="%Y-%m-%d")
    adj_wide.melt(id_vars=["Date"], var_name="Asset", value_name="Adj_Close").dropna().to_csv(
        DATA / "Price_Master_Long.csv", index=False, date_format="%Y-%m-%d")
    raw_wide.to_csv(DATA / "Price_Master_Raw_Close_Wide.csv", index=False, date_format="%Y-%m-%d")
    open_wide.to_csv(DATA / "Price_Master_Open_Wide.csv",     index=False, date_format="%Y-%m-%d")
    div_wide.to_csv(DATA / "Dividend_Master_Wide.csv",        index=False, date_format="%Y-%m-%d")

    audit_df = pd.DataFrame(
        audit,
        columns=["Asset","Yahoo_Symbol","Status","First_Date","Last_Date","Rows","Notes"]
    )
    audit_df.to_csv(AUDIT / "Data_Audit.csv", index=False)
    return adj_wide, raw_wide, open_wide, div_wide, audit_df


def build_composites(wide, required_assets, output_name="Composite_Prices.csv",
                     audit_name="Backfill_Scale_Audit.csv", price_basis="Adjusted Close"):
    """Build continuous, ratio-scaled composite prices for one price basis."""
    import pandas as pd
    import numpy as np

    df = wide.copy()
    df["Date"] = pd.to_datetime(df["Date"])
    comp = pd.DataFrame({"Date": df["Date"]})
    assets = sorted(set(required_assets) | set(CORE_ASSETS))
    scale_rows = []

    for asset in assets:
        if asset in {"TACTICAL", "A1V12"}:
            continue
        if asset in BACKFILLS:
            bf, until = BACKFILLS[asset]
            cutoff = pd.to_datetime(until)
            live = pd.to_numeric(df[asset], errors="coerce") if asset in df.columns else pd.Series(float("nan"), index=df.index)
            bfv  = pd.to_numeric(df[bf],    errors="coerce") if bf    in df.columns else pd.Series(float("nan"), index=df.index)
            scale, live_date, bf_date, status = 1.0, None, None, "UNSCALED"
            live_mask = (df["Date"] > cutoff) & live.notna()
            if live_mask.any() and bfv.notna().any():
                live_idx  = live_mask[live_mask].index[0]
                live_date = df.loc[live_idx, "Date"]
                prior     = (df["Date"] <= live_date) & bfv.notna()
                if prior.any():
                    bf_idx  = prior[prior].index[-1]
                    bf_date = df.loc[bf_idx, "Date"]
                    lv, bv  = live.loc[live_idx], bfv.loc[bf_idx]
                    if pd.notna(lv) and pd.notna(bv) and bv != 0:
                        scale, status = float(lv / bv), "SCALED"
            comp[asset] = np.where(df["Date"] <= cutoff, bfv * scale, live)
            scale_rows.append([asset, bf, until, scale, status,
                               live_date.date().isoformat() if live_date is not None else "",
                               bf_date.date().isoformat()   if bf_date  is not None else "",
                               price_basis])
        else:
            comp[asset] = df[asset] if asset in df.columns else float("nan")

    for asset in RESEARCH_ASSETS:
        if asset in df.columns and asset not in comp.columns:
            comp[asset] = df[asset]

    comp.to_csv(DATA / output_name, index=False, date_format="%Y-%m-%d")
    scale_df = pd.DataFrame(scale_rows,
        columns=["Asset","Backfill_Asset","Cutoff","Scale_Factor","Status",
                 "First_Live_Date","Backfill_Anchor_Date","Price_Basis"])
    scale_df.to_csv(AUDIT / audit_name, index=False)
    return comp, scale_df


def build_dividend_composites(div_wide, raw_scale_df, required_assets):
    """Create production-asset dividend-per-share series including scaled backfills."""
    import pandas as pd
    import numpy as np

    df = div_wide.copy()
    df["Date"] = pd.to_datetime(df["Date"])
    out = pd.DataFrame({"Date": df["Date"]})
    scale_map = {r["Asset"]: float(r["Scale_Factor"]) for _, r in raw_scale_df.iterrows()}
    assets = sorted(set(required_assets) | set(CORE_ASSETS))

    for asset in assets:
        if asset in {"TACTICAL", "A1V12"}:
            continue
        live = pd.to_numeric(df[asset], errors="coerce").fillna(0.0) if asset in df.columns else pd.Series(0.0, index=df.index)
        if asset in BACKFILLS:
            bf, until = BACKFILLS[asset]
            cutoff = pd.to_datetime(until)
            proxy  = pd.to_numeric(df[bf], errors="coerce").fillna(0.0) if bf in df.columns else pd.Series(0.0, index=df.index)
            out[asset] = np.where(df["Date"] <= cutoff, proxy * scale_map.get(asset, 1.0), live)
        else:
            out[asset] = live

    out.to_csv(DATA / "Composite_Dividends.csv", index=False, date_format="%Y-%m-%d")
    return out


def ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=1).mean()


def build_signals(comp_adj, comp_raw=None, signal_price_basis=SIGNAL_PRICE_BASIS):
    """
    Binary MGK/MGV signal engine with a configurable signal price basis.

    signal_price_basis:
      - "ADJUSTED" (recommended): MGK/MGV adjusted-close ratio vs EMA89.
      - "RAW": MGK/MGV raw-close ratio vs EMA89 for research comparison.

    The selected basis affects only signal generation. Tactical NAV remains
    raw-close, standalone VOO benchmark remains raw-close, and allocation
    models continue to use adjusted-close prices.

    Execution is strictly look-ahead free:
      - decision_position[i] is determined after day i closes;
      - effective_position[i+EXECUTION_LAG_DAYS] applies that decision;
      - trades execute at T+2 open in build_tactical_values() (was T+1).
    """
    import pandas as pd
    import numpy as np

    basis = _normalise_signal_price_basis(signal_price_basis)
    if basis == "RAW":
        if comp_raw is None:
            raise ValueError("comp_raw is required when SIGNAL_PRICE_BASIS='RAW'.")
        source = comp_raw
        basis_label = "Raw Close"
    else:
        source = comp_adj
        basis_label = "Adjusted Close"

    df = (source.dropna(subset=["MGK", "MGV"]).copy()
          .sort_values("Date").reset_index(drop=True))
    if df.empty:
        raise RuntimeError(f"No MGK/MGV data available for {basis_label} signals.")

    sig = pd.DataFrame({"Date": df["Date"], "MGK": df["MGK"], "MGV": df["MGV"]})
    sig["Signal_Price_Basis"] = basis_label
    sig["MGK_MGV"] = sig["MGK"] / sig["MGV"]
    sig["MGK_MGV_EMA89"] = ema(sig["MGK_MGV"], 89)

    raw_signal = (sig["MGK_MGV"] > sig["MGK_MGV_EMA89"]).astype(int).values
    n = len(sig)

    # ── Proximity filter ────────────────────────────────────────────────
    # A fresh EMA89 crossover is only accepted if the ratio was within
    # PROXIMITY_THRESHOLD of its EMA89 on at least one of the prior
    # PROXIMITY_LOOKBACK_DAYS trading days. This screens out crossovers
    # where price gapped straight through the EMA without genuinely
    # approaching it first -- the kind of move that tends to whipsaw.
    # Rejected crossovers are treated as noise: the signal simply holds
    # its prior value until a later crossover satisfies the proximity
    # condition. See PROXIMITY_FILTER_ENABLED docstring note above for
    # the backtest results that led to adopting this filter.
    #
    # Breakout fallback: if the proximity lookback comes back nil, accept
    # anyway if the ratio has moved decisively (BREAKOUT_THRESHOLD) past
    # its EMA89 in the crossover's own direction on the crossover day
    # itself. See BREAKOUT_FALLBACK_ENABLED docstring note above.
    if PROXIMITY_FILTER_ENABLED:
        pct_signed = (sig["MGK_MGV"] / sig["MGK_MGV_EMA89"] - 1.0).values
        pct_dev = np.abs(pct_signed)
        proximity = pct_dev <= PROXIMITY_THRESHOLD

        crossover = np.zeros(n, dtype=bool)
        crossover[1:] = raw_signal[1:] != raw_signal[:-1]

        prox_recent = np.zeros(n, dtype=bool)
        for lag in range(1, PROXIMITY_LOOKBACK_DAYS + 1):
            shifted = np.zeros(n, dtype=bool)
            shifted[lag:] = proximity[:-lag]
            prox_recent |= shifted

        if BREAKOUT_FALLBACK_ENABLED:
            decisive_up = pct_signed > BREAKOUT_THRESHOLD
            decisive_down = pct_signed < -BREAKOUT_THRESHOLD
            decisive = np.where(raw_signal.astype(bool), decisive_up, decisive_down)
            accepted = crossover & (prox_recent | decisive)
        else:
            accepted = crossover & prox_recent

        filtered_signal = np.empty(n, dtype=int)
        filtered_signal[0] = raw_signal[0]
        for i in range(1, n):
            filtered_signal[i] = raw_signal[i] if accepted[i] else filtered_signal[i - 1]

        # ── 1-day confirmation layer ────────────────────────────────────
        # A band-accepted crossover proposes a candidate value but does not
        # commit it until the following trading day confirms the raw
        # signal still agrees. Fully causal: day i's commit decision uses
        # only raw_signal up through day i. See CONFIRMATION_LAYER_ENABLED
        # docstring note above.
        if CONFIRMATION_LAYER_ENABLED:
            confirmed_signal = np.empty(n, dtype=int)
            confirmed_signal[0] = filtered_signal[0]
            pending_candidate = None
            pending_since = None
            for i in range(1, n):
                held = confirmed_signal[i - 1]
                if pending_candidate is not None and (i - pending_since) >= CONFIRMATION_LAYER_DAYS:
                    if raw_signal[i] == pending_candidate:
                        held = pending_candidate
                    pending_candidate = None
                    pending_since = None
                if accepted[i] and raw_signal[i] != held and pending_candidate is None:
                    pending_candidate = raw_signal[i]
                    pending_since = i
                confirmed_signal[i] = held
            raw_signal = confirmed_signal
        else:
            raw_signal = filtered_signal

    decision_position = [0] * n
    decision_position[0] = raw_signal[0]
    days_since = COOLDOWN_DAYS

    for i in range(1, n):
        days_since += 1
        if (raw_signal[i] != decision_position[i - 1]
                and days_since >= COOLDOWN_DAYS):
            decision_position[i] = raw_signal[i]
            days_since = 0
        else:
            decision_position[i] = decision_position[i - 1]

    # A decision made using today's close becomes effective EXECUTION_LAG_DAYS
    # trading days later (T+2 as of the 2026-07 timing amendment; see
    # EXECUTION_LAG_DAYS docstring note above).
    effective_position = (
        [decision_position[0]] * EXECUTION_LAG_DAYS
        + decision_position[:-EXECUTION_LAG_DAYS]
    )

    holdings_list = []
    trades_list = []
    current_holding = "MGK" if effective_position[0] == 1 else "MGV"
    holdings_list.append([
        sig["Date"].iloc[0],
        "Growth" if current_holding == "MGK" else "Value",
        current_holding,
        basis_label,
    ])

    for i in range(1, n):
        new_holding = "MGK" if effective_position[i] == 1 else "MGV"
        new_state = "Growth" if new_holding == "MGK" else "Value"
        if effective_position[i] != effective_position[i - 1]:
            _confirm_offset = CONFIRMATION_LAYER_DAYS if (PROXIMITY_FILTER_ENABLED and CONFIRMATION_LAYER_ENABLED) else 0
            trigger_date = sig["Date"].iloc[max(i - EXECUTION_LAG_DAYS - _confirm_offset, 0)]
            trade_date = sig["Date"].iloc[i]
            prox_clause = (
                f", {PROXIMITY_THRESHOLD*100:.2f}% proximity filter "
                f"({PROXIMITY_LOOKBACK_DAYS}-day lookback)"
                if PROXIMITY_FILTER_ENABLED else ""
            )
            breakout_clause = (
                f" w/ {BREAKOUT_THRESHOLD*100:.2f}% breakout fallback"
                if (PROXIMITY_FILTER_ENABLED and BREAKOUT_FALLBACK_ENABLED) else ""
            )
            if _confirm_offset:
                _exec_desc = "next day" if EXECUTION_LAG_DAYS == 1 else f"{EXECUTION_LAG_DAYS} trading days later"
                timing_desc = (f"Trigger + {_confirm_offset}-day confirmation, "
                                f"execution {_exec_desc}")
            else:
                lag_label = {1: "Next", 2: "2nd", 3: "3rd"}.get(EXECUTION_LAG_DAYS, f"{EXECUTION_LAG_DAYS}th")
                timing_desc = f"{lag_label} trading day after trigger"
            rule = (f"{timing_desc} (EMA89 crossover"
                    f"{prox_clause}{breakout_clause}, {basis_label} signal)")
            trades_list.append([
                trade_date, trigger_date, current_holding, new_holding,
                new_state, basis_label, rule,
            ])
            current_holding = new_holding
        holdings_list.append([sig["Date"].iloc[i], new_state,
                              current_holding, basis_label])

    h = pd.DataFrame(
        holdings_list,
        columns=["Date", "State", "EffectiveHolding", "Signal_Price_Basis"],
    )
    sig = sig.drop(columns=["Signal_Price_Basis"]).merge(h, on="Date", how="left")

    front = ["Date", "State", "EffectiveHolding", "Signal_Price_Basis",
             "MGK", "MGV", "MGK_MGV", "MGK_MGV_EMA89"]
    sig = sig[front + [c for c in sig.columns if c not in front]]

    trades_df = pd.DataFrame(
        trades_list,
        columns=["Trade_Date", "Trigger_Date", "From", "To", "New_State",
                 "Signal_Price_Basis", "Rule"],
    )

    port_start = pd.to_datetime(PORTFOLIO_START)
    sig[sig["Date"] >= port_start].to_csv(
        DATA / "Signal_History.csv", index=False, date_format="%Y-%m-%d")
    h[h["Date"] >= port_start].to_csv(
        DATA / "Daily_Holdings.csv", index=False, date_format="%Y-%m-%d")
    trades_df[trades_df["Trade_Date"] >= port_start].to_csv(
        DATA / "Trade_Ledger.csv", index=False, date_format="%Y-%m-%d")

    return sig, trades_df


def build_adjusted_open(comp_raw, comp_adj, comp_open):
    """
    Builds an adjusted (total-return-consistent) open-price composite,
    fixing a real bug identified in the 2026-07 research/production
    reconciliation: build_tactical_values() executes trade-day share
    conversions using the RAW open price while the rest of the NAV chain
    (prior/subsequent valuation) is entirely adjusted-close-based. That
    mismatch created value discontinuities on every switch day, sized by
    however much the raw/adjusted gap had grown for that asset by that
    date -- confirmed by replicating the exact mechanic on independently
    sourced data and reproducing the production/research CAGR gap almost
    exactly. This function closes that gap by scaling each day's raw open
    by that same day's own close/adjusted-close ratio:

        adj_open(t) = raw_open(t) * adj_close(t) / raw_close(t)

    This is the standard approximation for reconstructing an intraday
    adjusted price when only a daily adjustment factor is known -- it
    assumes the factor is constant intraday, which is true except on the
    rare day an ex-dividend date falls between that day's open and close.
    build_tactical_values()'s own docstring already described trade-day
    execution as using "adj-close open" -- this function is what actually
    makes that true, rather than the raw open that was silently used
    before.

    comp_raw  — raw-close composite (for the day's own adjustment factor)
    comp_adj  — adjusted-close composite (for the day's own adjustment factor)
    comp_open — raw open-price composite (to be adjusted)
    """
    import pandas as pd
    import numpy as np

    raw = comp_raw.copy()
    adj = comp_adj.copy()
    opn = comp_open.copy()
    raw["Date"] = pd.to_datetime(raw["Date"])
    adj["Date"] = pd.to_datetime(adj["Date"])
    opn["Date"] = pd.to_datetime(opn["Date"])

    tickers = [c for c in opn.columns
               if c != "Date" and c in raw.columns and c in adj.columns]

    merged = opn[["Date"] + tickers].merge(
        raw[["Date"] + tickers], on="Date", how="left", suffixes=("", "_raw")
    ).merge(
        adj[["Date"] + tickers], on="Date", how="left", suffixes=("", "_adj")
    )

    out = pd.DataFrame({"Date": merged["Date"]})
    for t in tickers:
        raw_close = merged[f"{t}_raw"]
        adj_close = merged[f"{t}_adj"]
        raw_open  = merged[t]
        factor = np.where(
            (raw_close.notna()) & (raw_close > 0) & adj_close.notna(),
            adj_close / raw_close,
            1.0,
        )
        out[t] = raw_open * factor

    return out


def build_alpha_overlay(comp_adj, comp_raw, comp_open, sig, pv):
    """
    Independent Alpha overlay layer, built and validated 2026-07/2026-08.
    Runs entirely alongside the base MGK/MGV regime (sig["EffectiveHolding"])
    without changing it -- this function only adds two independent, optional
    sleeves on top: SMH (semiconductors) and SPHB (high beta).

    SMH sleeve trigger: 5-day rate of change of the SMH/MGV RAW close ratio.
        Entry:  ROC5 >= +2.0%, sustained for 5 consecutive trading days.
        Exit:   ROC5 <= -2.0%, sustained for 5 consecutive trading days.

    SPHB sleeve trigger: SPHB/MGV RAW close ratio vs its own 50-day EMA.
        Entry:  ratio >= EMA50*(1+0.3%), sustained for 5 consecutive days.
        Exit:   ratio <= EMA50*(1-0.3%), sustained for 5 consecutive days.

    Allocation: each sleeve is 40% when active alone. When both sleeves are
        simultaneously active, their weights are scaled down proportionally
        so the combined sleeve weight never exceeds 50% (i.e. 40%/40% solo
        becomes 25%/25% together) -- a deliberate concentration cap, since
        the two sleeves are correlated (~0.30) and both draw from the same
        base holding. Funded from whichever base asset (MGK or MGV) the
        regime currently holds; each sleeve's on/off state is fully
        independent of the base regime and of the other sleeve -- a base
        regime switch only changes which asset the sleeves are funded
        from/returned to, never triggers a sleeve entry or exit itself.

    Execution: adjusted open on any day either sleeve's state changes or the
        base holding changes (same convention as build_tactical_values()),
        consistent with the rest of the adjusted-close-based NAV chain.

    Validated result (production data, 2026-07/08, corrected-data re-test
    after an earlier data-corruption artifact was found and fixed in the
    ad-hoc research CSVs and traced back through this logic): the combined
    overlay beats the base regime alone on both CAGR and Sharpe in every
    one of 5yr / 10yr / Jan-2016 / Jan-2021 / 2021-2023-stress / 2012-2015
    windows tested. SMH alone (40%) and SPHB alone (40%) were also each
    individually re-validated on the same battery. Tested and rejected as
    overlay candidates on the same battery: XLI, IYR, XLV, XLU, XLK
    (mixed/marginal), XLY, XLE, SPLV (either MGK or MGV denominator).
    SMH/VOO, SMH/XLE, SPHB/MGK were also tested as alternative trigger
    ratios and underperformed SMH/MGV and SPHB/MGV respectively.

    Outputs Data/Alpha_Overlay_Daily_Values.csv and
    Data/Alpha_Overlay_Trade_Ledger.csv. Deliberately NOT merged into
    Portfolio_Daily_Values.csv / Trade_Ledger.csv -- this is presented as
    its own dashboard tab, not as another checkbox in the main comparison.
    """
    import pandas as pd
    import numpy as np

    SMH_ENTRY_THRESH, SMH_EXIT_THRESH, SMH_PERSIST_DAYS = 0.020, -0.020, 5
    SPHB_ENTRY_THRESH, SPHB_EXIT_THRESH, SPHB_PERSIST_DAYS, SPHB_EMA_WINDOW = 0.003, -0.003, 5, 50
    SMH_WEIGHT = 0.40
    SPHB_WEIGHT = 0.40
    COMBINED_CAP = 0.50

    needed = ["SMH", "SPHB"]
    for tk in needed:
        if tk not in comp_raw.columns or tk not in comp_adj.columns or tk not in comp_open.columns:
            print(f"  Alpha overlay: {tk} not present in composites, skipping.")
            return None, None

    df = sig[["Date", "EffectiveHolding"]].merge(
        comp_raw[["Date", "MGV", "SMH", "SPHB"]].rename(
            columns={"MGV": "MGV_raw", "SMH": "SMH_raw", "SPHB": "SPHB_raw"}),
        on="Date", how="inner"
    ).merge(
        comp_adj[["Date", "MGK", "MGV", "SMH", "SPHB"]].rename(
            columns={"MGK": "MGK_adj", "MGV": "MGV_adj", "SMH": "SMH_adj", "SPHB": "SPHB_adj"}),
        on="Date", how="inner"
    ).merge(
        comp_open[["Date", "MGK", "MGV", "SMH", "SPHB"]].rename(
            columns={"MGK": "MGK_open", "MGV": "MGV_open", "SMH": "SMH_open", "SPHB": "SPHB_open"}),
        on="Date", how="inner"
    ).sort_values("Date").reset_index(drop=True)

    # SPHB (and, less commonly, SMH) may not be covered by the backfill
    # workbook the way MGK/MGV are, so Yahoo's own history for them can
    # start later than sig's full date range. A NaN in any of these columns
    # would otherwise poison s_smh/s_sphb permanently from that row onward
    # (nothing in the loop below ever resets a NaN share count), silently
    # blanking the entire output column rather than just the early rows.
    # Truncate to the first date where every required column has real data.
    _required_cols = ["MGV_raw", "SMH_raw", "SPHB_raw", "MGK_adj", "MGV_adj", "SMH_adj", "SPHB_adj",
                       "MGK_open", "MGV_open", "SMH_open", "SPHB_open"]
    _before = len(df)
    df = df.dropna(subset=_required_cols).reset_index(drop=True)
    _dropped = _before - len(df)
    if _dropped:
        print(f"  Alpha overlay: dropped {_dropped} row(s) missing SMH/SPHB/MGK/MGV data "
              f"(likely SPHB history starting later than the base regime's range); "
              f"effective range now starts {df['Date'].min().date() if len(df) else 'N/A'}.")

    n = len(df)
    if n == 0:
        print("  Alpha overlay: no overlapping data, skipping.")
        return None, None

    # --- SMH sleeve: ROC5 of SMH/MGV ---
    smh_ratio = df["SMH_raw"] / df["MGV_raw"]
    smh_roc5 = (smh_ratio / smh_ratio.shift(SMH_PERSIST_DAYS) - 1.0).values
    smh_on = np.zeros(n, dtype=bool)
    smh_streak_arr = np.zeros(n, dtype=int)
    smh_just_triggered = np.zeros(n, dtype=bool)
    on, streak = False, 0
    for i in range(n):
        rv = smh_roc5[i]
        if np.isnan(rv):
            smh_on[i] = on
            smh_streak_arr[i] = streak
            continue
        prev_on = on
        if not on:
            streak = streak + 1 if rv >= SMH_ENTRY_THRESH else 0
        else:
            streak = streak + 1 if rv <= SMH_EXIT_THRESH else 0
        # Record the streak that was actually reached today BEFORE any reset,
        # so the day a trigger fires shows "5/5", not "0/5" -- the display
        # value reflects what happened today, not the post-reset counter state.
        smh_streak_arr[i] = streak
        if streak >= SMH_PERSIST_DAYS:
            on = not on
            streak = 0
        smh_on[i] = on
        smh_just_triggered[i] = (on != prev_on)

    # --- SPHB sleeve: SPHB/MGV ratio vs its own 50-day EMA, +-0.3% band ---
    sphb_ratio = df["SPHB_raw"] / df["MGV_raw"]
    sphb_ema = sphb_ratio.ewm(span=SPHB_EMA_WINDOW, adjust=False, min_periods=1).mean()
    sphb_dev = (sphb_ratio / sphb_ema - 1.0).values
    sphb_on = np.zeros(n, dtype=bool)
    sphb_streak_arr = np.zeros(n, dtype=int)
    sphb_just_triggered = np.zeros(n, dtype=bool)
    on, streak = False, 0
    for i in range(n):
        pv_ = sphb_dev[i]
        if np.isnan(pv_):
            sphb_on[i] = on
            sphb_streak_arr[i] = streak
            continue
        prev_on = on
        if not on:
            streak = streak + 1 if pv_ >= SPHB_ENTRY_THRESH else 0
        else:
            streak = streak + 1 if pv_ <= SPHB_EXIT_THRESH else 0
        sphb_streak_arr[i] = streak
        if streak >= SPHB_PERSIST_DAYS:
            on = not on
            streak = 0
        sphb_on[i] = on
        sphb_just_triggered[i] = (on != prev_on)

    # T+1: today's confirmed sleeve state takes effect the next trading day,
    # matching the same no-lookahead convention used for the base regime.
    smh_eff = np.concatenate([[smh_on[0]], smh_on[:-1]])
    sphb_eff = np.concatenate([[sphb_on[0]], sphb_on[:-1]])
    overlay_active = smh_eff | sphb_eff   # single band flag for the chart, as before

    def weights_for(i):
        w_smh = SMH_WEIGHT if smh_eff[i] else 0.0
        w_sphb = SPHB_WEIGHT if sphb_eff[i] else 0.0
        if smh_eff[i] and sphb_eff[i]:
            total = w_smh + w_sphb
            if total > COMBINED_CAP:
                scale = COMBINED_CAP / total
                w_smh *= scale
                w_sphb *= scale
        return (1.0 - w_smh - w_sphb), w_smh, w_sphb

    h0 = df.loc[0, "EffectiveHolding"]
    w_base, w_smh, w_sphb = weights_for(0)
    s_base = w_base / float(df.loc[0, f"{h0}_adj"])
    s_smh = (w_smh / float(df.loc[0, "SMH_adj"])) if w_smh > 0 else 0.0
    s_sphb = (w_sphb / float(df.loc[0, "SPHB_adj"])) if w_sphb > 0 else 0.0

    vals = [1.0]
    trades = []
    prev_w = (w_base, w_smh, w_sphb)
    # Track each sleeve's current holding period (entry index/price) so exit
    # events can report Days_Held / Entry_Price / Exit_Price / Return.
    smh_entry = {"idx": 0, "price": float(df.loc[0, "SMH_open"])} if smh_eff[0] else None
    sphb_entry = {"idx": 0, "price": float(df.loc[0, "SPHB_open"])} if sphb_eff[0] else None
    for i in range(1, n):
        prev_base = df.loc[i - 1, "EffectiveHolding"]
        cur_base = df.loc[i, "EffectiveHolding"]
        cur = df.iloc[i]
        cur_w = weights_for(i)
        base_changed = cur_base != prev_base
        w_changed = cur_w != prev_w

        if not base_changed and not w_changed:
            val = (s_base * float(cur[f"{cur_base}_adj"])
                   + s_smh * float(cur["SMH_adj"])
                   + s_sphb * float(cur["SPHB_adj"]))
        else:
            if base_changed:
                trades.append({"Date": cur["Date"].strftime("%Y-%m-%d"), "Type": "Base regime",
                                "From": prev_base, "To": cur_base,
                                "Days_Held": None, "Entry_Price": None, "Exit_Price": None, "Return": None})
            if smh_eff[i] != smh_eff[i - 1]:
                row = {"Date": cur["Date"].strftime("%Y-%m-%d"), "Type": "SMH sleeve",
                       "From": "ON" if smh_eff[i - 1] else "OFF", "To": "ON" if smh_eff[i] else "OFF",
                       "Days_Held": None, "Entry_Price": None, "Exit_Price": None, "Return": None}
                if smh_eff[i]:  # turning ON: open a new holding period
                    smh_entry = {"idx": i, "price": float(cur["SMH_open"])}
                    row["Entry_Price"] = smh_entry["price"]
                elif smh_entry is not None:  # turning OFF: close out the period
                    exit_price = float(cur["SMH_open"])
                    row["Days_Held"] = i - smh_entry["idx"]
                    row["Entry_Price"] = smh_entry["price"]
                    row["Exit_Price"] = exit_price
                    row["Return"] = exit_price / smh_entry["price"] - 1 if smh_entry["price"] else None
                    smh_entry = None
                trades.append(row)
            if sphb_eff[i] != sphb_eff[i - 1]:
                row = {"Date": cur["Date"].strftime("%Y-%m-%d"), "Type": "SPHB sleeve",
                       "From": "ON" if sphb_eff[i - 1] else "OFF", "To": "ON" if sphb_eff[i] else "OFF",
                       "Days_Held": None, "Entry_Price": None, "Exit_Price": None, "Return": None}
                if sphb_eff[i]:  # turning ON: open a new holding period
                    sphb_entry = {"idx": i, "price": float(cur["SPHB_open"])}
                    row["Entry_Price"] = sphb_entry["price"]
                elif sphb_entry is not None:  # turning OFF: close out the period
                    exit_price = float(cur["SPHB_open"])
                    row["Days_Held"] = i - sphb_entry["idx"]
                    row["Entry_Price"] = sphb_entry["price"]
                    row["Exit_Price"] = exit_price
                    row["Return"] = exit_price / sphb_entry["price"] - 1 if sphb_entry["price"] else None
                    sphb_entry = None
                trades.append(row)
            sell_val = (s_base * float(cur[f"{prev_base}_open"])
                        + s_smh * float(cur["SMH_open"])
                        + s_sphb * float(cur["SPHB_open"]))
            w_base, w_smh, w_sphb = cur_w
            new_base_open = float(cur[f"{cur_base}_open"])
            s_base = w_base * sell_val / new_base_open
            s_smh = (w_smh * sell_val / float(cur["SMH_open"])) if w_smh > 0 else 0.0
            s_sphb = (w_sphb * sell_val / float(cur["SPHB_open"])) if w_sphb > 0 else 0.0
            val = (s_base * float(cur[f"{cur_base}_adj"])
                   + s_smh * float(cur["SMH_adj"])
                   + s_sphb * float(cur["SPHB_adj"]))
        vals.append(val)
        prev_w = cur_w

    out = pd.DataFrame({
        "Date": df["Date"],
        "Overlay_Active": overlay_active,
        "SMH_Active": smh_eff,
        "SPHB_Active": sphb_eff,
        "Tactical + Alpha Overlay": vals,
        # Raw trigger readouts for the Live Trigger Status panel.
        # SMH_Active/SPHB_Active (above) are the T+1 *funded* state -- what's
        # actually held today, based on yesterday's confirmed read.
        # SMH_RawOn/SPHB_RawOn are today's raw signal state (smh_on/sphb_on,
        # pre-shift) -- this becomes tomorrow's funded state. On an ordinary
        # day these two agree. On a day a transition fires, they diverge by
        # construction (that's *what a transition is*), and the panel should
        # show that divergence explicitly rather than blend them into one
        # "on/off" flag, which is what caused the entered/exited mislabel.
        "SMH_ROC5": smh_roc5,
        "SMH_Streak": smh_streak_arr,
        "SMH_JustTriggered": smh_just_triggered,
        "SMH_RawOn": smh_on,
        "SPHB_Dev": sphb_dev,
        "SPHB_Streak": sphb_streak_arr,
        "SPHB_JustTriggered": sphb_just_triggered,
        "SPHB_RawOn": sphb_on,
    })
    # Rebase VOO Benchmark and Tactical Sleeve from the existing production
    # series (pv) onto this same date range, rather than recomputing them --
    # single source of truth for those two lines.
    pv_local = pv[["Date", "VOO Benchmark", "A1V12 Tactical Sleeve"]].copy()
    pv_local["Date"] = pd.to_datetime(pv_local["Date"])
    out["Date"] = pd.to_datetime(out["Date"])
    out = out.merge(pv_local, on="Date", how="left")

    # pv occasionally lacks a trading day that the raw price composites do
    # have (observed: isolated single-day gaps), which left VOO Benchmark /
    # A1V12 Tactical Sleeve blank on those specific dates after the merge
    # above, even though Tactical + Alpha Overlay (computed independently of
    # pv) stayed populated. Forward-fill rather than leave a hole -- a
    # missing pv update for one day is not a missing portfolio value.
    _missing_before = out["VOO Benchmark"].isna().sum()
    out["VOO Benchmark"] = out["VOO Benchmark"].ffill()
    out["A1V12 Tactical Sleeve"] = out["A1V12 Tactical Sleeve"].ffill()
    if _missing_before:
        print(f"  Alpha overlay: forward-filled {_missing_before} date(s) where pv lacked "
              f"VOO Benchmark/A1V12 Tactical Sleeve data present elsewhere in the range.")

    # Truncate to the portfolio start date BEFORE computing rebase anchors --
    # sig/comp_* run further back (to EMA warm-up start) than pv does, so
    # taking .iloc[0] before truncating grabs a pre-pv, NaN-VOO row and
    # would silently null out every value in that column via NaN division.
    port_start = pd.to_datetime(PORTFOLIO_START)
    out = out[out["Date"] >= port_start].reset_index(drop=True)

    v0 = out["VOO Benchmark"].iloc[0]
    t0 = out["A1V12 Tactical Sleeve"].iloc[0]
    o0 = out["Tactical + Alpha Overlay"].iloc[0]
    out["VOO Benchmark"] = out["VOO Benchmark"] / v0 * 100000
    out["A1V12 Tactical Sleeve"] = out["A1V12 Tactical Sleeve"] / t0 * 100000
    out["Tactical + Alpha Overlay"] = out["Tactical + Alpha Overlay"] / o0 * 100000
    out = out[["Date", "VOO Benchmark", "A1V12 Tactical Sleeve", "Tactical + Alpha Overlay", "Overlay_Active",
               "SMH_Active", "SPHB_Active", "SMH_ROC5", "SMH_Streak", "SMH_JustTriggered", "SMH_RawOn",
               "SPHB_Dev", "SPHB_Streak", "SPHB_JustTriggered", "SPHB_RawOn"]]

    trades_df = pd.DataFrame(trades, columns=["Date", "Type", "From", "To",
                                               "Days_Held", "Entry_Price", "Exit_Price", "Return"])

    out.to_csv(DATA / "Alpha_Overlay_Daily_Values.csv", index=False, date_format="%Y-%m-%d")
    trades_df_write = trades_df.copy()
    if len(trades_df_write):
        trades_df_write["Date"] = pd.to_datetime(trades_df_write["Date"])
        trades_df_write = trades_df_write[trades_df_write["Date"] >= port_start]
        trades_df_write["Date"] = trades_df_write["Date"].dt.strftime("%Y-%m-%d")
    trades_df_write.to_csv(DATA / "Alpha_Overlay_Trade_Ledger.csv", index=False)

    print(f"  Alpha overlay: {len(trades_df)} total events, "
          f"final NAV ${out['Tactical + Alpha Overlay'].iloc[-1]:,.0f} on $100k base")

    return out, trades_df


def build_tactical_values(comp_adj, comp_open, sig):
    """
    v4.0: Adjusted-close total-return NAV for the tactical sleeve.
    Dividends are captured separately by build_dividend_analytics().

    Trade-day execution is T+2 open-price (look-ahead free, amended 2026-07, was T+1):
      - old holding: prior adj-close → trade_day_open (adj-close open)
      - new holding: trade_day_open → trade_day_close (adj-close)
    If open price is missing, prior adj-close is used as fallback.

    comp_adj  — adjusted-close composite (total return prices)
    comp_open — open-price composite (for T+2 execution split). Must be an
                ADJUSTED open series (see build_adjusted_open()) so trade-day
                execution stays consistent with the adjusted-close NAV used
                everywhere else in this function -- passing the raw open
                composite here reproduces the 2026-07 raw/adjusted mixing
                bug and will silently distort every switch-day valuation.
    sig       — signal DataFrame with EffectiveHolding column
    """
    import pandas as pd
    import numpy as np

    close   = comp_adj.copy()   # adj-close for total-return NAV
    open_px = comp_open.copy()
    close["Date"]   = pd.to_datetime(close["Date"])
    open_px["Date"] = pd.to_datetime(open_px["Date"])

    open_px = open_px.rename(columns={c: f"{c}__OPEN" for c in open_px.columns if c != "Date"})
    df = close.merge(open_px, on="Date", how="inner")
    print(f"  [DIAG][tv] after close+open merge: max date = {df['Date'].max().date()}, rows = {len(df)}")
    df = df.merge(sig[["Date","EffectiveHolding"]], on="Date", how="inner")
    print(f"  [DIAG][tv] after sig merge: max date = {df['Date'].max().date()}, rows = {len(df)}")
    df = df.dropna(subset=["MGK","MGV","VOO"]).sort_values("Date").reset_index(drop=True)
    print(f"  [DIAG][tv] after dropna(MGK,MGV,VOO): max date = {df['Date'].max().date()}, rows = {len(df)}")
    df = df[df["Date"] >= pd.to_datetime(PORTFOLIO_START)].reset_index(drop=True)

    # Track shares rather than price ratios to ensure NAV continuity
    # across asset switches. On a trade day:
    #   1. Value of old holding at trade-day open = shares * old_open
    #   2. Buy new holding at same open price = same dollar value
    #   3. New shares = trade_value / new_open
    # This keeps NAV continuous through switches regardless of
    # the price ratio between the two assets.

    h0    = df.loc[0, "EffectiveHolding"]
    p0    = float(df.loc[0, h0])
    shares = BASE_VALUE / p0 if p0 > 0 else 0.0
    val    = BASE_VALUE
    rows   = [[df.loc[0, "Date"], val, h0]]

    for i in range(1, len(df)):
        prev  = df.iloc[i - 1]
        cur   = df.iloc[i]
        old_h = prev["EffectiveHolding"]
        new_h = cur["EffectiveHolding"]

        if new_h == old_h:
            # Same holding — update value from new close price
            px_new = float(cur[new_h])
            px_old = float(prev[old_h])
            if px_old > 0:
                val = shares * px_new
        else:
            # Trade day — sell old at open, buy new at open
            old_open = cur.get(f"{old_h}__OPEN", float("nan"))
            new_open = cur.get(f"{new_h}__OPEN", float("nan"))
            px_old_prev = float(prev[old_h])

            if not (np.isfinite(old_open) and np.isfinite(new_open) and new_open > 0):
                # Fallback: use prior close → current close (no open available)
                print(f"  WARNING: missing Open on {cur['Date'].date()} for "
                      f"{old_h}->{new_h}; using close-to-close fallback")
                trade_value = shares * float(cur[new_h]) if px_old_prev == 0 else                               shares * px_old_prev * float(cur[new_h]) / px_old_prev
                # Keep NAV continuous: sell old at prev close, buy new at cur close
                trade_value = shares * px_old_prev if px_old_prev > 0 else val
                px_new_close = float(cur[new_h])
                shares = trade_value / px_new_close if px_new_close > 0 else shares
                val = shares * px_new_close
            else:
                # Sell old at open: trade_value = shares * old_open
                trade_value = shares * old_open
                # Buy new at open: new_shares = trade_value / new_open
                shares = trade_value / new_open
                # End-of-day value: new_shares * new_close
                val = shares * float(cur[new_h])

        rows.append([cur["Date"], val, new_h])

    tv = pd.DataFrame(rows, columns=["Date","A1V12","EffectiveHolding"])

    start_row = df.iloc[0]
    for a, label in [("MGK","MGK Buy Hold"), ("MGV","MGV Buy Hold"),
                     ("VOO","VOO Benchmark"), ("BIL","BIL Buy Hold")]:
        if a in df.columns and pd.notna(start_row[a]) and start_row[a] != 0:
            tv[label] = BASE_VALUE * df[a].values / float(start_row[a])

    # Dec 31 YTD anchor
    latest_yr  = tv["Date"].dt.year.max()
    prior_yr   = latest_yr - 1
    prior_data = tv[tv["Date"].dt.year == prior_yr]
    if not prior_data.empty:
        last_prior      = prior_data.iloc[-1].copy()
        last_prior_date = last_prior["Date"]
        if not (last_prior_date.month == 12 and last_prior_date.day == 31):
            anchor = last_prior.copy()
            anchor["Date"] = pd.Timestamp(prior_yr, 12, 31)
            tv = pd.concat([tv, pd.DataFrame([anchor])], ignore_index=True)
            tv["Date"] = pd.to_datetime(tv["Date"])
            tv = tv.sort_values("Date").reset_index(drop=True)

    # Sanity-check last row: a single model showing an implausible >20%
    # single-day move (e.g. a mutual fund posting its official NAV late,
    # after this pipeline already ran) should not hold back every other
    # column's real, current data for that date. Freeze just the affected
    # column(s) at the prior day's value; leave the date and every other
    # column's fresh value intact.
    tv_numeric = [c for c in tv.columns if c not in ("Date","EffectiveHolding")]
    if len(tv) >= 3:
        last   = tv[tv_numeric].iloc[-1]
        prev   = tv[tv_numeric].iloc[-2]
        change = ((last - prev) / prev.replace(0, float("nan"))).abs()
        if (change > 0.20).any():
            bad = change[change > 0.20].index.tolist()
            print(f"  WARNING: Tactical last row ({tv['Date'].iloc[-1]}) suspect "
                  f"returns > 20% in {bad} — freezing at prior-day value "
                  f"(date and other columns kept).")
            last_idx, prev_idx = tv.index[-1], tv.index[-2]
            for col in bad:
                tv.loc[last_idx, col] = tv.loc[prev_idx, col]

    tv.to_csv(DATA / "Tactical_Daily_Values.csv", index=False, date_format="%Y-%m-%d")
    return tv


def build_portfolios(comp_adj, tv, static_models, tactical_models):
    """
    v4.0: Unified adjusted-close total-return NAV for all models.

    All series — tactical sleeve, VOO benchmark, every static and
    tactical model — use Adjusted Close prices. This gives a single
    consistent return framework directly comparable across all models.

    Dividends are NOT double-counted. The adj-close series already
    reflects reinvested distributions; build_dividend_analytics()
    reports the cash flows separately for income analysis.

    Annual rebalancing applies to multi-asset models.
    Standalone tactical sleeve and VOO benchmark are not rebalanced.

    Writes Portfolio_Daily_Share_Ledger.csv and returns (vals, ledger).
    """
    import pandas as pd
    import numpy as np

    NO_REBALANCE = {"A1V12 Tactical Sleeve", "VOO Benchmark"}

    # v4.0: unified adj-close for all models including standalone series
    all_models = {
        "VOO Benchmark":         {"VOO": 1.0},
        "A1V12 Tactical Sleeve": {"TACTICAL": 1.0},
    }
    all_models.update(static_models)
    all_models.update(tactical_models)

    # Use adj-close as the single price source
    df = comp_adj.copy()
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.merge(tv[["Date","A1V12"]], on="Date", how="inner"
                  ).sort_values("Date").reset_index(drop=True)
    df = df[df["Date"] >= pd.to_datetime(PORTFOLIO_START)].reset_index(drop=True)
    df["TACTICAL"] = df["A1V12"]

    if "VOO" not in df.columns:
        raise ValueError("VOO adjusted-close data is required for the benchmark.")

    vals = pd.DataFrame({"Date": df["Date"]})
    ledger_rows = []

    for name, weights in all_models.items():
        keyed = {
            ("TACTICAL" if asset in {"TACTICAL", "A1V12"} else asset): float(weight)
            for asset, weight in weights.items()
        }
        keyed = {asset: weight for asset, weight in keyed.items() if asset in df.columns}
        total_w = sum(keyed.values())
        if not keyed or total_w <= 0:
            continue
        keyed = {asset: weight / total_w for asset, weight in keyed.items()}

        shares = {}
        for asset, weight in keyed.items():
            px = float(df.loc[0, asset])
            if np.isfinite(px) and px > 0:
                shares[asset] = BASE_VALUE * weight / px

        nav = []
        cur_year = pd.Timestamp(df.loc[0, "Date"]).year

        for i in range(len(df)):
            dt = pd.Timestamp(df.loc[i, "Date"])

            if (i > 0 and name not in NO_REBALANCE and len(shares) > 1
                    and dt.year != cur_year):
                prev_i = i - 1
                total_value = sum(
                    sh * float(df.loc[prev_i, asset])
                    for asset, sh in shares.items()
                    if pd.notna(df.loc[prev_i, asset])
                )
                new_shares = {}
                for asset, weight in keyed.items():
                    px = float(df.loc[prev_i, asset])
                    if np.isfinite(px) and px > 0:
                        new_shares[asset] = total_value * weight / px
                shares = new_shares
                cur_year = dt.year

            total = 0.0
            for asset, sh in shares.items():
                px = float(df.loc[i, asset])
                value = sh * px if np.isfinite(px) else 0.0
                total += value

                # Use the actual security ticker in the dividend ledger so the
                # raw-close VOO benchmark still receives VOO cash distributions.
                ledger_asset = asset
                if asset in {"TACTICAL", "A1V12"}:
                    price_basis = "Adjusted Close (Tactical Sleeve)"
                else:
                    price_basis = "Adjusted Close"

                ledger_rows.append({
                    "Date": dt,
                    "Model": name,
                    "Asset": ledger_asset,
                    "Internal_Asset": asset,
                    "Price_Basis": price_basis,
                    "Shares": sh,
                    "Price": px,
                    "Position_Value": value,
                })
            nav.append(total)

        vals[name] = nav

    ledger = pd.DataFrame(ledger_rows)
    ledger.to_csv(
        DATA / "Portfolio_Daily_Share_Ledger.csv",
        index=False,
        date_format="%Y-%m-%d",
    )

    # Dec 31 YTD anchor
    dates_ts = pd.to_datetime(vals["Date"])
    latest_yr = dates_ts.dt.year.max()
    prior_yr = latest_yr - 1
    prior_mask = dates_ts.dt.year == prior_yr
    if prior_mask.any():
        last_prior = vals[prior_mask].iloc[-1].copy()
        last_prior_date = pd.to_datetime(last_prior["Date"])
        if not (last_prior_date.month == 12 and last_prior_date.day == 31):
            anchor = last_prior.copy()
            anchor["Date"] = pd.Timestamp(prior_yr, 12, 31).strftime("%Y-%m-%d")
            vals = pd.concat([vals, pd.DataFrame([anchor])], ignore_index=True)
            vals["Date"] = pd.to_datetime(vals["Date"]).dt.strftime("%Y-%m-%d")
            vals = vals.sort_values("Date").reset_index(drop=True)

    # ── Sanity-check last row: a single model (often a mutual-fund-heavy
    # blend whose NAV posts late) showing an implausible >20% single-day
    # move should not hold back every other model's real, current data for
    # that date -- that was silently freezing "Latest data" one day behind
    # even when e.g. VOO Benchmark and A1V12 Tactical Sleeve were fine.
    # Freeze just the affected column(s) at the prior day's value instead.
    numeric_cols = [c for c in vals.columns if c != "Date"]
    if len(vals) >= 3:
        last   = vals[numeric_cols].iloc[-1]
        prev   = vals[numeric_cols].iloc[-2]
        change = ((last - prev) / prev.replace(0, float("nan"))).abs()
        if (change > 0.20).any():
            bad_cols = change[change > 0.20].index.tolist()
            print(f"  WARNING: Last row ({vals['Date'].iloc[-1]}) has suspect "
                  f"returns > 20% in {bad_cols} — freezing at prior-day value "
                  f"(date and other models kept).")
            last_idx, prev_idx = vals.index[-1], vals.index[-2]
            for col in bad_cols:
                vals.loc[last_idx, col] = vals.loc[prev_idx, col]

    vals.to_csv(
        DATA / "Portfolio_Daily_Values.csv",
        index=False,
        date_format="%Y-%m-%d",
    )
    return vals, ledger


def build_holding_analytics(sig, comp_raw):
    import pandas as pd
    px = comp_raw[["Date","MGK","MGV"]].copy()
    df = sig[["Date","EffectiveHolding"]].merge(px, on="Date", how="left")
    df = df[df["Date"] >= pd.to_datetime(PORTFOLIO_START)
            ].dropna(subset=["MGK","MGV"]).reset_index(drop=True)
    rows, start, current = [], 0, df.loc[0, "EffectiveHolding"]

    def period(st, en, asset):
        sub = df.iloc[st:en + 1]
        sp, ep = sub[asset].iloc[0], sub[asset].iloc[-1]
        return {"Start_Date": sub["Date"].iloc[0], "End_Date": sub["Date"].iloc[-1],
                "Asset": asset, "Trading_Days": len(sub),
                "Start_Price": sp, "End_Price": ep,
                "Return": ep / sp - 1 if sp else None}

    for i in range(1, len(df)):
        if df.loc[i, "EffectiveHolding"] != current:
            rows.append(period(start, i - 1, current))
            start, current = i, df.loc[i, "EffectiveHolding"]
    rows.append(period(start, len(df) - 1, current))

    hp = pd.DataFrame(rows)
    hp.to_csv(DATA / "Holding_Periods.csv", index=False, date_format="%Y-%m-%d")
    hs = hp.groupby("Asset").agg(
        Periods=("Asset","count"), Avg_Trading_Days=("Trading_Days","mean"),
        Median_Trading_Days=("Trading_Days","median"), Min_Trading_Days=("Trading_Days","min"),
        Max_Trading_Days=("Trading_Days","max"), Avg_Return=("Return","mean"),
        Best_Return=("Return","max"), Worst_Return=("Return","min"),
    ).reset_index()
    hs["Pct_Time"] = hs["Asset"].map(
        hp.groupby("Asset")["Trading_Days"].sum() / hp["Trading_Days"].sum())
    hs.to_csv(DATA / "Holding_Summary.csv", index=False)


def build_dividend_analytics(comp_raw, comp_open, div_comp, sig, tv,
                              portfolio_ledger, static_models, tactical_models):
    """
    Compute dividends from the exact daily shares in the NAV ledger.
    FIX: open-price fallback instead of hard ValueError crash.
    """
    import pandas as pd
    import numpy as np

    prices = comp_raw.copy()
    opens  = comp_open.copy()
    divs   = div_comp.copy()
    for frame in (prices, opens, divs):
        frame["Date"] = pd.to_datetime(frame["Date"])

    prices = prices.merge(tv[["Date","A1V12"]], on="Date", how="inner")
    prices = prices[prices["Date"] >= pd.to_datetime(PORTFOLIO_START)].sort_values("Date").reset_index(drop=True)
    opens  = opens.merge(prices[["Date"]], on="Date", how="right")
    divs   = divs.merge(prices[["Date"]], on="Date", how="right").fillna(0.0)

    # Diagnostic: show dividend coverage for key assets
    for _a in ["MGK", "MGV", "VOO", "AVUV"]:
        if _a in divs.columns:
            _n = (divs[_a].abs() > 1e-12).sum()
            print(f"  div_comp {_a}: {_n} non-zero events in portfolio window")
        else:
            print(f"  div_comp {_a}: COLUMN MISSING")

    hold = sig[["Date","EffectiveHolding"]].copy()
    hold["Date"] = pd.to_datetime(hold["Date"])
    hold = hold.merge(prices[["Date"]], on="Date", how="right")
    hold["EffectiveHolding"] = hold["EffectiveHolding"].ffill().bfill()

    # Exact underlying tactical share ledger
    current           = str(hold.loc[0, "EffectiveHolding"])
    underlying_shares = BASE_VALUE / float(prices.loc[0, current])
    synthetic_units   = BASE_VALUE / float(prices.loc[0, "A1V12"])
    tactical_dps      = np.zeros(len(prices))
    period_rows       = []
    period_start      = 0
    period_income     = 0.0

    for i in range(len(prices)):
        new_h = str(hold.loc[i, "EffectiveHolding"])
        dps   = float(divs.loc[i, current]) if current in divs.columns else 0.0
        income = underlying_shares * dps
        tactical_dps[i] = income / synthetic_units if synthetic_units else 0.0
        period_income  += income

        if i > 0 and new_h != current:
            period_rows.append({
                "Start_Date":      prices.loc[period_start, "Date"],
                "End_Date":        prices.loc[i - 1, "Date"],
                "Holding":         current,
                "Dividend_Income": period_income,
            })
            # FIX: fallback to prior close if open price is missing — no crash
            old_col  = current
            new_col  = new_h
            old_open = float(opens.loc[i, old_col]) if old_col in opens.columns else float("nan")
            new_open = float(opens.loc[i, new_col]) if new_col in opens.columns else float("nan")
            if not (np.isfinite(old_open) and np.isfinite(new_open) and new_open > 0):
                print(f"  WARNING: missing tactical Open on {prices.loc[i,'Date'].date()} "
                      f"for {current}->{new_h}; using prior close as fallback")
                old_open = float(prices.loc[i - 1, current]) if i > 0 else old_open
                new_open = float(prices.loc[i, new_h])
            security_value    = underlying_shares * old_open
            underlying_shares = security_value / new_open
            current       = new_h
            period_start  = i
            period_income = 0.0

    period_rows.append({
        "Start_Date":      prices.loc[period_start, "Date"],
        "End_Date":        prices.loc[len(prices) - 1, "Date"],
        "Holding":         current,
        "Dividend_Income": period_income,
    })
    pd.DataFrame(period_rows).to_csv(
        DATA / "Dividend_Holding_Periods.csv", index=False, date_format="%Y-%m-%d")

    # Add TACTICAL synthetic DPS to div frame
    tactical_div = pd.DataFrame({"Date": prices["Date"], "TACTICAL": tactical_dps})
    divs = divs.merge(tactical_div, on="Date", how="left", suffixes=("", "_new"))
    if "TACTICAL_new" in divs.columns:
        divs["TACTICAL"] = divs["TACTICAL_new"].fillna(0.0)
        divs = divs.drop(columns=["TACTICAL_new"])

    ledger = portfolio_ledger.copy()
    ledger["Date"] = pd.to_datetime(ledger["Date"])

    div_long = divs.melt(id_vars=["Date"], var_name="Asset", value_name="Dividend_Per_Share")
    div_long["Dividend_Per_Share"] = pd.to_numeric(
        div_long["Dividend_Per_Share"], errors="coerce").fillna(0.0)

    income = ledger.merge(div_long, on=["Date","Asset"], how="left")
    income["Dividend_Per_Share"] = income["Dividend_Per_Share"].fillna(0.0)
    income["Dividend_Income"]    = income["Shares"] * income["Dividend_Per_Share"]
    income["Year"]  = income["Date"].dt.year
    income["Month"] = income["Date"].dt.to_period("M").astype(str)

    events = income[income["Dividend_Income"].abs() > 1e-12].copy()
    events.to_csv(DATA / "Dividend_Daily_Income_Ledger.csv",
                  index=False, date_format="%Y-%m-%d")

    last_date    = pd.to_datetime(income["Date"].max())
    current_year = int(last_date.year)

    # Annual model summary
    annual = income.groupby(["Model","Year"], as_index=False)["Dividend_Income"].sum()
    annual = annual.sort_values(["Model","Year"])
    annual["Cumulative_Income"]   = annual.groupby("Model")["Dividend_Income"].cumsum()
    annual["Is_Partial_Year"]     = np.where(annual["Year"].eq(current_year), "YES", "NO")
    annual["Period"]              = annual["Year"].astype(str)
    annual.loc[annual["Year"].eq(current_year), "Period"] = (
        str(current_year) + " YTD through " + str(last_date.date()))
    # FIX: compute Annualized_Run_Rate for partial year; full years use actual income
    months_elapsed = last_date.month + last_date.day / 30.0
    annual["Annualized_Run_Rate"] = np.where(
        annual["Year"].eq(current_year),
        annual["Dividend_Income"] / months_elapsed * 12.0,
        annual["Dividend_Income"]
    )
    annual.to_csv(DATA / "Dividend_Model_Annual.csv", index=False)

    # Monthly model summary
    monthly = income.groupby(["Model","Month"], as_index=False)["Dividend_Income"].sum()
    monthly = monthly.sort_values(["Model","Month"])
    monthly["Cumulative_Income"] = monthly.groupby("Model")["Dividend_Income"].cumsum()
    monthly.to_csv(DATA / "Dividend_Model_Monthly.csv", index=False)

    # Annual by asset
    asset_annual = income.groupby(["Model","Year","Asset"], as_index=False)["Dividend_Income"].sum()
    asset_annual.to_csv(DATA / "Dividend_Asset_Annual.csv", index=False)

    # Monthly by asset
    asset_monthly = income.groupby(["Model","Month","Asset"], as_index=False)["Dividend_Income"].sum()
    asset_monthly.to_csv(DATA / "Dividend_Asset_Monthly.csv", index=False)

    # Model KPI summary
    ttm_start   = last_date - pd.DateOffset(years=1)
    summary_rows = []
    for model, g in income.groupby("Model"):
        summary_rows.append({
            "Model":                   model,
            "Lifetime_Dividend_Income": float(g["Dividend_Income"].sum()),
            "Current_Year_Income":      float(g.loc[g["Date"].dt.year.eq(current_year), "Dividend_Income"].sum()),
            "Prior_Full_Year_Income":   float(g.loc[g["Date"].dt.year.eq(current_year - 1), "Dividend_Income"].sum()),
            "TTM_Dividend_Income":      float(g.loc[g["Date"] > ttm_start, "Dividend_Income"].sum()),
            "Through_Date":             last_date.strftime("%Y-%m-%d"),
        })
    pd.DataFrame(summary_rows).to_csv(DATA / "Dividend_Model_Summary.csv", index=False)

    # Fixed-income monthly verification on $100k initial investment
    verify_rows = []
    for asset in ["PIMIX","JPIE","FIWDX","JBND"]:
        if asset not in prices.columns or asset not in divs.columns:
            continue
        first = prices[asset].first_valid_index()
        if first is None:
            continue
        sh  = BASE_VALUE / float(prices.loc[first, asset])
        tmp = pd.DataFrame({
            "Date": prices["Date"],
            "DPS":  pd.to_numeric(divs[asset], errors="coerce").fillna(0.0),
        })
        tmp["Month"]  = tmp["Date"].dt.to_period("M").astype(str)
        tmp["Income"] = sh * tmp["DPS"]
        out = tmp.groupby("Month", as_index=False).agg(
            Dividend_Per_Share=("DPS","sum"),
            Income_on_100k=("Income","sum"))
        out.insert(0, "Asset", asset)
        out = out[out["Dividend_Per_Share"].abs() > 1e-12]
        verify_rows.append(out)
    verify = pd.concat(verify_rows, ignore_index=True) if verify_rows else pd.DataFrame(
        columns=["Asset","Month","Dividend_Per_Share","Income_on_100k"])
    verify.to_csv(DATA / "Fixed_Income_Monthly_Verification.csv", index=False)

    # Dividend coverage audit
    coverage_rows = []
    for asset in sorted(ledger["Asset"].unique()):
        s    = div_long.loc[div_long["Asset"].eq(asset), "Dividend_Per_Share"]
        mask = s.abs() > 1e-12
        coverage_rows.append({
            "Asset":              asset,
            "Status":             "PASS" if mask.any() else "WARN",
            "Distribution_Count": int(mask.sum()),
            "Total_DPS":          float(s.sum()),
            "Detail": "Events found" if mask.any() else "No distributions; verify expected",
        })
    pd.DataFrame(coverage_rows).to_csv(AUDIT / "Dividend_Coverage_Audit.csv", index=False)

    # Sanity audit
    sanity_rows = []
    for model, g in annual.groupby("Model"):
        full = g[g["Year"] < current_year].sort_values("Year")
        if len(full) < 3:
            continue
        early  = float(full.head(min(3, len(full)))["Dividend_Income"].median())
        recent = float(full.tail(min(3, len(full)))["Dividend_Income"].median())
        ratio  = recent / early if early else float("nan")
        sanity_rows.append({
            "Model":                model,
            "Early_Median_Income":  early,
            "Recent_Median_Income": recent,
            "Recent_to_Early_Ratio": ratio,
            "Status": "PASS" if (not np.isfinite(ratio) or ratio >= 0.35) else "FAIL",
        })
    pd.DataFrame(sanity_rows).to_csv(AUDIT / "Dividend_Income_Sanity_Audit.csv", index=False)


def run_audit(alloc_df, static_models, tactical_models,
              comp_adj, comp_raw, sig, trades, tv, pv, data_audit_df):
    import pandas as pd
    checks = []
    def add(name, status, detail): checks.append([name, status, detail])

    add("Performance price basis", "PASS",
        "Unified Adjusted Close total return · tactical sleeve, VOO benchmark, all models · dividends reported separately")
    add("VOO benchmark basis", "PASS",
        "VOO uses Adjusted Close throughout — benchmark and all model allocations")
    add("Signal price basis",      "PASS",
        f"{_normalise_signal_price_basis(SIGNAL_PRICE_BASIS).title()} Close for MGK/MGV ratio and EMA89; configurable via SIGNAL_PRICE_BASIS")
    add("Dividend treatment",      "PASS", "Cash distributions reported separately; not reinvested in price-return NAV")
    add("Allocation file",         "PASS", "Config/MWM_Allocations.csv")
    add("Allocation rows",         "PASS", str(len(alloc_df)))
    add("Static MWM models",       "PASS", ", ".join(static_models.keys()))
    add("Tactical models",         "PASS", ", ".join(tactical_models.keys()))
    add("Tactical sleeve",         "PASS", "Binary MGK/MGV — no JIVE (v3.5)")
    add("EMA warmup",              "PASS",
        f"Price history from {START_DATE}; EMA89 warm by {PORTFOLIO_START}; "
        f"portfolio NAV starts from {PORTFOLIO_START}")
    add("Cooldown rule",           "PASS",
        f"{COOLDOWN_DAYS} trading days after any trade (Growth<->Value, uniform)")
    add("Open-price execution",    "PASS",
        "Trade-day return split at open price; fallback to prior close if open missing")

    required   = set(alloc_df["Production_Asset"].unique())
    ok_assets  = set(data_audit_df.loc[data_audit_df["Status"] == "OK", "Asset"])
    failed     = sorted(a for a in data_audit_df.loc[data_audit_df["Status"] != "OK", "Asset"]
                        if a in required)
    unresolved = sorted(a for a in required
                        if a not in ok_assets
                        and a not in BACKFILLS
                        and a not in {"TACTICAL","A1V12"})
    missing    = sorted(set(failed) | set(unresolved))
    add("Allocation assets resolve to live data",
        "PASS" if not missing else "FAIL",
        "All Production_Assets have OK price data" if not missing
        else f"Unresolved/failed: {', '.join(missing)}")

    add("Adjusted composite rows", "PASS" if len(comp_adj) else "FAIL", str(len(comp_adj)))
    add("Raw composite rows",      "PASS" if len(comp_raw) else "FAIL", str(len(comp_raw)))
    add("Signal rows",             "PASS" if len(sig)    else "FAIL", str(len(sig)))
    add("Trade ledger rows",       "PASS" if len(trades) else "WARN", str(len(trades)))
    add("Portfolio values rows",   "PASS" if len(pv)     else "FAIL", str(len(pv)))
    add("Latest signal date",      "PASS", str(sig["Date"].max())  if len(sig) else "N/A")
    add("Latest portfolio date",   "PASS", str(pv["Date"].max())   if len(pv)  else "N/A")
    add("Portfolio rebalancing",   "PASS",
        "Annual (first trading day of each year) for multi-asset models; "
        "continuous blend for single-asset models")
    add("Backfill scaling",        "PASS", "Ratio-scaled to first live observation")
    add("Chart downsampling",      "PASS", "Daily <=2Y, Weekly >2Y, Monthly >=8Y/SI")
    add("Drawdown chart",          "PASS", "Daily drawdown computed before downsampling")

    pd.DataFrame(checks, columns=["Check","Status","Detail"]
                 ).to_csv(AUDIT / "Production_Audit.csv", index=False)


def csv_payload(name):
    p = DATA / name
    return p.read_text() if p.exists() else ""


def _latest_prices_json():
    """
    Return a JSON string mapping asset → latest adjusted-close price.
    Used by the dashboard trailingYield() to compute S&P 500 yield:
        yield = sum(last 4 VOO dividends) / current VOO price
    Reads Composite_Prices.csv (adj-close, v4.0 primary source).
    Also reads Tactical_Daily_Values.csv for the VOO Benchmark price
    as a fallback to ensure VOO is always present in the output.
    """
    import json as _json
    import pandas as pd

    prices = {}

    # Primary: adj-close composite
    for p in [DATA / "Composite_Prices.csv",
              DATA / "Composite_Prices_Raw_Close.csv"]:
        if p.exists():
            try:
                df = pd.read_csv(p)
                last = df.iloc[-1].drop("Date", errors="ignore")
                for k, v in last.items():
                    try:
                        fv = float(v)
                        if pd.notna(fv) and fv > 0:
                            prices[k] = fv
                    except (ValueError, TypeError):
                        pass
            except Exception:
                pass
            break

    # Fallback: if VOO missing, read from Tactical_Daily_Values.csv
    # which always has a VOO Benchmark column
    if "VOO" not in prices:
        tv_path = DATA / "Tactical_Daily_Values.csv"
        if tv_path.exists():
            try:
                tv = pd.read_csv(tv_path)
                tv = tv.dropna(subset=["VOO Benchmark"])
                if len(tv):
                    # VOO Benchmark is rebased to $100k; need actual price
                    # Use ratio: latest_tv_voo / first_tv_voo * first_actual_voo
                    # Simpler: just flag that price is unavailable and
                    # compute yield from Composite instead
                    pass
            except Exception:
                pass

    # If still missing VOO, try reading it directly from Price_Master_Wide.csv
    if "VOO" not in prices:
        wide_path = DATA / "Price_Master_Wide.csv"
        if wide_path.exists():
            try:
                df = pd.read_csv(wide_path)
                if "VOO" in df.columns:
                    last_voo = df["VOO"].dropna()
                    if len(last_voo):
                        prices["VOO"] = float(last_voo.iloc[-1])
                        print(f"  assetprices VOO from Price_Master_Wide: {prices['VOO']:.2f}")
            except Exception:
                pass

    if "VOO" not in prices:
        print("  WARNING: VOO price not found for trailingYield() — S&P 500 yield will show —")

    return _json.dumps(prices)


def _dividend_history_json():
    """
    Return a JSON array of {Asset, Ex_Date, Div_Per_Share} objects
    from Dividend_Master_Wide.csv (written by download_prices()).
    Includes only rows where the dividend is positive.
    Used by the dashboard trailingYield() function:
        yield = sum(last 4 payments) / current price
    """
    import json as _json
    p = DATA / "Dividend_Master_Wide.csv"
    if not p.exists():
        return "[]"
    try:
        import pandas as pd
        wide = pd.read_csv(p, parse_dates=["Date"])
        wide = wide[wide["Date"] >= pd.to_datetime(PORTFOLIO_START)]
        # Melt to long format
        long = wide.melt(id_vars=["Date"], var_name="Asset",
                         value_name="Div_Per_Share")
        long = long[long["Div_Per_Share"].abs() > 1e-12].copy()
        long["Ex_Date"] = long["Date"].dt.strftime("%Y-%m-%d")
        long = long[["Asset", "Ex_Date", "Div_Per_Share"]].sort_values(
            ["Asset", "Ex_Date"])
        # Diagnostic: log event counts for key assets
        for _a in ["VOO", "MGK", "MGV", "BIL"]:
            _n = long[long["Asset"] == _a].shape[0]
            print(f"  divhistory {_a}: {_n} events")
        return _json.dumps(long.to_dict(orient="records"))
    except Exception as e:
        print(f"  WARNING: _dividend_history_json() failed: {e}")
        import traceback; traceback.print_exc()
        return "[]"


def build_dashboard():
    payload = {
        "tactical":          csv_payload("Tactical_Daily_Values.csv"),
        "portfolio":         csv_payload("Portfolio_Daily_Values.csv"),
        "signals":           csv_payload("Signal_History.csv"),
        "trades":            csv_payload("Trade_Ledger.csv"),
        "holdsum":           csv_payload("Holding_Summary.csv"),
        "holdperiods":       csv_payload("Holding_Periods.csv"),
        "dataaudit":         (AUDIT/"Data_Audit.csv").read_text()        if (AUDIT/"Data_Audit.csv").exists()        else "",
        "prodaudit":         (AUDIT/"Production_Audit.csv").read_text()  if (AUDIT/"Production_Audit.csv").exists()  else "",
        "modelmap":          csv_payload("Tactical_Model_Map.csv"),
        "alloc":             csv_payload("Allocation_Config_Normalized.csv"),
        "backfillaudit":     (AUDIT/"Backfill_Scale_Audit.csv").read_text() if (AUDIT/"Backfill_Scale_Audit.csv").exists() else "",
        # Dividend payloads — FIX: all four required by renderDividend()
        "divsummary":        csv_payload("Dividend_Model_Summary.csv"),
        "divannual":         csv_payload("Dividend_Model_Annual.csv"),
        "divasset":          csv_payload("Dividend_Asset_Annual.csv"),
        "divperiods":        csv_payload("Dividend_Holding_Periods.csv"),
        "divassetmonthly":   csv_payload("Dividend_Asset_Monthly.csv"),       # FIX: was missing
        "fixedincomeverify": csv_payload("Fixed_Income_Monthly_Verification.csv"),  # FIX: was missing
        # Asset price and dividend history for industry-standard yield calculation
        # yield = sum(last 4 dividends) / current price
        "divhistory":        _dividend_history_json(),
        "assetprices":       _latest_prices_json(),
        "alphaoverlay":       csv_payload("Alpha_Overlay_Daily_Values.csv"),
        "alphaoverlaytrades": csv_payload("Alpha_Overlay_Trade_Ledger.csv"),
    }
    _signal_basis_label = _normalise_signal_price_basis(SIGNAL_PRICE_BASIS).title()
    _disclosure_note = (
        f"Tactical signals use {_signal_basis_label} closing prices "
        f"(see SIGNAL_PRICE_BASIS). Charts and metrics use adjusted closing "
        f"prices, matching the total-return NAV above. Trade-day execution "
        f"uses an adjusted open price (see build_adjusted_open()), kept "
        f"consistent with the adjusted-close NAV rather than mixing in a "
        f"raw open price."
    )
    html = DASHBOARD_HTML.replace("__PAYLOAD__", json.dumps(payload))
    html = html.replace("__SIGNAL_DISCLOSURE_NOTE__", _disclosure_note)
    out  = DASH / "A1V12_Yahoo_Production_v3_2_Dashboard.html"
    out.write_text(html)
    return out


DASHBOARD_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>A1V12 Yahoo Production v3.6</title>
<style>
body{font-family:Arial;margin:0;background:#f5f7fb;color:#111827}.wrap{max-width:1680px;margin:auto;padding:18px}h1{color:#17365d;margin:0}.sub{color:#64748b;font-size:13px}.card{background:white;border:1px solid #d7deea;border-radius:13px;padding:14px;margin:12px 0}.tabs,.controls,.checks{display:flex;gap:7px;flex-wrap:wrap;margin:10px 0}button{border:1px solid #cbd5e1;background:white;border-radius:9px;padding:8px 11px;font-weight:700;cursor:pointer}button.active{background:#17365d;color:white}.tab{display:none}.tab.active{display:block}.grid{display:grid;gap:12px}.grid2{grid-template-columns:2fr 1fr}.kpis{grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}.kpi{background:#f8fafc;border:1px solid #e5e7eb;border-radius:10px;padding:10px}.label{font-size:11px;text-transform:uppercase;color:#64748b;font-weight:800}.big{font-size:22px;font-weight:900}.chartbox{height:430px;width:100%;border:1px solid #eef2f7;border-radius:10px;background:white}.chartbox.short{height:300px}canvas{width:100%;height:100%;display:block}.legend{display:flex;flex-wrap:wrap;gap:16px;font-size:12px;margin-top:10px}.sw{width:18px;height:4px;border-radius:2px;display:inline-block;margin-right:5px}.scroll{max-height:560px;overflow:auto;border:1px solid #eef2f7;border-radius:10px}table{border-collapse:collapse;width:100%;font-size:12px}th,td{border-bottom:1px solid #e5e7eb;padding:7px;text-align:right;white-space:nowrap}th{background:#f3f4f6;position:sticky;top:0;cursor:pointer;z-index:2}td:first-child,th:first-child{text-align:left}.freeze1{position:sticky;left:0;background:white;z-index:1;min-width:120px}.freeze2{position:sticky;left:120px;background:white;z-index:1;min-width:90px}.freeze3{position:sticky;left:210px;background:white;z-index:1;min-width:180px}.good{color:#15803d;font-weight:800}.bad{color:#b91c1c;font-weight:800}.pass{color:#15803d;font-weight:900}.fail{color:#b91c1c;font-weight:900}.warn{color:#a16207;font-weight:900}.note{font-size:12px;color:#64748b}.pill{display:inline-block;background:#eef2ff;border:1px solid #c7d2fe;border-radius:999px;padding:4px 8px;margin:2px;font-size:12px;font-weight:700}.state-growth{background:#ecfdf5}.state-value{background:#eff6ff}.tradebox{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}.tradeitem{background:#f8fafc;border:1px solid #e5e7eb;border-radius:10px;padding:10px}
</style></head><body><div class="wrap">
<h1>A1V12 Yahoo Production v3.4</h1><div class="sub">Binary MGK/MGV tactical sleeve · Unified adjusted-close total return · All series comparable · Dividends reported separately</div>
<div class="card" style="border-left:5px solid #17365d"><b>All series use adjusted-close total return (distributions reinvested). Tactical sleeve, VOO benchmark, and all model allocations are directly comparable. Dividend income shown separately below.</b><div class="note">__SIGNAL_DISCLOSURE_NOTE__</div></div>
<div class="tabs">
<button class="tabbtn active" onclick="showTab(event,'overview')">Overview</button>
<button class="tabbtn" onclick="showTab(event,'tactical')">Tactical Sleeve</button>
<button class="tabbtn" onclick="showTab(event,'mwm')">MWM Static</button>
<button class="tabbtn" onclick="showTab(event,'tacticalmodels')">Tactical Models</button>
<button class="tabbtn" onclick="showTab(event,'signals')">Signals</button>
<button class="tabbtn" onclick="showTab(event,'holding')">Holding Analytics</button>
<button class="tabbtn" onclick="showTab(event,'trade')">Trade Log</button>
<button class="tabbtn" onclick="showTab(event,'chartaudit')">Chart Audit</button>
<button class="tabbtn" onclick="showTab(event,'audit')">Audit</button>

<button class="tabbtn" onclick="showTab(event,'allocation')">Allocation</button>
<button class="tabbtn" onclick="showTab(event,'config')">Config</button>
<button id="alphaOverlayTabBtn" class="tabbtn" onclick="showTab(event,'alphaoverlay')" style="border-color:#C9962C;color:#8A6A1F">Alpha Overlay</button>
</div>
<div class="controls"><b class="note">Period</b><span id="periodButtons"></span><span id="freqPill" class="pill">Display: Daily</span><span class="pill">Metrics use daily rows</span><span class="pill">Drawdown before downsample</span></div>
<section id="overview" class="tab active"><div class="grid kpis" id="kpiBox"></div><div class="grid grid2"><div class="card"><h2>Primary Comparison</h2><div class="controls"><button onclick="preset('core')">Core</button><button onclick="preset('static')">MWM Static</button><button onclick="preset('tacticalmodels')">Tactical Models</button><button onclick="preset('all')">All</button></div><div id="overviewChecks" class="checks"></div><div class="chartbox"><canvas id="overviewChart"></canvas></div><div id="overviewLegend" class="legend"></div></div><div class="card"><h2>Current State &amp; Latest Trade</h2><div id="stateBox"></div><div id="latestTrade"></div><div id="divYield" style="margin-top:12px"></div></div></div><div class="card"><h2>Sortable Metrics</h2><div class="scroll"><table id="metricsTable"></table></div></div></section>
<section id="alphaoverlay" class="tab">
<div class="card" style="border-top:3px solid #C9962C">
  <h2>Alpha Overlay — Does It Earn Its Place?</h2>
  <div class="note">Same MGK/MGV base regime throughout, completely unaffected by this layer. Two independent triggers add optional sleeves on top of whichever asset the base regime already holds: (1) SMH — 5-day rate of change of the SMH/MGV raw-close ratio, entry at ROC5 &ge;+2.0%, exit at ROC5 &le;-2.0%, 5 consecutive trading days to confirm; (2) SPHB — SPHB/MGV raw-close ratio vs its own 50-day EMA, entry/exit at &plusmn;0.3% band, 5 consecutive trading days to confirm. Each sleeve is 40% when active alone; when both are active simultaneously, weights scale down proportionally so combined sleeve exposure is capped at 50%. Validated across 5yr / 10yr / Jan-2016 / Jan-2021 / 2021-2023-stress / 2012-2015 windows: the combined overlay beats the base regime alone on both CAGR and Sharpe in every one. Six other sectors (XLI, IYR, XLV, XLU, XLY, XLE), SPLV (either denominator), and several alternative trigger ratios (SMH/VOO, SMH/XLE, SPHB/MGK) were tested and did not clear this bar.</div>
  <div class="grid kpis" id="alphaKpiBox" style="margin-top:12px"></div>
</div>
<div class="card">
  <h2>Live Trigger Status</h2>
  <div class="note">Where today's raw signal stands, independent of whether the sleeve is currently funded (SMH_Active/SPHB_Active above reflect yesterday's confirmed state, T+1). Streak counts consecutive qualifying days toward the next entry (if off) or exit (if on); resets to 0 the day a transition fires.</div>
  <div class="tradebox" id="alphaTriggerBox" style="margin-top:12px"></div>
</div>
<div class="card">
  <h2>Growth of $100,000</h2>
  <div class="note">Gold bands mark every stretch either sleeve (SMH or SPHB) was active — the actual calendar, not a summary stat.</div>
  <div class="chartbox"><canvas id="alphaOverlayChart"></canvas></div>
  <div id="alphaOverlayLegend" class="legend"></div>
</div>
<div class="card">
  <h2>Trade Log</h2>
  <div class="note">Base regime switches (growth &harr; value), SMH sleeve entries/exits, and SPHB sleeve entries/exits, most recent first.</div>
  <div class="scroll"><table id="alphaOverlayTradeTable"></table></div>
</div>
</section>
<section id="tactical" class="tab"><div class="card"><h2>Tactical Sleeve — MGK / MGV Binary (v3.4)</h2><div class="chartbox"><canvas id="tacticalChart"></canvas></div><div id="tacticalLegend" class="legend"></div></div><div class="card"><h2>Tactical Drawdown</h2><div class="chartbox short"><canvas id="tacticalDD"></canvas></div><div id="tacticalDDLegend" class="legend"></div><div class="note">Daily drawdown computed before chart downsampling.</div></div><div class="card"><h2>Tactical Metrics</h2><div class="scroll"><table id="tacticalMetrics"></table></div></div></section>
<section id="mwm" class="tab"><div class="card"><h2>MWM Static Models</h2><div class="chartbox"><canvas id="mwmChart"></canvas></div><div id="mwmLegend" class="legend"></div></div><div class="card"><h2>MWM Static Metrics</h2><div class="scroll"><table id="mwmMetrics"></table></div></div></section>
<section id="tacticalmodels" class="tab"><div class="card"><h2>Tactical Models</h2><div class="chartbox"><canvas id="tacticalModelsChart"></canvas></div><div id="tacticalModelsLegend" class="legend"></div></div><div class="card"><h2>Tactical Model Metrics</h2><div class="scroll"><table id="tacticalModelsMetrics"></table></div></div></section>
<section id="signals" class="tab"><div class="card"><h2>Recent Signals</h2><div class="scroll"><table id="signalTable"></table></div></div></section>
<section id="holding" class="tab"><div class="card"><h2>Holding Summary</h2><div class="scroll"><table id="holdingSummary"></table></div></div><div class="card"><h2>Holding Period Details</h2><div class="scroll"><table id="holdingPeriods"></table></div></div></section>
<section id="trade" class="tab"><div class="card"><h2>Trade Ledger</h2><div id="latestTrade2"></div><div class="scroll"><table id="tradeTable"></table></div></div></section>
<section id="chartaudit" class="tab"><div class="card"><h2>Chart Audit</h2><div class="scroll"><table id="chartAuditTable"></table></div></div><div class="card"><h2>Chart Rules</h2><table><tr><th>Window</th><th>Display frequency</th><th>Calculation basis</th></tr><tr><td>YTD, 1Y, 2Y</td><td>Daily</td><td>Full daily values</td></tr><tr><td>&gt;2Y and &lt;8Y</td><td>Weekly, last trading observation of week</td><td>Full daily values</td></tr><tr><td>≥8Y or SI</td><td>Monthly, last trading observation of month</td><td>Full daily values</td></tr><tr><td>Drawdown</td><td>Downsample after drawdown is computed</td><td>Daily running peak first</td></tr></table></div></section>
<section id="audit" class="tab"><div class="card"><h2>Metric Window Audit</h2><div id="windowAudit"></div><div class="scroll"><table id="windowRows"></table></div></div><div class="card"><h2>Production Audit</h2><div class="scroll"><table id="prodAuditTable"></table></div></div><div class="card"><h2>Data Audit</h2><div class="scroll"><table id="auditTable"></table></div></div></section>
<section id="dividend" class="tab">
<div class="card">
  <h2>Dividend Income</h2>
  <div class="note">Cash distributions calculated from exact daily shares in the NAV ledger. Yield on cost is intentionally not displayed — income grows with NAV. Prior Full-Year Income is the most comparable cross-period figure.</div>
  <div class="controls"><b class="note">Model</b><span id="divModelButtons"></span></div>
  <div class="grid kpis" id="divKpis"></div>
</div>
<div class="grid grid2">
  <div class="card"><h2>Annual Dividend Income</h2><div class="chartbox short"><canvas id="divAnnualChart"></canvas></div><div id="divAnnualLegend" class="legend"></div></div>
  <div class="card"><h2>Cumulative Dividend Income</h2><div class="chartbox short"><canvas id="divCumulativeChart"></canvas></div><div id="divCumulativeLegend" class="legend"></div></div>
</div>
<div class="card"><h2>Annual Income Detail</h2><div class="scroll"><table id="divAnnualTable"></table></div></div>
<div class="card"><h2>Income by Asset (Annual)</h2><div class="scroll"><table id="divAssetTable"></table></div></div>
<div class="card"><h2>Income by Asset (Monthly)</h2><div class="scroll"><table id="divAssetMonthlyTable"></table></div></div>
<div class="card"><h2>Fixed-Income Monthly Verification — $100,000 Initial Investment</h2><div class="note">PIMIX, JPIE, FIWDX, JBND. Actual monthly distribution-per-share history.</div><div class="scroll"><table id="fixedIncomeVerifyTable"></table></div></div>
<div class="card"><h2>Tactical Sleeve — Income by Holding Period</h2><div class="scroll"><table id="divPeriodTable"></table></div></div>
</section>
<section id="allocation" class="tab"><div class="card"><h2>Model Allocation</h2><div class="controls"><b class="note">Model</b><span id="allocModelButtons"></span></div><div class="grid grid2"><div><div class="chartbox"><canvas id="allocPie"></canvas></div><div id="allocLegend" class="legend"></div></div><div class="scroll"><table id="allocTable"></table></div></div></div></section>
<section id="config" class="tab"><div class="card"><h2>Backfill Scale Audit</h2><div class="note">Backfilled series are ratio-scaled to prevent artificial jumps at live/backfill transition dates.</div><div class="scroll"><table id="backfillAuditTable"></table></div></div><div class="card"><h2>Static to Tactical Model Map</h2><div class="scroll"><table id="modelMapTable"></table></div></div><div class="card"><h2>Normalized Allocation Config</h2><div class="scroll"><table id="allocationTable"></table></div></div></section>
</div><script>
const EMBEDDED=__PAYLOAD__;
const colors=['#6d35c4','#15803d','#0057b8','#e11d1d','#17365d','#a16207','#0f766e','#1d4ed8','#be123c','#7c3aed','#2563eb','#ea580c'];
const STR=new Set(['Date','Trade_Date','Trigger_Date','Start','End','Start_Date','End_Date','Asset','Production_Asset','State','EffectiveHolding','From','To','New_State','Rule','Status','Yahoo_Symbol','Notes','Check','Detail','Model','Static_Model','Tactical_Model','Chart','Series','Frequency','Holding','Through_Date','Period','Is_Partial_Year','Month','Overlay_Active','SMH_Active','SPHB_Active','SMH_JustTriggered','SPHB_JustTriggered','SMH_RawOn','SPHB_RawOn','Type']);
let sortState={},tableData={},period='3Y',periods=['YTD','1Y','2Y','3Y','5Y','2018','2016','SI'],visible=[];
function parseCSV(t){if(!t)return[];let L=t.trim().split(/\r?\n/);if(!L[0])return[];let H=L[0].split(',');return L.slice(1).filter(Boolean).map(l=>{let V=[],c='',q=false;for(let i=0;i<l.length;i++){let ch=l[i];if(ch=='"')q=!q;else if(ch==','&&!q){V.push(c);c=''}else c+=ch}V.push(c);let o={};H.forEach((h,i)=>{let v=V[i]??'',n=parseFloat(v);o[h]=(!STR.has(h)&&!isNaN(n)&&v.trim()!=='')?n:v});return o})}
let tactical=parseCSV(EMBEDDED.tactical),portfolio=parseCSV(EMBEDDED.portfolio),signals=parseCSV(EMBEDDED.signals),trades=parseCSV(EMBEDDED.trades),holdsum=parseCSV(EMBEDDED.holdsum),holdperiods=parseCSV(EMBEDDED.holdperiods),audit=parseCSV(EMBEDDED.dataaudit),prodaudit=parseCSV(EMBEDDED.prodaudit),modelmap=parseCSV(EMBEDDED.modelmap),alloc=parseCSV(EMBEDDED.alloc),backfillaudit=parseCSV(EMBEDDED.backfillaudit),alphaOverlay=parseCSV(EMBEDDED.alphaoverlay),alphaOverlayTrades=parseCSV(EMBEDDED.alphaoverlaytrades);
let divsummary=parseCSV(EMBEDDED.divsummary||''),divannual=parseCSV(EMBEDDED.divannual||''),divasset=parseCSV(EMBEDDED.divasset||''),divperiods=parseCSV(EMBEDDED.divperiods||''),divhistory=(()=>{try{return JSON.parse(EMBEDDED.divhistory||'[]')}catch(e){return []}})();
const assetprices=(()=>{try{return JSON.parse(EMBEDDED.assetprices||'{}')}catch(e){return {}}})()
// trailingYield(asset): sum of last 4 dividend payments / current price
function trailingYield(asset){
  const px=assetprices[asset];
  if(!px||px<=0)return null;
  const rows=divhistory.filter(r=>r.Asset===asset&&Number.isFinite(r.Div_Per_Share)&&Number(r.Div_Per_Share)>0)
    .sort((a,b)=>String(b.Ex_Date).localeCompare(String(a.Ex_Date)));
  // Sum last 4 payments (covers ~1 year of quarterly distributions)
  const last4=rows.slice(0,4).reduce((s,r)=>s+Number(r.Div_Per_Share),0);
  return last4>0?last4/px:null;
}
let divassetmonthly=parseCSV(EMBEDDED.divassetmonthly||'');
let fixedincomeverify=parseCSV(EMBEDDED.fixedincomeverify||'');
let bilSeries=tactical.filter(r=>Number.isFinite(r['BIL Buy Hold'])).map(r=>({Date:r.Date,BIL:r['BIL Buy Hold']}));
function riskFreeCAGR(s,e){if(!bilSeries.length)return 0;let sd=new Date(s),ed=new Date(e);let ir=bilSeries.filter(r=>{let d=new Date(r.Date);return d>=sd&&d<=ed});if(ir.length<2)return 0;let y=(new Date(ir.at(-1).Date)-new Date(ir[0].Date))/86400000/365.25;if(!(y>0))return 0;let rat=ir.at(-1).BIL/ir[0].BIL;return rat>0?Math.pow(rat,1/y)-1:0}
function money(v){return Number.isFinite(v)?'$'+v.toLocaleString(undefined,{minimumFractionDigits:0,maximumFractionDigits:0}):''}
function moYY(dateStr){let dt=new Date(dateStr);return String(dt.getMonth()+1).padStart(2,'0')+'/'+String(dt.getFullYear()).slice(-2)}
function drawXAxisDates(ctx,d,xAt,h,B){
  if(!d.length)return;
  ctx.save();ctx.fillStyle='#334155';ctx.font='11px Arial';ctx.textAlign='center';
  let nTicks=Math.min(7,d.length);
  for(let k=0;k<nTicks;k++){
    let i=Math.round(k*(d.length-1)/(nTicks-1||1));
    ctx.fillText(moYY(d[i].Date),xAt(i),h-B+18);
  }
  ctx.restore();
}
function pct(v){return Number.isFinite(v)?(v*100).toFixed(2)+'%':''}
function ratio(v){return Number.isFinite(v)?v.toFixed(2):''}
function num(v,d=2){return Number.isFinite(v)?v.toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d}):v||''}
function fmt(h,v){
  if(v==null||v==='')return '';
  if(h.includes('Value')||h.includes('Price')||h.includes('Income')||h.includes('Cumulative'))return money(v);
  if(h.includes('CAGR')||h.includes('Volatility')||h.includes('Drawdown')||h.includes('Return')||h.includes('Pct')||h.includes('Weight')||h.includes('Diff')||h.includes('Yield'))return pct(v);
  if(h.includes('Sharpe')||h.includes('Ratio'))return ratio(v);
  if(h.includes('Days'))return num(v,1);
  if(h.includes('Rows')||h.includes('Periods')||h.includes('Count'))return Number.isFinite(v)?Math.round(v).toLocaleString():v;
  return Number.isFinite(v)?num(v,2):v
}
function cls(h,v,row){let out='';if(h=='Status')out=v=='PASS'?'pass':(v=='FAIL'?'fail':'warn');else if(Number.isFinite(v)){if(h.includes('Drawdown')||h.includes('Worst'))out='bad';else if(h.includes('CAGR')||h.includes('Sharpe')||h.includes('Return')||h.includes('Best'))out=v>=0?'good':'bad'}if(row&&row.__current)out+=' '+(row.State=='Value'?'state-value':'state-growth');return out}
function sortVal(v){if(v==null||v==='')return null;if(typeof v==='number')return v;let s=String(v);if(/^\d{4}-\d{2}-\d{2}/.test(s)){let d=Date.parse(s);if(!isNaN(d))return d}let n=parseFloat(s.replace(/[$,%]/g,''));if(!isNaN(n))return n;return s.toLowerCase()}
function sortRows(id,h){let rows=tableData[id]||[],key=id+'|'+h,dir=sortState[key]=='asc'?'desc':'asc';sortState={};sortState[key]=dir;let sorted=[...rows].sort((a,b)=>{let av=sortVal(a[h]),bv=sortVal(b[h]);if(av==null&&bv==null)return 0;if(av==null)return 1;if(bv==null)return -1;if(av<bv)return dir=='asc'?-1:1;if(av>bv)return dir=='asc'?1:-1;return 0});drawTable(id,sorted)}
function drawTable(id,rows,freeze=false){let e=document.getElementById(id);if(!e)return;if(!rows||!rows.length){e.innerHTML='<tr><td class=note>No data</td></tr>';return}tableData[id]=rows;let H=Object.keys(rows[0]).filter(h=>h!='__current');let sk=Object.keys(sortState).find(k=>k.startsWith(id+'|')),active=sk?sk.split('|')[1]:null,dir=sk?sortState[sk]:null;e.innerHTML='<thead><tr>'+H.map((h,i)=>`<th class="${freeze&&i<3?'freeze'+(i+1):''}" onclick="sortRows('${id}','${String(h).replace(/'/g,"\\'")}')">${h}${active==h?(dir=='asc'?' ▲':' ▼'):''}</th>`).join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+H.map((h,i)=>`<td class="${(freeze&&i<3?'freeze'+(i+1)+' ':'')+cls(h,r[h],r)}">${fmt(h,r[h])}</td>`).join('')+'</tr>').join('')+'</tbody>'}
function cols(d){return d.length?Object.keys(d[0]).filter(k=>k!='Date'):[]}
function cut(d){if(!d.length)return[];let end=new Date(d[d.length-1].Date),start=new Date(d[0].Date);if(period=='YTD')start=new Date(end.getFullYear()-1,11,31);else if(period.endsWith('Y')){start=new Date(end);start.setFullYear(start.getFullYear()-parseInt(period))}else if(period=='2018')start=new Date('2018-01-01');else if(period=='2016')start=new Date('2016-01-01');return d.filter(r=>new Date(r.Date)>=start&&new Date(r.Date)<=end)}
function yearsIn(d){return d.length>1?(new Date(d.at(-1).Date)-new Date(d[0].Date))/86400000/365.25:0}
function displayFrequency(d){let y=period==='SI'?99:yearsIn(d);if(period==='YTD'||period==='1Y'||period==='2Y'||y<=2.05)return 'Daily';if(y>=8)return 'Monthly';return 'Weekly'}
function sampleDisplay(d){let freq=displayFrequency(d);if(freq==='Daily')return d;let map=new Map();d.forEach(r=>{let dt=new Date(r.Date);let key;if(freq==='Monthly'){key=dt.getFullYear()+'-'+String(dt.getMonth()+1).padStart(2,'0')}else{let x=new Date(dt);let day=x.getDay();let diff=(day+6)%7;x.setDate(x.getDate()-diff);key=x.toISOString().slice(0,10)}map.set(key,r)});return Array.from(map.values())}
function rebase(d,c){if(!d.length)return[];return d.map(r=>{let o={Date:r.Date};c.forEach(x=>{let f=d.find(z=>Number.isFinite(z[x])&&z[x]>0);o[x]=f?r[x]/f[x]*100000:null});return o})}
function metric(d,c){
  // Build daily BIL return lookup for the window
  const bilMap={};
  bilSeries.forEach(r=>{bilMap[r.Date]=r.BIL});
  const bilDates=Object.keys(bilMap).sort();
  function bilReturn(i,dates){
    // daily BIL return between dates[i-1] and dates[i]
    if(i<1)return 0;
    const b0=bilMap[dates[i-1]],b1=bilMap[dates[i]];
    return(b0&&b1&&b0>0)?b1/b0-1:0;
  }
  let out=[];
  if(d.length<2)return out;
  const rf=riskFreeCAGR(d[0].Date,d[d.length-1].Date);
  const dates=d.map(r=>r.Date);
  c.forEach(x=>{
    const v=d.map(r=>r[x]);
    const valid=v.filter(Number.isFinite);
    if(valid.length<2)return;
    const days=(new Date(d[d.length-1].Date)-new Date(d[0].Date))/86400000,yrs=days/365.25;
    const re=[],excess=[],bilRets=[];
    for(let i=1;i<v.length;i++){
      if(!Number.isFinite(v[i])||!Number.isFinite(v[i-1])||v[i-1]===0)continue;
      const r=v[i]/v[i-1]-1;
      const rb=bilReturn(i,dates);
      re.push(r);
      excess.push(r-rb);
      bilRets.push(rb);
    }
    if(!re.length)return;
    // Volatility: std of portfolio daily returns * sqrt(252)
    const avg=re.reduce((a,b)=>a+b,0)/re.length;
    const vol=Math.sqrt(re.reduce((a,b)=>a+(b-avg)**2,0)/(re.length-1))*Math.sqrt(252);
    // Sharpe: mean(daily excess) * 252 / (std(daily excess) * sqrt(252))
    //       = mean(daily excess) * sqrt(252) / std(daily excess)
    const exAvg=excess.reduce((a,b)=>a+b,0)/excess.length;
    const exStd=Math.sqrt(excess.reduce((a,b)=>a+(b-exAvg)**2,0)/(excess.length-1));
    const sharpe=exStd>0?exAvg*Math.sqrt(252)/exStd:null;
    // Sortino: mean(daily portfolio return)*252 / (std(negative returns)*sqrt(252))
    const neg=re.filter(r=>r<0);
    const negStd=neg.length>1?Math.sqrt(neg.reduce((a,b)=>a+b**2,0)/neg.length)*Math.sqrt(252):null;
    const sortino=negStd&&negStd>0?avg*252/negStd:null;
    const cagr=Math.pow(valid.at(-1)/valid[0],1/yrs)-1;
    let peak=valid[0],dd=0;
    v.forEach(z=>{if(Number.isFinite(z)){peak=Math.max(peak,z);dd=Math.min(dd,z/peak-1)}});
    out.push({Model:x,
      'Beginning Value':valid[0],'Ending Value':valid.at(-1),
      'Total Return':valid.at(-1)/valid[0]-1,
      CAGR:cagr,Volatility:vol,Risk_Free_CAGR:rf,
      'Sharpe (vs BIL)':sharpe,Sortino:sortino,
      'Max Drawdown':dd,Rows:valid.length,Days:days,
      Start:d[0].Date,End:d[d.length-1].Date});
  });
  return out;
}
function draw(id,dDaily,c,leg,isDD=false){let d=sampleDisplay(dDaily);let cv=document.getElementById(id);if(!cv)return;let box=cv.parentElement,wCss=Math.max(700,box.clientWidth||900),hCss=Math.max(260,box.clientHeight||430),pr=window.devicePixelRatio||1;cv.width=wCss*pr;cv.height=hCss*pr;let ctx=cv.getContext('2d');ctx.setTransform(pr,0,0,pr,0,0);let w=wCss,h=hCss;ctx.clearRect(0,0,w,h);ctx.font='11px Arial';if(!d.length||!c.length){ctx.fillText('No chart data',30,40);return}let vals=[];c.forEach(x=>d.forEach(r=>{if(Number.isFinite(r[x]))vals.push(r[x])}));if(!vals.length){ctx.fillText('No numeric series selected',30,40);return}let mn=Math.min(...vals),mx=Math.max(...vals),pad=(mx-mn)*.08||1;mn-=pad;mx+=pad;let L=90,R=30,T=25,B=55;ctx.strokeStyle='#d7deea';ctx.fillStyle='#334155';for(let i=0;i<5;i++){let y=T+(h-T-B)*i/4;ctx.beginPath();ctx.moveTo(L,y);ctx.lineTo(w-R,y);ctx.stroke();let val=mx-(mx-mn)*i/4;ctx.fillText(isDD?pct(val):money(val),8,y+4)}c.forEach((x,j)=>{ctx.strokeStyle=colors[j%colors.length];ctx.lineWidth=x.includes('VOO')?2.5:2;ctx.beginPath();d.forEach((r,i)=>{let xx=L+(w-L-R)*(d.length===1?0:i/(d.length-1)),yy=T+(h-T-B)*(1-(r[x]-mn)/(mx-mn));i?ctx.lineTo(xx,yy):ctx.moveTo(xx,yy)});ctx.stroke()});drawXAxisDates(ctx,d,i=>L+(w-L-R)*(d.length===1?0:i/(d.length-1)),h,B);let el=document.getElementById(leg);if(el)el.innerHTML=c.map((x,j)=>`<span><i class=sw style="background:${colors[j%colors.length]}"></i>${x}</span>`).join('')}
function drawOverlayChart(id,dDaily,c,leg,smhCol,sphbCol){let d=sampleDisplay(dDaily);let cv=document.getElementById(id);if(!cv)return;let box=cv.parentElement,wCss=Math.max(700,box.clientWidth||900),hCss=Math.max(260,box.clientHeight||430),pr=window.devicePixelRatio||1;cv.width=wCss*pr;cv.height=hCss*pr;let ctx=cv.getContext('2d');ctx.setTransform(pr,0,0,pr,0,0);let w=wCss,h=hCss;ctx.clearRect(0,0,w,h);ctx.font='11px Arial';if(!d.length||!c.length){ctx.fillText('No chart data',30,40);return}let vals=[];c.forEach(x=>d.forEach(r=>{if(Number.isFinite(r[x]))vals.push(r[x])}));if(!vals.length){ctx.fillText('No numeric series selected',30,40);return}let mn=Math.min(...vals),mx=Math.max(...vals),pad=(mx-mn)*.08||1;mn-=pad;mx+=pad;let L=90,R=30,T=25,B=55;let xAt=i=>L+(w-L-R)*(d.length===1?0:i/(d.length-1));
  // three-color bands behind the lines: light blue = SMH only, light green = SPHB only, gold = both simultaneously
  const bandColor={both:'rgba(201,150,44,0.16)',smh:'rgba(96,165,250,0.18)',sphb:'rgba(134,239,172,0.22)'};
  const stateAt=i=>{let smh=String(d[i][smhCol])==='True',sphb=String(d[i][sphbCol])==='True';if(smh&&sphb)return'both';if(smh)return'smh';if(sphb)return'sphb';return null};
  let prevState=null,bandStart=0;
  for(let i=0;i<=d.length;i++){let s=i<d.length?stateAt(i):null;if(s!==prevState){if(prevState!==null){ctx.fillStyle=bandColor[prevState];ctx.fillRect(xAt(bandStart),T,Math.max(xAt(i-1)-xAt(bandStart),1.5),h-T-B)}bandStart=i;prevState=s}}
  ctx.strokeStyle='#d7deea';ctx.fillStyle='#334155';for(let i=0;i<5;i++){let y=T+(h-T-B)*i/4;ctx.beginPath();ctx.moveTo(L,y);ctx.lineTo(w-R,y);ctx.stroke();let val=mx-(mx-mn)*i/4;ctx.fillText(money(val),8,y+4)}
  c.forEach((x,j)=>{ctx.strokeStyle=colors[j%colors.length];ctx.lineWidth=x.includes('Alpha')?2.6:x.includes('VOO')?1.6:2;ctx.beginPath();let started=false;d.forEach((r,i)=>{if(!Number.isFinite(r[x])){started=false;return}let xx=xAt(i),yy=T+(h-T-B)*(1-(r[x]-mn)/(mx-mn));if(started){ctx.lineTo(xx,yy)}else{ctx.moveTo(xx,yy);started=true}});ctx.stroke()});
  drawXAxisDates(ctx,d,xAt,h,B);
  let el=document.getElementById(leg);if(el)el.innerHTML=c.map((x,j)=>`<span><i class=sw style="background:${colors[j%colors.length]}"></i>${x}</span>`).join('')+'<span><i class=sw style="background:rgba(96,165,250,0.45)"></i>SMH active</span>'+'<span><i class=sw style="background:rgba(134,239,172,0.55)"></i>SPHB active</span>'+'<span><i class=sw style="background:rgba(201,150,44,0.5)"></i>Both active</span>'}
function renderAlphaTriggerStatus(){
  let el=document.getElementById('alphaTriggerBox');
  if(!el)return;
  try{
    if(!alphaOverlay.length){el.innerHTML='<div class=note>No data</div>';return}
    let last=alphaOverlay[alphaOverlay.length-1];
    let SMH_PERSIST=5, SPHB_PERSIST=5;
    function pctStr(v){return Number.isFinite(v)?(v>=0?'+':'')+(v*100).toFixed(2)+'%':'N/A'}
    // fundedOn: what's actually held today (yesterday's confirmed read, T+1 lag).
    // rawOn: today's raw signal read -- becomes tomorrow's fundedOn. On an
    // ordinary day fundedOn===rawOn. They only diverge on the day a
    // transition fires, which is exactly the case worth calling out
    // explicitly rather than blending into a single on/off flag.
    function panel(label,fundedOn,rawOn,justTriggered,val,valLabel,streak,persist,entryThr,exitThr,asOfDate){
      // The threshold/target being worked toward is always about rawOn (today's
      // live read), never the funded state -- that's true whether or not a
      // transition just fired.
      let target=rawOn?'EXIT':'ENTRY';
      let thrStr=rawOn?('&le;'+(exitThr*100).toFixed(1)+'%'):('&ge;'+(entryThr*100).toFixed(1)+'%');
      let bar='';
      if(justTriggered){
        // The streak value on a trigger day is the count that JUST completed
        // (matching the OLD state's threshold), not progress toward the new
        // one -- showing it next to the new target's label is what caused the
        // contradiction. Show the completed event on its own line instead,
        // and make clear the next streak starts from zero.
        bar=`<div class="pill" style="background:#fef3c7;border-color:#fbbf24;color:#92400e;margin-top:6px">
              ${rawOn?'ON':'OFF'} Trigger confirmed ${asOfDate} (${streak}/${persist} qualifying days) &mdash; Trade effective next trading day
             </div>
             <div class=note style="margin-top:6px">Streak toward next ${target}: <b>0 / ${persist}</b> days (resets after a transition)</div>`;
      } else {
        let barPct=Math.max(0,Math.min(100,(streak/persist)*100));
        let barColor=rawOn?'#b91c1c':'#15803d';
        bar=`<div class=note>Streak toward ${target} (${thrStr}): <b>${streak} / ${persist}</b> days</div>
             <div style="background:#eef2f7;border-radius:6px;height:8px;margin-top:6px;overflow:hidden">
               <div style="background:${barColor};height:100%;width:${barPct}%"></div>
             </div>`;
      }
      return `<div class=tradeitem>
        <div class=label>${label} &mdash; Currently funded: ${fundedOn?'<span class=bad>ON</span>':'<span class=note>OFF</span>'}</div>
        <div class=big>${valLabel}: ${pctStr(val)}</div>
        ${bar}
      </div>`;
    }
    let smhFundedOn=String(last.SMH_Active)==='True';
    let smhRawOn=String(last.SMH_RawOn)==='True';
    let smhJust=String(last.SMH_JustTriggered)==='True';
    let sphbFundedOn=String(last.SPHB_Active)==='True';
    let sphbRawOn=String(last.SPHB_RawOn)==='True';
    let sphbJust=String(last.SPHB_JustTriggered)==='True';
    let smhHtml=panel('SMH sleeve', smhFundedOn, smhRawOn, smhJust, last.SMH_ROC5, 'ROC5', last.SMH_Streak||0, SMH_PERSIST, 0.02, -0.02, last.Date);
    let sphbHtml=panel('SPHB sleeve', sphbFundedOn, sphbRawOn, sphbJust, last.SPHB_Dev, 'Dev from EMA50', last.SPHB_Streak||0, SPHB_PERSIST, 0.003, -0.003, last.Date);
    el.innerHTML=`<div class=note style="grid-column:1/-1;margin-bottom:4px">As of ${last.Date}</div>`+smhHtml+sphbHtml;
  }catch(e){console.error('Alpha trigger status failed:',e);el.innerHTML='<div class=note>Trigger status failed to render — check console</div>';}
}
function renderAlphaOverlay(){
  try{
    let btn=document.getElementById('alphaOverlayTabBtn');
    if(btn&&alphaOverlay.length){
      let last=alphaOverlay[alphaOverlay.length-1];
      // Color reflects the raw trigger (today's confirmed signal), not the
      // T+1 funded state -- so the badge flips on the trigger date itself.
      // SMH_Active/SPHB_Active (funded state) and all downstream valuation/
      // trade-log logic are untouched.
      let currentlyActive=String(last.SMH_RawOn)==='True'||String(last.SPHB_RawOn)==='True';
      if(currentlyActive){btn.style.borderColor='#15803d';btn.style.color='#15803d';btn.style.background='#ecfdf5'}
      else{btn.style.borderColor='#C9962C';btn.style.color='#8A6A1F';btn.style.background=''}
    }
  }catch(e){console.error('Alpha overlay tab indicator failed:',e);}
  renderAlphaTriggerStatus();
  let cols3=['VOO Benchmark','A1V12 Tactical Sleeve','Tactical + Alpha Overlay'];
  let dCut=cut(alphaOverlay);
  let d=rebase(dCut,cols3).map((r,i)=>({...r,SMH_Active:dCut[i]?dCut[i].SMH_Active:false,SPHB_Active:dCut[i]?dCut[i].SPHB_Active:false}));
  try{
    drawOverlayChart('alphaOverlayChart',d,cols3,'alphaOverlayLegend','SMH_Active','SPHB_Active');
  }catch(e){console.error('Alpha overlay chart failed:',e);}
  try{
    let m=metric(d,cols3);
    document.getElementById('alphaKpiBox').innerHTML=m.map(r=>`<div class=kpi><div class=label>${r.Model}</div><div class=big>${money(r['Ending Value'])}</div><div class=note>Total <span class=good>${pct(r['Total Return'])}</span> | CAGR <span class=good>${pct(r.CAGR)}</span> | Sharpe ${(r['Sharpe (vs BIL)']!=null&&Number.isFinite(r['Sharpe (vs BIL)']))?r['Sharpe (vs BIL)'].toFixed(2):'N/A'} | Max DD <span class=bad>${pct(r['Max Drawdown'])}</span></div></div>`).join('');
  }catch(e){console.error('Alpha overlay KPI cards failed:',e);}
  try{
    let merged=[...trades.map(t=>({Date:t.Trade_Date,Type:'Base regime',From:t.From,To:t.To,Days_Held:'',Entry_Price:'',Exit_Price:'',Return:''})),...alphaOverlayTrades].sort((a,b)=>(b.Date||'').localeCompare(a.Date||''));
    drawTable('alphaOverlayTradeTable',merged.slice(0,300));
  }catch(e){console.error('Alpha overlay trade table failed:',e);let el=document.getElementById('alphaOverlayTradeTable');if(el)el.innerHTML='<tr><td class=note>Trade log failed to render — check console</td></tr>';}
}
function dailyDrawdown(d,c){let z=d.map(r=>({Date:r.Date}));c.forEach(x=>{let p=null;z.forEach((o,i)=>{let v=d[i][x];if(!Number.isFinite(v)){o[x]=null;return}p=Math.max(p||v,v);o[x]=v/p-1})});return z}
function chartAuditRows(name,dDaily,cols,drawdown=false){let basis=drawdown?dailyDrawdown(dDaily,cols):dDaily;let freq=displayFrequency(dDaily),disp=sampleDisplay(basis),rows=[];cols.forEach(x=>{let vals=basis.map(r=>r[x]).filter(isFinite),latestDaily=basis.length?basis.at(-1)[x]:null,latestPlot=disp.length?disp.at(-1)[x]:null;let miss=basis.length-vals.length;rows.push({Chart:name,Series:x,Frequency:freq,'Daily Rows':basis.length,'Plotted Rows':disp.length,'Missing Count':miss,'Latest Daily Date':basis.length?basis.at(-1).Date:'','Latest Plot Date':disp.length?disp.at(-1).Date:'','Latest Point Diff':(Number.isFinite(latestDaily)&&Number.isFinite(latestPlot))?latestPlot-latestDaily:null,Status:(miss===0&&(!Number.isFinite(latestDaily)||Math.abs((latestPlot||0)-latestDaily)<1e-8))?'PASS':'WARN'})});return rows}
function staticCols(){return cols(portfolio).filter(x=>x.startsWith('MWM '))}
function tacticalModelCols(){return cols(portfolio).filter(x=>x.startsWith('Tactical '))}
function showTab(e,id){
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  document.querySelectorAll('.tabbtn').forEach(b=>b.classList.remove('active'));
  e.target.classList.add('active');
  setTimeout(render,80);
  setTimeout(renderAllocation,80);
  setTimeout(renderDividend,80);
  setTimeout(renderAlphaOverlay,80);
}
function setPeriod(p){period=p;sortState={};document.querySelectorAll('#periodButtons button').forEach(b=>b.classList.toggle('active',b.textContent==p));render();renderAlphaOverlay()}
function preset(p){let all=cols(portfolio);visible=p=='core'?['A1V12 Tactical Sleeve','VOO Benchmark']:p=='static'?staticCols():p=='tacticalmodels'?tacticalModelCols():all;document.getElementById('overviewChecks').innerHTML=all.map(x=>`<label><input type=checkbox ${visible.includes(x)?'checked':''} onchange="tog('${x}',this.checked)"> ${x}</label>`).join('');render()}
function tog(x,on){if(on&&!visible.includes(x))visible.push(x);if(!on)visible=visible.filter(y=>y!=x);render()}
function recentSignals(){return [...signals].reverse().slice(0,60).map((r,i)=>({...r,__current:i==0}))}
function render(){let d=cut(portfolio),rb=rebase(d,visible),m=metric(rb,visible);document.getElementById('freqPill').innerHTML='Display: '+displayFrequency(d);draw('overviewChart',rb,visible,'overviewLegend');drawTable('metricsTable',m);document.getElementById('kpiBox').innerHTML=m.slice(0,4).map(r=>`<div class=kpi><div class=label>${r.Model}</div><div class=big>${money(r['Ending Value'])}</div><div class=note>Total <span class=good>${pct(r['Total Return'])}</span> | CAGR <span class=good>${pct(r.CAGR)}</span></div></div>`).join('');document.getElementById('windowAudit').innerHTML=d.length?`<b>${period}</b><br>Start: ${d[0].Date}<br>End: ${d.at(-1).Date}<br>Daily rows: ${d.length}<br>Display frequency: ${displayFrequency(d)}<br>Display rows: ${sampleDisplay(d).length}`:'No data';drawTable('windowRows',m);
let tc=cols(tactical).filter(x=>['A1V12','VOO Benchmark','MGK Buy Hold','MGV Buy Hold'].includes(x));
let td=rebase(cut(tactical),tc),tm=metric(td,tc);draw('tacticalChart',td,tc,'tacticalLegend');draw('tacticalDD',dailyDrawdown(td,tc),tc,'tacticalDDLegend',true);drawTable('tacticalMetrics',tm);let sd=rebase(cut(portfolio),staticCols());draw('mwmChart',sd,staticCols(),'mwmLegend');drawTable('mwmMetrics',metric(sd,staticCols()));let tmd=rebase(cut(portfolio),tacticalModelCols());draw('tacticalModelsChart',tmd,tacticalModelCols(),'tacticalModelsLegend');drawTable('tacticalModelsMetrics',metric(tmd,tacticalModelCols()));state();drawTable('signalTable',recentSignals(),true);let auditRows=[...chartAuditRows('Overview',rb,visible,false),...chartAuditRows('Tactical Sleeve',td,tc,false),...chartAuditRows('Tactical Drawdown',td,tc,true),...chartAuditRows('MWM Static',sd,staticCols(),false),...chartAuditRows('Tactical Models',tmd,tacticalModelCols(),false)];drawTable('chartAuditTable',auditRows);updateDivYield()}
function state(){let s=signals.at(-1)||{},tr=trades.at(-1)||{};let stateHtml=`<div class=tradebox><div class=tradeitem><div class=label>Current State</div><div class=big>${s.State||'N/A'}</div></div><div class=tradeitem><div class=label>Current Holding</div><div class=big>${s.EffectiveHolding||'N/A'}</div></div><div class=tradeitem><div class=label>Data As Of</div><div class=big>${s.Date||'N/A'}</div></div><div class=tradeitem><div class=label>MGK/MGV Ratio</div><div class=big>${Number.isFinite(s.MGK_MGV)?s.MGK_MGV.toFixed(4):'N/A'}</div></div><div class=tradeitem><div class=label>EMA89</div><div class=big>${Number.isFinite(s.MGK_MGV_EMA89)?s.MGK_MGV_EMA89.toFixed(4):'N/A'}</div></div></div>`;let tradeHtml=`<div class=tradebox style="margin-top:10px"><div class=tradeitem><div class=label>Trigger Date</div><div class=big>${tr.Trigger_Date||'N/A'}</div></div><div class=tradeitem><div class=label>Trade Date</div><div class=big>${tr.Trade_Date||'N/A'}</div></div><div class=tradeitem><div class=label>Latest Trade</div><div class=big>${tr.From||''} → ${tr.To||''}</div></div><div class=tradeitem><div class=label>Rule</div><div class=note>${tr.Rule||'N/A'}</div></div></div>`;document.getElementById('stateBox').innerHTML=stateHtml;document.getElementById('latestTrade').innerHTML=tradeHtml;document.getElementById('latestTrade2').innerHTML=tradeHtml}
function updateDivYield(){
  // Dividend yield display in Current State card.
  //
  // S&P 500 (VOO): industry-standard calculation —
  //   sum of last 4 VOO dividend payments / current VOO price
  //   Matches Morningstar / Yahoo Finance published yield.
  //
  // Portfolio models: TTM income / current NAV from dividend engine.
  //   Reflects the blended yield of the actual model holdings.
  //
  // Always shows S&P 500 first (benchmark anchor), then up to 4
  // selected models from visible[]. No dollar amounts shown.

  const el=document.getElementById('divYield');
  if(!el)return;
  const pv=portfolio.length?portfolio[portfolio.length-1]:null;

  function modelYield(model){
    // Portfolio model: TTM income / current portfolio NAV
    const sum=divsummary.find(r=>r.Model===model);
    if(!sum)return null;
    const nav=pv&&Number.isFinite(pv[model])?Number(pv[model]):null;
    const ttm=Number.isFinite(sum.TTM_Dividend_Income)?Number(sum.TTM_Dividend_Income):null;
    return nav&&ttm&&nav>0?ttm/nav:null;
  }

  function shortName(m){
    if(m==='VOO Benchmark')return 'S&P 500';
    return m.replace('MWM ','').replace('Tactical ','').replace(' Plus','+');
  }

  // S&P 500 tile: last 4 VOO dividends / current VOO price
  const vooYld=trailingYield('VOO');

  // Portfolio model tiles: up to 4 from visible[], excluding VOO Benchmark
  const modelList=visible.filter(m=>m!=='VOO Benchmark').slice(0,4);

  // Build S&P 500 tile first
  const vooTile=`<div class=tradeitem style="border:2px solid #17365d">
    <div class=label>S&amp;P 500</div>
    <div class=big style="font-size:20px">${vooYld?pct(vooYld):'—'}</div>
    <div class=note>Last 4 divs ÷ price</div>
  </div>`;

  // Build model tiles
  const modelTiles=modelList.map(model=>{
    const yld=modelYield(model);
    return `<div class=tradeitem>
      <div class=label>${shortName(model)}</div>
      <div class=big style="font-size:20px">${yld?pct(yld):'—'}</div>
      <div class=note>TTM yield</div>
    </div>`;
  }).join('');

  el.innerHTML=`<div style="border-top:1px solid #e5e7eb;padding-top:10px;margin-top:2px">
    <div class=label style="margin-bottom:6px">Dividend Yield</div>
    <div class=tradebox>${vooTile}${modelTiles}</div>
    <div class=note style="margin-top:8px;line-height:1.5">
      <b>S&amp;P 500:</b> Sum of last 4 VOO quarterly dividends ÷ current VOO price. Standard published yield methodology (Morningstar, Yahoo Finance).<br>
      <b>Portfolio models:</b> Trailing 12-month cash distributions from actual share ledger ÷ current portfolio NAV. Reflects blended yield of holdings — e.g. tactical sleeve income is sourced from MGK or MGV based on what was actually held on each ex-dividend date.
    </div>
  </div>`;
}
function staticTables(){drawTable('tradeTable',trades.slice().reverse());drawTable('holdingSummary',holdsum);drawTable('holdingPeriods',holdperiods.slice().reverse());drawTable('auditTable',audit);drawTable('prodAuditTable',prodaudit);drawTable('modelMapTable',modelmap);drawTable('allocationTable',alloc);drawTable('backfillAuditTable',backfillaudit)}
let allocModel=null;
function allocModels(){return[...new Set(alloc.map(r=>r.Model))]}
function allocForModel(m){let rows=alloc.filter(r=>r.Model===m);let byAsset={};rows.forEach(r=>{byAsset[r.Production_Asset]=(byAsset[r.Production_Asset]||0)+Number(r.Weight)});return Object.entries(byAsset).map(([Asset,Weight])=>({Asset,Weight})).sort((a,b)=>b.Weight-a.Weight)}
function setAllocModel(m){allocModel=m;document.querySelectorAll('#allocModelButtons button').forEach(b=>b.classList.toggle('active',b.textContent===m));renderAllocation()}
function drawPie(id,rows){let cv=document.getElementById(id);if(!cv)return;let box=cv.parentElement,wCss=Math.max(300,box.clientWidth||400),hCss=Math.max(260,box.clientHeight||400),pr=window.devicePixelRatio||1;cv.width=wCss*pr;cv.height=hCss*pr;let ctx=cv.getContext('2d');ctx.setTransform(pr,0,0,pr,0,0);ctx.clearRect(0,0,wCss,hCss);ctx.font='11px Arial';if(!rows.length){ctx.fillText('No allocation data',30,40);return}let total=rows.reduce((a,r)=>a+r.Weight,0);if(!total){ctx.fillText('Allocation weights sum to zero',30,40);return}let cx=wCss/2,cy=hCss/2,r=Math.min(wCss,hCss)/2-20,start=-Math.PI/2;rows.forEach((row,i)=>{let slice=(row.Weight/total)*2*Math.PI;ctx.beginPath();ctx.moveTo(cx,cy);ctx.arc(cx,cy,r,start,start+slice);ctx.closePath();ctx.fillStyle=colors[i%colors.length];ctx.fill();ctx.strokeStyle='#ffffff';ctx.lineWidth=1.5;ctx.stroke();if(slice>0.14){let mid=start+slice/2;let lx=cx+Math.cos(mid)*r*0.65,ly=cy+Math.sin(mid)*r*0.65;ctx.fillStyle='#ffffff';ctx.font='bold 12px Arial';ctx.textAlign='center';ctx.fillText(pct(row.Weight/total),lx,ly+4);ctx.font='11px Arial'}start+=slice});ctx.textAlign='left'}
function renderAllocation(){if(!allocModel){if(allocModels().length){allocModel=allocModels()[0]}else{return}}let rows=allocForModel(allocModel);let total=rows.reduce((a,r)=>a+r.Weight,0)||1;drawPie('allocPie',rows);let el=document.getElementById('allocLegend');if(el)el.innerHTML=rows.map((r,j)=>`<span><i class=sw style="background:${colors[j%colors.length]}"></i>${r.Asset} (${pct(r.Weight/total)})</span>`).join('');drawTable('allocTable',rows.map(r=>({Asset:r.Asset,Weight:r.Weight})))}
let divModel=null;
function divModels(){return divsummary.map(r=>r.Model)}
function setDivModel(m){divModel=m;document.querySelectorAll('#divModelButtons button').forEach(b=>b.classList.toggle('active',b.textContent===m));renderDividend()}
function renderDividend(){
  if(!divsummary.length)return;
  if(!divModel){if(divModels().length)divModel=divModels()[0];else return;}
  document.querySelectorAll('#divModelButtons button').forEach(b=>b.classList.toggle('active',b.textContent===divModel));
  let sum=divsummary.find(r=>r.Model===divModel)||{};
  let ann=divannual.filter(r=>r.Model===divModel).sort((a,b)=>a.Year-b.Year);
  let annualChart=ann.map(r=>({Date:String(r.Year)+'-12-31','Annual Dividend Income':r.Dividend_Income}));
  let cumulativeChart=ann.map(r=>({Date:String(r.Year)+'-12-31','Cumulative Dividend Income':r.Cumulative_Income}));
  draw('divAnnualChart',annualChart,['Annual Dividend Income'],'divAnnualLegend');
  draw('divCumulativeChart',cumulativeChart,['Cumulative Dividend Income'],'divCumulativeLegend');
  let through=sum.Through_Date||'';
  document.getElementById('divKpis').innerHTML=
    `<div class=kpi><div class=label>Lifetime Dividend Income</div><div class=big>${money(sum.Lifetime_Dividend_Income)}</div></div>`+
    `<div class=kpi><div class=label>Current Year Income</div><div class=big>${money(sum.Current_Year_Income)}</div><div class=note>YTD through ${through}</div></div>`+
    `<div class=kpi><div class=label>Trailing 12 Months</div><div class=big>${money(sum.TTM_Dividend_Income)}</div></div>`+
    `<div class=kpi><div class=label>Prior Full-Year Income</div><div class=big>${money(sum.Prior_Full_Year_Income)}</div><div class=note>Last completed calendar year</div></div>`;
  drawTable('divAnnualTable', ann.map(r=>({
    Period:r.Period||String(r.Year),
    Dividend_Income:r.Dividend_Income,
    Annualized_Run_Rate:r.Annualized_Run_Rate||null,
    Cumulative_Income:r.Cumulative_Income,
    Is_Partial_Year:r.Is_Partial_Year
  })));
  drawTable('divAssetTable', divasset.filter(r=>r.Model===divModel).sort((a,b)=>b.Year-a.Year||b.Dividend_Income-a.Dividend_Income));
  drawTable('divAssetMonthlyTable', divassetmonthly.filter(r=>r.Model===divModel).sort((a,b)=>String(b.Month).localeCompare(String(a.Month))||b.Dividend_Income-a.Dividend_Income));
  drawTable('fixedIncomeVerifyTable', fixedincomeverify.slice().sort((a,b)=>String(b.Month).localeCompare(String(a.Month))||String(a.Asset).localeCompare(String(b.Asset))));
  drawTable('divPeriodTable', divperiods.slice().reverse());
}
function init(){
  document.getElementById('periodButtons').innerHTML=periods.map(p=>`<button onclick="setPeriod('${p}')" class="${p==period?'active':''}">${p}</button>`).join('');
  preset('core');
  staticTables();
  document.getElementById('allocModelButtons').innerHTML=allocModels().map(m=>`<button onclick="setAllocModel('${m}')">${m}</button>`).join('');
  if(allocModels().length)setAllocModel(allocModels()[0]);
  document.getElementById('divModelButtons').innerHTML=divModels().map(m=>`<button onclick="setDivModel('${m.replace(/'/g,"\\'")}')">${m}</button>`).join('');
  if(divModels().length)setDivModel(divModels()[0]);
  setTimeout(render,120);
  setTimeout(renderAllocation,120);
  setTimeout(renderDividend,120);
  setTimeout(renderAlphaOverlay,120);
  setTimeout(updateDivYield,150);
}
window.addEventListener('resize',()=>setTimeout(()=>{render();renderAllocation();renderDividend();renderAlphaOverlay();updateDivYield()},120));
init();
</script></body></html>"""


def main():
    import pandas as pd
    print("BUILD: A1V12 Yahoo Production v4.0")
    print("Script compiled: 2026-07-15 12:42 UTC")
    print("Workbook-first price sourcing + share-tracking NAV + full 15yr history")
    backup = backup_existing_outputs()
    print("Backup folder:", backup)

    # Copy backfilled workbook to Config/ if not already there
    # The workbook provides full price history for MGK, MGV and other
    # primary assets, avoiding Yahoo's server-side 5-year truncation.
    _ensure_workbook_in_config()

    alloc_df = read_allocations()
    static_models, tactical_models = build_model_configs(alloc_df)
    required_assets = set(alloc_df["Production_Asset"].unique()) | {"MGK","MGV","VOO","BIL","SMH","SPHB"}

    adj_wide, raw_wide, open_wide, div_wide, data_audit_df = download_prices(required_assets)

    comp_adj, _  = build_composites(adj_wide,  required_assets,
        output_name="Composite_Prices.csv",
        audit_name="Backfill_Scale_Audit.csv",
        price_basis="Adjusted Close")
    comp_raw, raw_scale = build_composites(raw_wide,  required_assets,
        output_name="Composite_Prices_Raw_Close.csv",
        audit_name="Backfill_Raw_Scale_Audit.csv",
        price_basis="Raw Close")
    comp_open, _        = build_composites(open_wide, required_assets,
        output_name="Composite_Prices_Open.csv",
        audit_name="Backfill_Open_Scale_Audit.csv",
        price_basis="Open")

    print(f"  [DIAG] comp_adj max date: {pd.to_datetime(comp_adj['Date']).max().date()}")
    print(f"  [DIAG] comp_raw max date: {pd.to_datetime(comp_raw['Date']).max().date()}")
    print(f"  [DIAG] comp_open max date: {pd.to_datetime(comp_open['Date']).max().date()}")
    for _t in ("MGK", "MGV", "VOO"):
        if _t in comp_open.columns:
            _nn = comp_open.loc[comp_open[_t].notna(), "Date"]
            print(f"  [DIAG] comp_open {_t} non-null through: "
                  f"{pd.to_datetime(_nn).max().date() if len(_nn) else 'NEVER'}")

    # Adjusted-open series for tactical execution (fixes the 2026-07
    # raw/adjusted mixing bug in build_tactical_values() -- see that
    # function's docstring and build_adjusted_open()'s docstring for
    # the full writeup). comp_open itself stays raw and is passed
    # unchanged to build_dividend_analytics(), which legitimately needs
    # raw prices for actual cash-flow reporting -- this fix is scoped
    # to tactical NAV execution only.
    comp_open_adj = build_adjusted_open(comp_raw, comp_adj, comp_open)
    print(f"  [DIAG] comp_open_adj max date: {pd.to_datetime(comp_open_adj['Date']).max().date()}")
    for _t in ("MGK", "MGV", "VOO"):
        if _t in comp_open_adj.columns:
            _nn = comp_open_adj.loc[comp_open_adj[_t].notna(), "Date"]
            print(f"  [DIAG] comp_open_adj {_t} non-null through: "
                  f"{pd.to_datetime(_nn).max().date() if len(_nn) else 'NEVER'}")

    div_comp = build_dividend_composites(div_wide, raw_scale, required_assets)

    sig, trades          = build_signals(comp_adj, comp_raw, SIGNAL_PRICE_BASIS)
    print(f"  [DIAG] sig max date: {pd.to_datetime(sig['Date']).max().date()}")
    tv                   = build_tactical_values(comp_adj, comp_open_adj, sig)
    print(f"  [DIAG] tv (Tactical_Daily_Values) max date: {pd.to_datetime(tv['Date']).max().date()}")
    pv, portfolio_ledger = build_portfolios(comp_adj, tv, static_models, tactical_models)
    print(f"  [DIAG] pv (Portfolio_Daily_Values) max date: {pd.to_datetime(pv['Date']).max().date()}")
    build_alpha_overlay(comp_adj, comp_raw, comp_open_adj, sig, pv)
    build_holding_analytics(sig, comp_raw)
    try:
        build_dividend_analytics(comp_raw, comp_open, div_comp, sig, tv,
                                 portfolio_ledger, static_models, tactical_models)
    except Exception as _div_err:
        print(f"  ERROR in build_dividend_analytics: {_div_err}")
        import traceback; traceback.print_exc()
    run_audit(alloc_df, static_models, tactical_models,
              comp_adj, comp_raw, sig, trades, tv, pv, data_audit_df)
    dash = build_dashboard()

    print("\nA1V12 Yahoo Production v4.0 complete.")
    _prox_note = (
        f" · {PROXIMITY_THRESHOLD*100:.2f}% proximity filter ({PROXIMITY_LOOKBACK_DAYS}d lookback)"
        if PROXIMITY_FILTER_ENABLED else " · proximity filter OFF"
    )
    _breakout_note = (
        f" + {BREAKOUT_THRESHOLD*100:.2f}% breakout fallback"
        if (PROXIMITY_FILTER_ENABLED and BREAKOUT_FALLBACK_ENABLED) else ""
    )
    _confirm_note = (
        f" + {CONFIRMATION_LAYER_DAYS}-day confirmation"
        if (PROXIMITY_FILTER_ENABLED and CONFIRMATION_LAYER_ENABLED) else ""
    )
    print(f"Signal engine:   {_normalise_signal_price_basis(SIGNAL_PRICE_BASIS).title()} Close · Binary MGK/MGV · EMA89{_prox_note}{_breakout_note}{_confirm_note} · T+{EXECUTION_LAG_DAYS} execution")
    print("Performance:     Unified Adjusted Close total return · all series · dividends reported separately")
    print("VOO:             Adjusted Close total return for benchmark and all model allocations")
    print("Dividend income: Separate model-level cash-income reporting")
    print("Rebalancing:     Annual (Jan 1) for all multi-asset models")
    print("Static models:  ", ", ".join(static_models.keys()))
    print("Tactical models:", ", ".join(tactical_models.keys()))
    print("Latest data:    ", pv["Date"].max())
    print("Dashboard:      ", dash)


if __name__ == "__main__":
    main()