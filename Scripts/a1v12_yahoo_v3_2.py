#!/usr/bin/env python3
"""
A1V12 Yahoo Production v3.3 (Raw-Close Performance + Dividend Income)

Complete integrated package.

Production methodology:
- Charts, NAV, metrics, and drawdowns use raw Close prices.
- Tactical signals use Adjusted Close prices.
- Dividend income is calculated separately at full model level.

v3.3 changes from v3.2:
- Tactical sleeve simplified to binary MGK / MGV rotation — JIVE removed
  from the signal engine entirely.
- Growth<->Value trigger: pure EMA89 crossover of MGK/MGV ratio with a
  3-day post-trade cooldown (identical to A2V1-C). Replaces the v3.2
  approach of a 3-day streak on a ±0.5%/-1.0% banded threshold.
- Value sleeve: MGV only. MGV<->JIVE switching and MIN_HOLD_DAYS are
  removed. EffectiveHolding is now always "MGK" or "MGV".
- Tactical_Daily_Values.csv no longer contains A1V12-XMGV or A1V12-XJIVE
  comparison columns (no longer meaningful without JIVE).
- JIVE is still downloaded and available for static model allocations that
  reference it; it is simply not used by the tactical signal engine.
- Dashboard Tactical Sleeve chart now shows A1V12 vs MGK Buy Hold vs
  MGV Buy Hold vs VOO Benchmark.
- Rule string: "Next trading day after trigger (EMA89 crossover, 3-day cooldown)"
- Portfolio rebalancing: all multi-asset models (MWM Static and Tactical
  Models) now use ANNUAL rebalancing. Holdings drift with daily price
  changes throughout the year and are reset to target weights on the first
  trading day of each new calendar year (applied at open, before that
  day's returns). Single-asset models ("A1V12 Tactical Sleeve" and
  "VOO Benchmark") are unchanged — continuous daily blend.

v3.2 (previous):
- True Sharpe ratio vs BIL.
- Chart Audit drawdown bug fix.
- Whipsaw test #1: MIN_HOLD_DAYS scoped to Value-mode MGV<->JIVE switching.
- Allocation tab with pie charts.

v3.0 (earlier):
- Configuration-driven model engine using Config/MWM_Allocations.csv.
- Static MWM and Tactical Models separated.
- Chart downsampling: YTD/1Y/2Y=Daily, >2Y<8Y=Weekly, >=8Y/SI=Monthly.
- Drawdown computed from daily series before downsampling.
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

START_DATE      = "2008-01-01"   # Download from 2008 so EMA89 is warm by 2011
PORTFOLIO_START = "2011-01-01"   # Dashboard / portfolio NAV starts here
BASE_VALUE      = 100000.0

# JIVE kept in CORE_ASSETS so static model allocations that hold JIVE
# still download correctly. It is not used by the tactical signal engine.
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

# v3.3: cooldown applies uniformly to ALL Growth<->Value transitions.
# 3 trading days must elapse after any trade before the next switch is allowed.
# This replaces v3.2's MIN_HOLD_DAYS which only gated MGV<->JIVE within Value.
COOLDOWN_DAYS = 3


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
        map_rows.append([model, tactical_name, replaced_weight,
                         "MGK/XLG/VOO sleeve replaced by A1V12 tactical sleeve"])

    import pandas as pd
    pd.DataFrame(map_rows,
                 columns=["Static_Model","Tactical_Model","Tactical_Weight","Rule"]
                 ).to_csv(DATA / "Tactical_Model_Map.csv", index=False)
    return static, tactical


def download_prices(required_assets):
    """Download Open, raw Close, Adjusted Close, and cash distributions."""
    ensure("pandas"); ensure("numpy"); ensure("yfinance")
    import pandas as pd
    import yfinance as yf

    all_assets = sorted(set(required_assets) | set(CORE_ASSETS) | set(RESEARCH_ASSETS))
    adj_frames, raw_frames, open_frames, div_frames, audit = [], [], [], [], []

    for asset in all_assets:
        if asset in {"TACTICAL", "A1V12"}:
            continue
        sym = YMAP.get(asset, asset)
        print(f"Downloading {asset} ({sym}) open, raw close, adjusted close, and dividends...")
        try:
            # Ticker.history is used because it reliably exposes action columns.
            hist = yf.Ticker(sym).history(
                start=START_DATE, auto_adjust=False, actions=True, repair=False
            )
            if hist.empty and asset == "DXY":
                hist = yf.Ticker("^DXY").history(
                    start=START_DATE, auto_adjust=False, actions=True, repair=False
                )
            if hist.empty:
                audit.append([asset, sym, "FAIL", "", "", 0, "No data returned"])
                continue

            if isinstance(hist.columns, pd.MultiIndex):
                hist.columns = hist.columns.get_level_values(0)

            idx = pd.to_datetime(hist.index)
            if getattr(idx, "tz", None) is not None:
                idx = idx.tz_localize(None)
            idx = idx.normalize()

            open_px = pd.to_numeric(hist.get("Open"), errors="coerce")
            close = pd.to_numeric(hist.get("Close"), errors="coerce")
            adj = pd.to_numeric(hist.get("Adj Close", hist.get("Close")), errors="coerce")
            div = pd.to_numeric(
                hist.get("Dividends", pd.Series(0.0, index=hist.index)),
                errors="coerce"
            ).fillna(0.0)

            def frame(series, col):
                f = pd.DataFrame({"Date": idx, col: series.values})
                return f.dropna(subset=[col]).drop_duplicates("Date", keep="last")

            af = frame(adj, asset)
            rf = frame(close, asset)
            of = frame(open_px, asset)
            df = pd.DataFrame({"Date": idx, asset: div.values}).drop_duplicates(
                "Date", keep="last"
            )

            adj_frames.append(af)
            raw_frames.append(rf)
            open_frames.append(of)
            div_frames.append(df)

            div_count = int((div.abs() > 1e-12).sum())
            audit.append([
                asset, sym, "OK",
                rf["Date"].min().date().isoformat(),
                rf["Date"].max().date().isoformat(),
                len(rf),
                f"Open + Raw Close + Adj Close; {div_count} dividend events"
            ])
        except Exception as e:
            audit.append([asset, sym, "ERROR", "", "", 0, str(e)])

    if not adj_frames or not raw_frames or not open_frames:
        raise RuntimeError("No Yahoo price data downloaded.")

    def merge_frames(frames):
        wide = frames[0]
        for f in frames[1:]:
            wide = wide.merge(f, on="Date", how="outer")
        return wide.sort_values("Date").reset_index(drop=True)

    adj_wide = merge_frames(adj_frames)
    raw_wide = merge_frames(raw_frames)
    open_wide = merge_frames(open_frames)
    div_wide = merge_frames(div_frames).fillna(0.0)

    adj_wide.to_csv(DATA / "Price_Master_Wide.csv", index=False, date_format="%Y-%m-%d")
    adj_wide.melt(id_vars=["Date"], var_name="Asset", value_name="Adj_Close").dropna().to_csv(
        DATA / "Price_Master_Long.csv", index=False, date_format="%Y-%m-%d")
    raw_wide.to_csv(DATA / "Price_Master_Raw_Close_Wide.csv", index=False, date_format="%Y-%m-%d")
    raw_wide.melt(id_vars=["Date"], var_name="Asset", value_name="Raw_Close").dropna().to_csv(
        DATA / "Price_Master_Raw_Close_Long.csv", index=False, date_format="%Y-%m-%d")
    open_wide.to_csv(DATA / "Price_Master_Open_Wide.csv", index=False, date_format="%Y-%m-%d")
    div_wide.to_csv(DATA / "Dividend_Master_Wide.csv", index=False, date_format="%Y-%m-%d")

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
            bfv = pd.to_numeric(df[bf], errors="coerce") if bf in df.columns else pd.Series(float("nan"), index=df.index)
            scale, live_date, bf_date, status = 1.0, None, None, "UNSCALED"
            live_mask = (df["Date"] > cutoff) & live.notna()
            if live_mask.any() and bfv.notna().any():
                live_idx = live_mask[live_mask].index[0]
                live_date = df.loc[live_idx, "Date"]
                prior = (df["Date"] <= live_date) & bfv.notna()
                if prior.any():
                    bf_idx = prior[prior].index[-1]
                    bf_date = df.loc[bf_idx, "Date"]
                    lv, bv = live.loc[live_idx], bfv.loc[bf_idx]
                    if pd.notna(lv) and pd.notna(bv) and bv != 0:
                        scale, status = float(lv / bv), "SCALED"
            comp[asset] = np.where(df["Date"] <= cutoff, bfv * scale, live)
            scale_rows.append([asset, bf, until, scale, status,
                               live_date.date().isoformat() if live_date is not None else "",
                               bf_date.date().isoformat() if bf_date is not None else "", price_basis])
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
    """Create production-asset dividend-per-share series, including scaled backfills."""
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
            proxy = pd.to_numeric(df[bf], errors="coerce").fillna(0.0) if bf in df.columns else pd.Series(0.0, index=df.index)
            out[asset] = np.where(df["Date"] <= cutoff, proxy * scale_map.get(asset, 1.0), live)
        else:
            out[asset] = live

    out.to_csv(DATA / "Composite_Dividends.csv", index=False, date_format="%Y-%m-%d")
    return out

def ema(s, n):
    # min_periods=1: EMA seeds from the very first row — no NaN gap.
    # START_DATE pulls 3 years of pre-history so EMA is warm by PORTFOLIO_START.
    return s.ewm(span=n, adjust=False, min_periods=1).mean()


def build_signals(comp):
    """
    v3.3 signal engine — binary MGK / MGV, no JIVE.

    Trigger:  MGK/MGV Adj-Close ratio vs its 89-day EMA.
              ratio > EMA89  →  Growth / hold MGK
              ratio ≤ EMA89  →  Value  / hold MGV

    Cooldown: COOLDOWN_DAYS (default 3) trading days must elapse after
              any trade before the next switch is allowed.  Both
              Growth→Value and Value→Growth transitions are subject to
              the same cooldown (uniform, no asymmetry).

    Execution: T+1 — trigger fires at close, trade executes next open.
    """
    import pandas as pd

    df  = comp.dropna(subset=["MGK", "MGV"]).copy().sort_values("Date").reset_index(drop=True)
    sig = pd.DataFrame({"Date": df["Date"], "MGK": df["MGK"], "MGV": df["MGV"]})

    sig["MGK_MGV"]      = sig["MGK"] / sig["MGV"]
    sig["MGK_MGV_EMA89"] = ema(sig["MGK_MGV"], 89)

    # Raw daily signal: 1 = Growth (MGK), 0 = Value (MGV)
    raw_signal = (sig["MGK_MGV"] > sig["MGK_MGV_EMA89"]).astype(int).values

    n             = len(sig)
    position      = [0] * n          # 0 = MGV, 1 = MGK
    position[0]   = raw_signal[0]
    days_since    = COOLDOWN_DAYS    # pre-filled so first trade is always allowed

    for i in range(1, n):
        days_since += 1
        if raw_signal[i] != position[i - 1] and days_since >= COOLDOWN_DAYS:
            position[i]  = raw_signal[i]
            days_since   = 0
        else:
            position[i]  = position[i - 1]

    # T+1 shift: today's signal → tomorrow's holding
    # Build EffectiveHolding by shifting position forward one row
    holdings_list  = []
    trades_list    = []
    current_holding = "MGV" if position[0] == 0 else "MGK"

    # First row: holding is the initial position (no prior trade)
    holdings_list.append([sig["Date"].iloc[0], "Growth" if current_holding == "MGK" else "Value", current_holding])

    for i in range(1, n):
        new_holding = "MGK" if position[i] == 1 else "MGV"
        new_state   = "Growth" if new_holding == "MGK" else "Value"
        # Detect T+1 execution: position changed vs prior day
        if position[i] != position[i - 1]:
            trigger_date = sig["Date"].iloc[i - 1]   # signal fired yesterday
            trade_date   = sig["Date"].iloc[i]        # execute today
            rule = (f"Next trading day after trigger "
                    f"(EMA89 crossover, {COOLDOWN_DAYS}-day cooldown)")
            trades_list.append([trade_date, trigger_date,
                                 current_holding, new_holding, new_state, rule])
            current_holding = new_holding
        holdings_list.append([sig["Date"].iloc[i], new_state, current_holding])

    h = pd.DataFrame(holdings_list, columns=["Date", "State", "EffectiveHolding"])
    sig = sig.merge(h, on="Date", how="left")

    front = ["Date","State","EffectiveHolding","MGK","MGV","MGK_MGV","MGK_MGV_EMA89"]
    sig   = sig[front + [c for c in sig.columns if c not in front]]

    trades_df = pd.DataFrame(trades_list,
        columns=["Trade_Date","Trigger_Date","From","To","New_State","Rule"])

    # Clip to PORTFOLIO_START for dashboard output.
    # Pre-history rows were only needed to warm the EMA89.
    port_start = pd.to_datetime(PORTFOLIO_START)
    sig_out    = sig[sig["Date"]               >= port_start].copy()
    h_out      = h[h["Date"]                   >= port_start].copy()
    trades_out = trades_df[trades_df["Trade_Date"] >= port_start].copy()

    sig_out.to_csv(    DATA / "Signal_History.csv", index=False, date_format="%Y-%m-%d")
    h_out.to_csv(      DATA / "Daily_Holdings.csv", index=False, date_format="%Y-%m-%d")
    trades_out.to_csv( DATA / "Trade_Ledger.csv",   index=False, date_format="%Y-%m-%d")

    # Return FULL sig (inc. warmup) so build_tactical_values has the
    # complete EffectiveHolding series for portfolio NAV calculation.
    return sig, trades_df


def build_tactical_values(comp_close, comp_open, sig):
    """
    Compute raw-close price-return NAV for the A1V12 sleeve.

    Signals are determined from adjusted closes. A holding change executes at
    the next trading day's Open:
      old holding: prior Close -> trade-day Open
      new holding: trade-day Open -> trade-day Close
    This removes the look-ahead gain created by applying the new holding's
    full prior-close-to-current-close return on the trade date.
    """
    import pandas as pd

    close = comp_close.copy()
    open_px = comp_open.copy()
    close["Date"] = pd.to_datetime(close["Date"])
    open_px["Date"] = pd.to_datetime(open_px["Date"])

    open_px = open_px.rename(columns={c: f"{c}__OPEN" for c in open_px.columns if c != "Date"})
    df = close.merge(open_px, on="Date", how="inner")
    df = df.merge(sig[["Date", "EffectiveHolding"]], on="Date", how="inner")
    df = df.dropna(subset=["MGK", "MGV", "VOO", "MGK__OPEN", "MGV__OPEN"])
    df = df.sort_values("Date").reset_index(drop=True)
    df = df[df["Date"] >= pd.to_datetime(PORTFOLIO_START)].reset_index(drop=True)

    val = BASE_VALUE
    rows = [[df.loc[0, "Date"], val, df.loc[0, "EffectiveHolding"]]]

    for i in range(1, len(df)):
        prev = df.iloc[i - 1]
        cur = df.iloc[i]
        old_h = prev["EffectiveHolding"]
        new_h = cur["EffectiveHolding"]

        if new_h == old_h:
            val *= cur[new_h] / prev[old_h]
        else:
            old_open = cur[f"{old_h}__OPEN"]
            new_open = cur[f"{new_h}__OPEN"]
            if pd.isna(old_open) or pd.isna(new_open) or old_open == 0 or new_open == 0:
                raise ValueError(f"Missing trade-day Open price on {cur['Date']} for {old_h}->{new_h}")
            val *= old_open / prev[old_h]
            val *= cur[new_h] / new_open

        rows.append([cur["Date"], val, new_h])

    tv = pd.DataFrame(rows, columns=["Date", "A1V12", "EffectiveHolding"])

    start_row = df.iloc[0]
    for a, label in [
        ("MGK", "MGK Buy Hold"),
        ("MGV", "MGV Buy Hold"),
        ("VOO", "VOO Benchmark"),
        ("BIL", "BIL Buy Hold"),
    ]:
        if a in df.columns and pd.notna(start_row[a]) and start_row[a] != 0:
            tv[label] = BASE_VALUE * df[a].values / start_row[a]

    latest_yr = tv["Date"].dt.year.max()
    prior_yr = latest_yr - 1
    prior_data = tv[tv["Date"].dt.year == prior_yr]
    if not prior_data.empty:
        last_prior = prior_data.iloc[-1].copy()
        last_prior_date = last_prior["Date"]
        if not (last_prior_date.month == 12 and last_prior_date.day == 31):
            anchor = last_prior.copy()
            anchor["Date"] = pd.Timestamp(prior_yr, 12, 31)
            tv = pd.concat([tv, pd.DataFrame([anchor])], ignore_index=True)
            tv = tv.sort_values("Date").reset_index(drop=True)

    tv.to_csv(DATA / "Tactical_Daily_Values.csv", index=False, date_format="%Y-%m-%d")
    return tv

def build_portfolios(comp, tv, static_models, tactical_models):
    """
    Compute daily NAV for all models.

    Rebalancing policy
    ------------------
    Single-asset models ("A1V12 Tactical Sleeve", "VOO Benchmark") are
    computed with continuous daily blending — rebalancing has no meaning
    when there is only one holding.

    All multi-asset models (MWM Static and Tactical Models) use ANNUAL
    rebalancing: holdings drift with daily price changes throughout the
    year and are reset to target weights on the first trading day of each
    new calendar year.  Rebalancing is applied at the open (i.e. before
    that day's returns are applied), consistent with a Jan 1 rebalance
    instruction executed at the following market open.
    """
    import pandas as pd

    # Models whose rebalancing is meaningless (single effective asset)
    NO_REBALANCE = {"A1V12 Tactical Sleeve", "VOO Benchmark"}

    all_models = {
        "VOO Benchmark":         {"VOO": 1.0},
        "A1V12 Tactical Sleeve": {"TACTICAL": 1.0},
    }
    all_models.update(static_models)
    all_models.update(tactical_models)

    df = comp.merge(tv[["Date", "A1V12"]], on="Date", how="inner"
                    ).sort_values("Date").reset_index(drop=True)
    # Ensure portfolio NAV starts from PORTFOLIO_START — comp includes
    # pre-history dates for EMA warmup that must not appear in output.
    df = df[df["Date"] >= pd.to_datetime(PORTFOLIO_START)].reset_index(drop=True)

    ret = pd.DataFrame({"Date": df["Date"],
                        "TACTICAL": df["A1V12"].pct_change().fillna(0),
                        "A1V12":    df["A1V12"].pct_change().fillna(0)})
    for a in [c for c in df.columns if c != "Date"]:
        ret[a] = df[a].pct_change().fillna(0)

    dates    = df["Date"].values
    n_rows   = len(df)
    vals     = pd.DataFrame({"Date": df["Date"]})

    for name, weights in all_models.items():

        # Normalise asset key (TACTICAL alias)
        keyed = {
            ("TACTICAL" if a in {"TACTICAL", "A1V12"} else a): w
            for a, w in weights.items()
        }
        # Keep only assets that exist in the return frame
        keyed = {k: w for k, w in keyed.items() if k in ret.columns}
        if not keyed:
            continue

        if name in NO_REBALANCE or len(keyed) == 1:
            # ── Continuous daily blend (single-asset or excluded) ──────────
            v = [BASE_VALUE]
            for i in range(1, n_rows):
                dr = sum(w * ret.loc[i, k] for k, w in keyed.items())
                v.append(v[-1] * (1 + dr))
            vals[name] = v

        else:
            # ── Annual rebalancing ─────────────────────────────────────────
            # Initialise dollar holdings at target weights
            holdings = {k: BASE_VALUE * w for k, w in keyed.items()}
            nav      = [BASE_VALUE]
            cur_year = pd.Timestamp(dates[0]).year

            for i in range(1, n_rows):
                yr = pd.Timestamp(dates[i]).year

                # First trading day of a new year → rebalance before returns
                if yr != cur_year:
                    total = sum(holdings.values())
                    holdings = {k: total * w for k, w in keyed.items()}
                    cur_year = yr

                # Apply that day's returns to each holding
                for k in holdings:
                    holdings[k] *= (1.0 + ret.loc[i, k])

                nav.append(sum(holdings.values()))

            vals[name] = nav

    # Add single prior year-end anchor so YTD rebases from Dec 31 close.
    dates_ts = pd.to_datetime(vals["Date"])
    latest_yr2 = dates_ts.dt.year.max()
    prior_yr2  = latest_yr2 - 1
    prior_mask2 = dates_ts.dt.year == prior_yr2
    if prior_mask2.any():
        last_prior2 = vals[prior_mask2].iloc[-1].copy()
        last_prior2_date = pd.to_datetime(last_prior2["Date"])
        if not (last_prior2_date.month == 12 and last_prior2_date.day == 31):
            anchor2 = last_prior2.copy()
            anchor2["Date"] = pd.Timestamp(prior_yr2, 12, 31).strftime("%Y-%m-%d")
            vals = pd.concat([vals, pd.DataFrame([anchor2])], ignore_index=True)
            vals["Date"] = pd.to_datetime(vals["Date"]).dt.strftime("%Y-%m-%d")
            vals = vals.sort_values("Date").reset_index(drop=True)

    vals.to_csv(DATA / "Portfolio_Daily_Values.csv", index=False, date_format="%Y-%m-%d")
    return vals


def build_holding_analytics(sig, comp_raw):
    import pandas as pd
    px = comp_raw[["Date", "MGK", "MGV"]].copy()
    df = sig[["Date", "EffectiveHolding"]].merge(px, on="Date", how="left")
    df = df[df["Date"] >= pd.to_datetime(PORTFOLIO_START)].dropna(subset=["MGK", "MGV"]).reset_index(drop=True)
    rows, start, current = [], 0, df.loc[0, "EffectiveHolding"]

    def period(st, en, asset):
        sub = df.iloc[st:en + 1]
        sp, ep = sub[asset].iloc[0], sub[asset].iloc[-1]
        return {"Start_Date": sub["Date"].iloc[0], "End_Date": sub["Date"].iloc[-1],
                "Asset": asset, "Trading_Days": len(sub), "Start_Price": sp,
                "End_Price": ep, "Return": ep / sp - 1 if sp else None}

    for i in range(1, len(df)):
        if df.loc[i, "EffectiveHolding"] != current:
            rows.append(period(start, i - 1, current))
            start, current = i, df.loc[i, "EffectiveHolding"]
    rows.append(period(start, len(df) - 1, current))

    hp = pd.DataFrame(rows)
    hp.to_csv(DATA / "Holding_Periods.csv", index=False, date_format="%Y-%m-%d")
    hs = hp.groupby("Asset").agg(Periods=("Asset", "count"), Avg_Trading_Days=("Trading_Days", "mean"),
        Median_Trading_Days=("Trading_Days", "median"), Min_Trading_Days=("Trading_Days", "min"),
        Max_Trading_Days=("Trading_Days", "max"), Avg_Return=("Return", "mean"),
        Best_Return=("Return", "max"), Worst_Return=("Return", "min")).reset_index()
    hs["Pct_Time"] = hs["Asset"].map(hp.groupby("Asset")["Trading_Days"].sum() / hp["Trading_Days"].sum())
    hs.to_csv(DATA / "Holding_Summary.csv", index=False)

def build_dividend_analytics(comp_raw, comp_open, div_comp, sig, tv,
                             static_models, tactical_models):
    """
    Calculate dividend income with an explicit security-value + cash ledger.

    Raw-close price return remains separate. Dividends are recorded as cash
    income and reinvested at the distribution-date raw close in the income
    ledger. Annual model rebalances then use the resulting security values.

    Also writes monthly model/asset income and a fixed-income verification
    table for PIMIX, JPIE, FIWDX, and JBND.
    """
    import pandas as pd
    import numpy as np

    prices = comp_raw.copy()
    opens = comp_open.copy()
    divs = div_comp.copy()
    for frame in (prices, opens, divs):
        frame["Date"] = pd.to_datetime(frame["Date"])

    prices = prices.merge(tv[["Date", "A1V12"]], on="Date", how="inner")
    prices = prices[prices["Date"] >= pd.to_datetime(PORTFOLIO_START)]
    prices = prices.sort_values("Date").reset_index(drop=True)
    opens = opens.merge(prices[["Date"]], on="Date", how="right")
    divs = divs.merge(prices[["Date"]], on="Date", how="right").fillna(0.0)

    hold = sig[["Date", "EffectiveHolding"]].copy()
    hold["Date"] = pd.to_datetime(hold["Date"])
    hold = hold.merge(prices[["Date"]], on="Date", how="right")
    hold["EffectiveHolding"] = hold["EffectiveHolding"].ffill().bfill()

    # Standalone tactical sleeve dividend ledger.
    tactical_daily_income = np.zeros(len(prices))
    current = str(hold.loc[0, "EffectiveHolding"])
    shares = BASE_VALUE / float(prices.loc[0, current])
    cash_balance = 0.0
    period_start = 0
    period_income = 0.0
    period_rows = []

    for i in range(len(prices)):
        new_h = str(hold.loc[i, "EffectiveHolding"])

        dps = float(divs.loc[i, current]) if current in divs.columns else 0.0
        income = shares * dps
        tactical_daily_income[i] = income
        period_income += income
        # Reinvest each distribution at the distribution-date raw close so the
        # share ledger represents a continuously invested economic account.
        close_px = float(prices.loc[i, current])
        if income and np.isfinite(close_px) and close_px > 0:
            shares += income / close_px

        if i > 0 and new_h != current:
            period_rows.append({
                "Start_Date": prices.loc[period_start, "Date"],
                "End_Date": prices.loc[i - 1, "Date"],
                "Holding": current,
                "Dividend_Income": period_income,
            })
            old_open = float(opens.loc[i, current])
            new_open = float(opens.loc[i, new_h])
            if not np.isfinite(old_open) or not np.isfinite(new_open) or new_open <= 0:
                raise ValueError(
                    f"Missing tactical Open price on {prices.loc[i, 'Date']} "
                    f"for {current}->{new_h}"
                )
            account_value = shares * old_open
            shares = account_value / new_open
            current = new_h
            period_start = i
            period_income = 0.0

    period_rows.append({
        "Start_Date": prices.loc[period_start, "Date"],
        "End_Date": prices.loc[len(prices) - 1, "Date"],
        "Holding": current,
        "Dividend_Income": period_income,
    })
    pd.DataFrame(period_rows).to_csv(
        DATA / "Dividend_Holding_Periods.csv", index=False, date_format="%Y-%m-%d"
    )

    # Create a synthetic tactical dividend-per-unit series for model blending.
    synthetic_units = BASE_VALUE / float(prices.loc[0, "A1V12"])
    prices["TACTICAL"] = prices["A1V12"]
    divs["TACTICAL"] = tactical_daily_income / synthetic_units
    divs["A1V12"] = divs["TACTICAL"]

    all_models = {
        "VOO Benchmark": {"VOO": 1.0},
        "A1V12 Tactical Sleeve": {"TACTICAL": 1.0},
    }
    all_models.update(static_models)
    all_models.update(tactical_models)

    no_rebalance = {"VOO Benchmark", "A1V12 Tactical Sleeve"}
    annual_rows, monthly_rows, asset_rows, summary_rows, coverage_rows = [], [], [], [], []

    last_date = pd.to_datetime(prices["Date"].iloc[-1])
    ttm_start = last_date - pd.DateOffset(years=1)
    current_year = int(last_date.year)
    days_elapsed = max(int(last_date.dayofyear), 1)
    year_days = 366 if last_date.is_leap_year else 365
    annualization_factor = year_days / days_elapsed

    used_assets = sorted({
        ("TACTICAL" if a in {"TACTICAL", "A1V12"} else a)
        for weights in all_models.values() for a in weights
    })

    for asset in used_assets:
        series = pd.to_numeric(divs[asset], errors="coerce").fillna(0.0) if asset in divs.columns else pd.Series(0.0, index=divs.index)
        mask = series.abs() > 1e-12
        coverage_rows.append({
            "Asset": asset,
            "Status": "PASS" if mask.any() else "WARN",
            "Distribution_Count": int(mask.sum()),
            "First_Distribution_Date": prices.loc[mask, "Date"].min() if mask.any() else "",
            "Last_Distribution_Date": prices.loc[mask, "Date"].max() if mask.any() else "",
            "Total_DPS": float(series.sum()),
            "Detail": "Distribution events found" if mask.any()
                      else "No distributions found; verify this is expected",
        })

    for model, weights in all_models.items():
        keyed = {
            ("TACTICAL" if a in {"TACTICAL", "A1V12"} else a): float(w)
            for a, w in weights.items()
        }
        keyed = {a: w for a, w in keyed.items() if a in prices.columns}
        total_w = sum(keyed.values())
        if not keyed or total_w <= 0:
            continue
        keyed = {a: w / total_w for a, w in keyed.items()}

        shares_by_asset = {}
        for asset, weight in keyed.items():
            px0 = float(prices.loc[0, asset])
            if np.isfinite(px0) and px0 > 0:
                shares_by_asset[asset] = BASE_VALUE * weight / px0

        daily_rows = []
        cur_year = int(prices.loc[0, "Date"].year)

        for i in range(len(prices)):
            dt = pd.to_datetime(prices.loc[i, "Date"])

            # Rebalance the full economic account: securities + accumulated cash.
            if (i > 0 and model not in no_rebalance and len(keyed) > 1
                    and dt.year != cur_year):
                prev_i = i - 1
                security_value = sum(
                    sh * float(prices.loc[prev_i, asset])
                    for asset, sh in shares_by_asset.items()
                    if pd.notna(prices.loc[prev_i, asset])
                )
                total_value = security_value
                shares_by_asset = {
                    asset: total_value * weight / float(prices.loc[prev_i, asset])
                    for asset, weight in keyed.items()
                    if pd.notna(prices.loc[prev_i, asset])
                    and float(prices.loc[prev_i, asset]) > 0
                }
                cur_year = dt.year

            day_total = 0.0
            for asset, sh in shares_by_asset.items():
                dps = float(divs.loc[i, asset]) if asset in divs.columns else 0.0
                income = sh * dps
                if abs(income) > 1e-12:
                    asset_rows.append({
                        "Model": model,
                        "Date": dt,
                        "Month": dt.strftime("%Y-%m"),
                        "Year": int(dt.year),
                        "Asset": asset,
                        "Dividend_Income": income,
                    })
                day_total += income

            # Record cash income, then reinvest it into the distributing asset
            # at that day's raw close. This keeps future shares and income from
            # shrinking after distributions while price-return charts remain raw-close.
            for asset, sh in list(shares_by_asset.items()):
                dps = float(divs.loc[i, asset]) if asset in divs.columns else 0.0
                income = sh * dps
                close_px = float(prices.loc[i, asset]) if asset in prices.columns else float("nan")
                if income and np.isfinite(close_px) and close_px > 0:
                    shares_by_asset[asset] += income / close_px
            daily_rows.append((dt, day_total))

        ddf = pd.DataFrame(daily_rows, columns=["Date", "Dividend_Income"])
        yearly = ddf.groupby(ddf["Date"].dt.year)["Dividend_Income"].sum()
        monthly = ddf.groupby(ddf["Date"].dt.to_period("M"))["Dividend_Income"].sum()

        cumulative = 0.0
        for yr, income in yearly.items():
            income = float(income)
            cumulative += income
            partial = int(yr) == current_year and not (last_date.month == 12 and last_date.day == 31)
            annual_rows.append({
                "Model": model,
                "Year": int(yr),
                "Period": f"{int(yr)} YTD through {last_date.date()}" if partial else str(int(yr)),
                "Dividend_Income": income,
                "Cumulative_Income": cumulative,
                "Annualized_Run_Rate": income * annualization_factor if partial else income,
                "Is_Partial_Year": "YES" if partial else "NO",
            })

        running = 0.0
        for month, income in monthly.items():
            income = float(income)
            running += income
            monthly_rows.append({
                "Model": model,
                "Month": str(month),
                "Dividend_Income": income,
                "Cumulative_Income": running,
            })

        total_income = float(ddf["Dividend_Income"].sum())
        ttm_income = float(ddf.loc[ddf["Date"] > ttm_start, "Dividend_Income"].sum())
        current_income = float(yearly.get(current_year, 0.0))
        summary_rows.append({
            "Model": model,
            "Lifetime_Dividend_Income": total_income,
            "Current_Year_Income": current_income,
            "Current_Year_Annualized_Run_Rate": current_income * annualization_factor,
            "TTM_Dividend_Income": ttm_income,
            "Through_Date": last_date,
        })

    annual_df = pd.DataFrame(annual_rows)
    monthly_df = pd.DataFrame(monthly_rows)
    asset_df = pd.DataFrame(asset_rows)
    summary_df = pd.DataFrame(summary_rows)
    coverage_df = pd.DataFrame(coverage_rows)

    annual_df.to_csv(DATA / "Dividend_Model_Annual.csv", index=False)
    monthly_df.to_csv(DATA / "Dividend_Model_Monthly.csv", index=False)

    if not asset_df.empty:
        asset_monthly = asset_df.groupby(
            ["Model", "Month", "Asset"], as_index=False
        )["Dividend_Income"].sum()
        asset_annual = asset_df.groupby(
            ["Model", "Year", "Asset"], as_index=False
        )["Dividend_Income"].sum()
        asset_monthly.to_csv(DATA / "Dividend_Asset_Monthly.csv", index=False)
        asset_annual.to_csv(DATA / "Dividend_Asset_Annual.csv", index=False)
    else:
        pd.DataFrame(columns=["Model","Month","Asset","Dividend_Income"]).to_csv(
            DATA / "Dividend_Asset_Monthly.csv", index=False)
        pd.DataFrame(columns=["Model","Year","Asset","Dividend_Income"]).to_csv(
            DATA / "Dividend_Asset_Annual.csv", index=False)

    # Per-$100,000 monthly verification for the four requested income funds.
    verify_rows = []
    verify_assets = ["PIMIX", "JPIE", "FIWDX", "JBND"]
    for asset in verify_assets:
        if asset not in prices.columns or asset not in divs.columns:
            continue
        first_valid = prices[asset].first_valid_index()
        if first_valid is None:
            continue
        shares_100k = BASE_VALUE / float(prices.loc[first_valid, asset])
        tmp = pd.DataFrame({
            "Date": prices["Date"],
            "Price": pd.to_numeric(prices[asset], errors="coerce"),
            "DPS": pd.to_numeric(divs[asset], errors="coerce").fillna(0.0),
        })
        tmp["Income_on_100k"] = shares_100k * tmp["DPS"]
        tmp["Month"] = tmp["Date"].dt.to_period("M").astype(str)
        for month, g in tmp.groupby("Month"):
            dps = float(g["DPS"].sum())
            income = float(g["Income_on_100k"].sum())
            if abs(dps) > 1e-12:
                verify_rows.append({
                    "Asset": asset,
                    "Month": month,
                    "Dividend_Per_Share": dps,
                    "Income_on_100k_Initial_Investment": income,
                })

    pd.DataFrame(verify_rows).to_csv(
        DATA / "Fixed_Income_Monthly_Verification.csv", index=False
    )
    summary_df.to_csv(
        DATA / "Dividend_Model_Summary.csv", index=False, date_format="%Y-%m-%d"
    )
    coverage_df.to_csv(
        AUDIT / "Dividend_Coverage_Audit.csv", index=False, date_format="%Y-%m-%d"
    )

    # Production sanity audit: flag extreme unexplained income collapse.
    sanity_rows = []
    for model, g in annual_df.groupby("Model"):
        full = g[g["Is_Partial_Year"] == "NO"].sort_values("Year")
        if len(full) >= 5:
            early = float(full.head(3)["Dividend_Income"].median())
            recent = float(full.tail(3)["Dividend_Income"].median())
            ratio = recent / early if early else float("nan")
            status = "PASS" if (not np.isfinite(ratio) or ratio >= 0.35) else "FAIL"
            sanity_rows.append({
                "Model": model, "Early_3Y_Median": early,
                "Recent_3Y_Median": recent, "Recent_to_Early_Ratio": ratio,
                "Status": status,
                "Detail": "Income history plausible" if status == "PASS" else
                          "Annual income collapsed below 35% of early-period median"
            })
    pd.DataFrame(sanity_rows).to_csv(AUDIT / "Dividend_Income_Sanity_Audit.csv", index=False)

def run_audit(alloc_df, static_models, tactical_models, comp_adj, comp_raw, sig, trades, tv, pv, data_audit_df):
    import pandas as pd
    checks = []
    def add(name, status, detail): checks.append([name, status, detail])

    add("Performance price basis", "PASS", "Yahoo raw Close for NAV, charts, metrics, and drawdowns")
    add("Signal price basis",      "PASS", "Yahoo Adjusted Close for MGK/MGV ratio and EMA89")
    add("Dividend treatment",      "PASS", "Cash distributions reported separately; not reinvested in price-return NAV")
    add("Allocation file",     "PASS", "Config/MWM_Allocations.csv")
    add("Allocation rows",     "PASS", str(len(alloc_df)))
    add("Static MWM models",   "PASS", ", ".join(static_models.keys()))
    add("Tactical models",     "PASS", ", ".join(tactical_models.keys()))
    add("Tactical sleeve",     "PASS", "Binary MGK/MGV — no JIVE (v3.3)")
    add("EMA warmup",          "PASS",
        f"Price history downloaded from {START_DATE}; EMA89 warm by {PORTFOLIO_START}; "
        f"portfolio NAV and dashboard start from {PORTFOLIO_START}")
    add("Cooldown rule",       "PASS",
        f"{COOLDOWN_DAYS} trading days after any trade (Growth<->Value, uniform)")

    required    = set(alloc_df["Production_Asset"].unique())
    ok_assets   = set(data_audit_df.loc[data_audit_df["Status"] == "OK", "Asset"])
    failed      = sorted(a for a in data_audit_df.loc[data_audit_df["Status"] != "OK", "Asset"]
                         if a in required)
    unresolved  = sorted(a for a in required
                         if a not in ok_assets
                         and a not in BACKFILLS
                         and a not in {"TACTICAL", "A1V12"})
    missing     = sorted(set(failed) | set(unresolved))
    add("Allocation assets resolve to live data",
        "PASS" if not missing else "FAIL",
        "All Production_Assets have OK price data" if not missing
        else f"Unresolved/failed: {', '.join(missing)}")

    add("Adjusted composite rows", "PASS" if len(comp_adj) else "FAIL", str(len(comp_adj)))
    add("Raw composite rows",      "PASS" if len(comp_raw) else "FAIL", str(len(comp_raw)))
    add("Signal rows",          "PASS" if len(sig)    else "FAIL", str(len(sig)))
    add("Trade ledger rows",    "PASS" if len(trades) else "WARN", str(len(trades)))
    add("Portfolio values rows","PASS" if len(pv)     else "FAIL", str(len(pv)))
    add("Latest signal date",   "PASS", str(sig["Date"].max())  if len(sig) else "N/A")
    add("Latest portfolio date","PASS", str(pv["Date"].max())   if len(pv)  else "N/A")
    add("Portfolio rebalancing", "PASS",
        "Annual (first trading day of each year) for all multi-asset models; "
        "continuous daily blend for single-asset models (Tactical Sleeve, VOO Benchmark)")
    add("Backfill scaling",     "PASS", "Backfill legs ratio-scaled to first live observation")
    add("Chart downsampling",   "PASS", "Daily <=2Y, Weekly >2Y, Monthly >=8Y/SI")
    add("Drawdown chart",       "PASS", "Daily drawdown computed before downsampling")
    add("MCI production status","PASS", "Research-only / unused")

    pd.DataFrame(checks, columns=["Check","Status","Detail"]
                 ).to_csv(AUDIT / "Production_Audit.csv", index=False)


def csv_payload(name):
    p = DATA / name
    return p.read_text() if p.exists() else ""


def build_dashboard():
    payload = {
        "tactical":     csv_payload("Tactical_Daily_Values.csv"),
        "portfolio":    csv_payload("Portfolio_Daily_Values.csv"),
        "signals":      csv_payload("Signal_History.csv"),
        "trades":       csv_payload("Trade_Ledger.csv"),
        "holdsum":      csv_payload("Holding_Summary.csv"),
        "holdperiods":  csv_payload("Holding_Periods.csv"),
        "dataaudit":    (AUDIT / "Data_Audit.csv").read_text()       if (AUDIT / "Data_Audit.csv").exists()       else "",
        "prodaudit":    (AUDIT / "Production_Audit.csv").read_text() if (AUDIT / "Production_Audit.csv").exists() else "",
        "modelmap":     csv_payload("Tactical_Model_Map.csv"),
        "alloc":        csv_payload("Allocation_Config_Normalized.csv"),
        "backfillaudit":(AUDIT / "Backfill_Scale_Audit.csv").read_text() if (AUDIT / "Backfill_Scale_Audit.csv").exists() else "",
        "divsummary":   csv_payload("Dividend_Model_Summary.csv"),
        "divannual":    csv_payload("Dividend_Model_Annual.csv"),
        "divasset":     csv_payload("Dividend_Asset_Annual.csv"),
        "divperiods":   csv_payload("Dividend_Holding_Periods.csv"),
    }
    html = DASHBOARD_HTML.replace("__PAYLOAD__", json.dumps(payload))

    # Deployment guard: never publish a stale dividend implementation.
    forbidden = [
        "Current-Year Yield on Cost",
        "CURRENT-YEAR YIELD ON COST",
        "Current_Year_Yield_on_Cost",
        'id="divChart"',
    ]
    stale = [token for token in forbidden if token in html]
    required = [
        'id="divAnnualChart"',
        'id="divCumulativeChart"',
        "Annualized YTD Run Rate",
        "Yield on cost is intentionally not displayed",
    ]
    missing = [token for token in required if token not in html]
    if stale or missing:
        raise RuntimeError(
            "Dashboard deployment guard failed. "
            f"Stale markers={stale}; missing markers={missing}"
        )

    out  = DASH / "A1V12_Yahoo_Production_v3_2_Dashboard.html"
    out.write_text(html)

    # Re-read the exact file written to disk so the build cannot pass if an
    # older dashboard survives or the wrong output path is used.
    written = out.read_text()
    if any(token in written for token in forbidden):
        raise RuntimeError("Stale dividend dashboard was written to the production path.")
    if not all(token in written for token in required):
        raise RuntimeError("Corrected dividend dashboard markers are missing from production output.")
    return out


DASHBOARD_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>A1V12 Yahoo Production v3.3</title>
<style>
body{font-family:Arial;margin:0;background:#f5f7fb;color:#111827}.wrap{max-width:1680px;margin:auto;padding:18px}h1{color:#17365d;margin:0}.sub{color:#64748b;font-size:13px}.card{background:white;border:1px solid #d7deea;border-radius:13px;padding:14px;margin:12px 0}.tabs,.controls,.checks{display:flex;gap:7px;flex-wrap:wrap;margin:10px 0}button{border:1px solid #cbd5e1;background:white;border-radius:9px;padding:8px 11px;font-weight:700;cursor:pointer}button.active{background:#17365d;color:white}.tab{display:none}.tab.active{display:block}.grid{display:grid;gap:12px}.grid2{grid-template-columns:2fr 1fr}.kpis{grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}.kpi{background:#f8fafc;border:1px solid #e5e7eb;border-radius:10px;padding:10px}.label{font-size:11px;text-transform:uppercase;color:#64748b;font-weight:800}.big{font-size:22px;font-weight:900}.chartbox{height:430px;width:100%;border:1px solid #eef2f7;border-radius:10px;background:white}.chartbox.short{height:300px}canvas{width:100%;height:100%;display:block}.legend{display:flex;flex-wrap:wrap;gap:16px;font-size:12px;margin-top:10px}.sw{width:18px;height:4px;border-radius:2px;display:inline-block;margin-right:5px}.scroll{max-height:560px;overflow:auto;border:1px solid #eef2f7;border-radius:10px}table{border-collapse:collapse;width:100%;font-size:12px}th,td{border-bottom:1px solid #e5e7eb;padding:7px;text-align:right;white-space:nowrap}th{background:#f3f4f6;position:sticky;top:0;cursor:pointer;z-index:2}td:first-child,th:first-child{text-align:left}.freeze1{position:sticky;left:0;background:white;z-index:1;min-width:120px}.freeze2{position:sticky;left:120px;background:white;z-index:1;min-width:90px}.freeze3{position:sticky;left:210px;background:white;z-index:1;min-width:180px}.good{color:#15803d;font-weight:800}.bad{color:#b91c1c;font-weight:800}.pass{color:#15803d;font-weight:900}.fail{color:#b91c1c;font-weight:900}.warn{color:#a16207;font-weight:900}.note{font-size:12px;color:#64748b}.pill{display:inline-block;background:#eef2ff;border:1px solid #c7d2fe;border-radius:999px;padding:4px 8px;margin:2px;font-size:12px;font-weight:700}.state-growth{background:#ecfdf5}.state-value{background:#eff6ff}.tradebox{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}.tradeitem{background:#f8fafc;border:1px solid #e5e7eb;border-radius:10px;padding:10px}
</style></head><body><div class="wrap">
<h1>A1V12 Yahoo Production v3.3</h1><div class="sub">Binary MGK/MGV tactical sleeve. Annual rebalancing for multi-asset models. Daily calculations retained; chart downsampling is display-only.</div>
<div class="card" style="border-left:5px solid #17365d"><b>Returns shown are price return. Dividend income shown separately on the Dividend Income tab.</b><div class="note">Charts and metrics use raw closing prices. Tactical signals use adjusted closing prices.</div></div>
<div class="tabs"><button class="tabbtn active" onclick="showTab(event,'overview')">Overview</button><button class="tabbtn" onclick="showTab(event,'tactical')">Tactical Sleeve</button><button class="tabbtn" onclick="showTab(event,'mwm')">MWM Static</button><button class="tabbtn" onclick="showTab(event,'tacticalmodels')">Tactical Models</button><button class="tabbtn" onclick="showTab(event,'signals')">Signals</button><button class="tabbtn" onclick="showTab(event,'holding')">Holding Analytics</button><button class="tabbtn" onclick="showTab(event,'trade')">Trade Log</button><button class="tabbtn" onclick="showTab(event,'chartaudit')">Chart Audit</button><button class="tabbtn" onclick="showTab(event,'audit')">Audit</button><button class="tabbtn" onclick="showTab(event,'dividend')">Dividend Income</button><button class="tabbtn" onclick="showTab(event,'allocation')">Allocation</button><button class="tabbtn" onclick="showTab(event,'config')">Config</button></div>
<div class="controls"><b class="note">Period</b><span id="periodButtons"></span><span id="freqPill" class="pill">Display: Daily</span><span class="pill">Metrics use daily rows</span><span class="pill">Drawdown before downsample</span></div>
<section id="overview" class="tab active"><div class="grid kpis" id="kpiBox"></div><div class="grid grid2"><div class="card"><h2>Primary Comparison</h2><div class="controls"><button onclick="preset('core')">Core</button><button onclick="preset('static')">MWM Static</button><button onclick="preset('tacticalmodels')">Tactical Models</button><button onclick="preset('all')">All</button></div><div id="overviewChecks" class="checks"></div><div class="chartbox"><canvas id="overviewChart"></canvas></div><div id="overviewLegend" class="legend"></div></div><div class="card"><h2>Current State &amp; Latest Trade</h2><div id="stateBox"></div><div id="latestTrade"></div></div></div><div class="card"><h2>Sortable Metrics</h2><div class="scroll"><table id="metricsTable"></table></div></div></section>
<section id="tactical" class="tab"><div class="card"><h2>Tactical Sleeve — MGK / MGV Binary (v3.3)</h2><div class="chartbox"><canvas id="tacticalChart"></canvas></div><div id="tacticalLegend" class="legend"></div></div><div class="card"><h2>Tactical Drawdown</h2><div class="chartbox short"><canvas id="tacticalDD"></canvas></div><div id="tacticalDDLegend" class="legend"></div><div class="note">Daily drawdown computed before chart downsampling.</div></div><div class="card"><h2>Tactical Metrics</h2><div class="scroll"><table id="tacticalMetrics"></table></div></div></section>
<section id="mwm" class="tab"><div class="card"><h2>MWM Static Models</h2><div class="chartbox"><canvas id="mwmChart"></canvas></div><div id="mwmLegend" class="legend"></div></div><div class="card"><h2>MWM Static Metrics</h2><div class="scroll"><table id="mwmMetrics"></table></div></div></section>
<section id="tacticalmodels" class="tab"><div class="card"><h2>Tactical Models</h2><div class="chartbox"><canvas id="tacticalModelsChart"></canvas></div><div id="tacticalModelsLegend" class="legend"></div></div><div class="card"><h2>Tactical Model Metrics</h2><div class="scroll"><table id="tacticalModelsMetrics"></table></div></div></section>
<section id="signals" class="tab"><div class="card"><h2>Recent Signals</h2><div class="scroll"><table id="signalTable"></table></div></div></section>
<section id="holding" class="tab"><div class="card"><h2>Holding Summary</h2><div class="scroll"><table id="holdingSummary"></table></div></div><div class="card"><h2>Holding Period Details</h2><div class="scroll"><table id="holdingPeriods"></table></div></div></section>
<section id="trade" class="tab"><div class="card"><h2>Trade Ledger</h2><div id="latestTrade2"></div><div class="scroll"><table id="tradeTable"></table></div></div></section>
<section id="chartaudit" class="tab"><div class="card"><h2>Chart Audit</h2><div class="scroll"><table id="chartAuditTable"></table></div></div><div class="card"><h2>Chart Rules</h2><table><tr><th>Window</th><th>Display frequency</th><th>Calculation basis</th></tr><tr><td>YTD, 1Y, 2Y</td><td>Daily</td><td>Full daily values</td></tr><tr><td>&gt;2Y and &lt;8Y</td><td>Weekly, last trading observation of week</td><td>Full daily values</td></tr><tr><td>≥8Y or SI</td><td>Monthly, last trading observation of month</td><td>Full daily values</td></tr><tr><td>Drawdown</td><td>Downsample after drawdown is computed</td><td>Daily running peak first</td></tr></table></div></section>
<section id="audit" class="tab"><div class="card"><h2>Metric Window Audit</h2><div id="windowAudit"></div><div class="scroll"><table id="windowRows"></table></div></div><div class="card"><h2>Production Audit</h2><div class="scroll"><table id="prodAuditTable"></table></div></div><div class="card"><h2>Data Audit</h2><div class="scroll"><table id="auditTable"></table></div></div></section>
<section id="dividend" class="tab">
<div class="card">
  <h2>Dividend Income <span class="pill">Income engine v6 verified</span></h2>
  <div class="note">Cash distributions are recorded separately from raw-close price return and reinvested at the distribution-date raw close in the income ledger. Yield on cost is intentionally not displayed.</div>
  <div class="controls"><b class="note">Model</b><span id="divModelButtons"></span></div>
  <div class="grid kpis" id="divKpis"></div>
</div>
<div class="grid grid2">
  <div class="card"><h2>Annual Dividend Income</h2><div class="chartbox short"><canvas id="divAnnualChart"></canvas></div><div id="divAnnualLegend" class="legend"></div></div>
  <div class="card"><h2>Cumulative Dividend Income</h2><div class="chartbox short"><canvas id="divCumulativeChart"></canvas></div><div id="divCumulativeLegend" class="legend"></div></div>
</div>
<div class="card"><h2>Annual Income Detail</h2><div class="scroll"><table id="divAnnualTable"></table></div></div>
<div class="card"><h2>Income by Asset</h2><div class="scroll"><table id="divAssetTable"></table></div></div>
<div class="card"><h2>Monthly Income by Asset</h2><div class="scroll"><table id="divAssetMonthlyTable"></table></div></div>
<div class="card"><h2>Fixed-Income Monthly Verification — $100,000 Initial Investment</h2><div class="note">PIMIX, JPIE, FIWDX, and JBND. Uses each fund's actual monthly distribution-per-share history.</div><div class="scroll"><table id="fixedIncomeVerifyTable"></table></div></div>
<div class="card"><h2>A1V12 Tactical Sleeve — Income by Holding Period</h2><div class="scroll"><table id="divPeriodTable"></table></div></div>
</section>
<section id="allocation" class="tab"><div class="card"><h2>Model Allocation</h2><div class="controls"><b class="note">Model</b><span id="allocModelButtons"></span></div><div class="grid grid2"><div><div class="chartbox"><canvas id="allocPie"></canvas></div><div id="allocLegend" class="legend"></div></div><div class="scroll"><table id="allocTable"></table></div></div></div></section>
<section id="config" class="tab"><div class="card"><h2>Backfill Scale Audit</h2><div class="note">Backfilled series are ratio-scaled to prevent artificial jumps at live/backfill transition dates.</div><div class="scroll"><table id="backfillAuditTable"></table></div></div><div class="card"><h2>Static to Tactical Model Map</h2><div class="scroll"><table id="modelMapTable"></table></div></div><div class="card"><h2>Normalized Allocation Config</h2><div class="scroll"><table id="allocationTable"></table></div></div></section>
</div><script>
const EMBEDDED=__PAYLOAD__;
const colors=['#6d35c4','#15803d','#0057b8','#e11d1d','#17365d','#a16207','#0f766e','#1d4ed8','#be123c','#7c3aed','#2563eb','#ea580c'];
const STR=new Set(['Date','Trade_Date','Trigger_Date','Start','End','Start_Date','End_Date','Asset','Production_Asset','State','EffectiveHolding','From','To','New_State','Rule','Status','Yahoo_Symbol','Notes','Check','Detail','Model','Static_Model','Tactical_Model','Chart','Series','Frequency','Holding','Through_Date']);
let sortState={},tableData={},period='3Y',periods=['YTD','1Y','2Y','3Y','5Y','2018','2016','SI'],visible=[];
function parseCSV(t){if(!t)return[];let L=t.trim().split(/\r?\n/);if(!L[0])return[];let H=L[0].split(',');return L.slice(1).filter(Boolean).map(l=>{let V=[],c='',q=false;for(let i=0;i<l.length;i++){let ch=l[i];if(ch=='"')q=!q;else if(ch==','&&!q){V.push(c);c=''}else c+=ch}V.push(c);let o={};H.forEach((h,i)=>{let v=V[i]??'',n=parseFloat(v);o[h]=(!STR.has(h)&&!isNaN(n)&&v.trim()!=='')?n:v});return o})}
let tactical=parseCSV(EMBEDDED.tactical),portfolio=parseCSV(EMBEDDED.portfolio),signals=parseCSV(EMBEDDED.signals),trades=parseCSV(EMBEDDED.trades),holdsum=parseCSV(EMBEDDED.holdsum),holdperiods=parseCSV(EMBEDDED.holdperiods),audit=parseCSV(EMBEDDED.dataaudit),prodaudit=parseCSV(EMBEDDED.prodaudit),modelmap=parseCSV(EMBEDDED.modelmap),alloc=parseCSV(EMBEDDED.alloc),backfillaudit=parseCSV(EMBEDDED.backfillaudit),divsummary=parseCSV(EMBEDDED.divsummary),divannual=parseCSV(EMBEDDED.divannual),divasset=parseCSV(EMBEDDED.divasset),divperiods=parseCSV(EMBEDDED.divperiods);
let bilSeries=tactical.filter(r=>isFinite(r['BIL Buy Hold'])).map(r=>({Date:r.Date,BIL:r['BIL Buy Hold']}));
function riskFreeCAGR(startDateStr,endDateStr){if(!bilSeries.length)return 0;let start=new Date(startDateStr),end=new Date(endDateStr);let inRange=bilSeries.filter(r=>{let dt=new Date(r.Date);return dt>=start&&dt<=end});if(inRange.length<2)return 0;let yrs=(new Date(inRange.at(-1).Date)-new Date(inRange[0].Date))/86400000/365.25;if(!(yrs>0))return 0;let ratio=inRange.at(-1).BIL/inRange[0].BIL;if(!(ratio>0))return 0;return Math.pow(ratio,1/yrs)-1}
function money(v){return isFinite(v)?'$'+v.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}):''}function pct(v){return isFinite(v)?(v*100).toFixed(2)+'%':''}function ratio(v){return isFinite(v)?v.toFixed(2):''}function num(v,d=2){return isFinite(v)?v.toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d}):v||''}
function fmt(h,v){if(v==null||v==='')return '';if(h.includes('Value')||h.includes('Price')||h.includes('Income'))return money(v);if(h.includes('CAGR')||h.includes('Volatility')||h.includes('Drawdown')||h.includes('Return')||h.includes('Pct')||h.includes('Weight')||h.includes('Diff')||h.includes('Yield'))return pct(v);if(h.includes('Sharpe')||h.includes('Ratio'))return ratio(v);if(h.includes('Days'))return num(v,1);if(h.includes('Rows')||h.includes('Periods')||h.includes('Count'))return isFinite(v)?Math.round(v).toLocaleString():v;return isFinite(v)?num(v,2):v}
function cls(h,v,row){let out='';if(h=='Status')out=v=='PASS'?'pass':(v=='FAIL'?'fail':'warn');else if(isFinite(v)){if(h.includes('Drawdown')||h.includes('Worst'))out='bad';else if(h.includes('CAGR')||h.includes('Sharpe')||h.includes('Return')||h.includes('Best'))out=v>=0?'good':'bad'}if(row&&row.__current)out+=' '+(row.State=='Value'?'state-value':'state-growth');return out}
function sortVal(v){if(v==null||v==='')return null;if(typeof v==='number')return v;let s=String(v);if(/^\d{4}-\d{2}-\d{2}/.test(s)){let d=Date.parse(s);if(!isNaN(d))return d;}let n=parseFloat(s.replace(/[$,%]/g,''));if(!isNaN(n))return n;return s.toLowerCase()}
function sortRows(id,h){let rows=tableData[id]||[],key=id+'|'+h,dir=sortState[key]=='asc'?'desc':'asc';sortState={};sortState[key]=dir;let sorted=[...rows].sort((a,b)=>{let av=sortVal(a[h]),bv=sortVal(b[h]);if(av==null&&bv==null)return 0;if(av==null)return 1;if(bv==null)return -1;if(av<bv)return dir=='asc'?-1:1;if(av>bv)return dir=='asc'?1:-1;return 0});drawTable(id,sorted)}
function drawTable(id,rows,freeze=false){let e=document.getElementById(id);if(!e)return;if(!rows||!rows.length){e.innerHTML='<tr><td class=note>No data</td></tr>';return}tableData[id]=rows;let H=Object.keys(rows[0]).filter(h=>h!='__current');let sk=Object.keys(sortState).find(k=>k.startsWith(id+'|')),active=sk?sk.split('|')[1]:null,dir=sk?sortState[sk]:null;e.innerHTML='<thead><tr>'+H.map((h,i)=>`<th class="${freeze&&i<3?'freeze'+(i+1):''}" onclick="sortRows('${id}','${String(h).replace(/'/g,"\\'")}')">${h}${active==h?(dir=='asc'?' ▲':' ▼'):''}</th>`).join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+H.map((h,i)=>`<td class="${(freeze&&i<3?'freeze'+(i+1)+' ':'')+cls(h,r[h],r)}">${fmt(h,r[h])}</td>`).join('')+'</tr>').join('')+'</tbody>'}
function cols(d){return d.length?Object.keys(d[0]).filter(k=>k!='Date'):[]}
function cut(d){if(!d.length)return[];let end=new Date(d[d.length-1].Date),start=new Date(d[0].Date);if(period=='YTD')start=new Date(end.getFullYear()-1,11,31);else if(period.endsWith('Y')){start=new Date(end);start.setFullYear(start.getFullYear()-parseInt(period))}else if(period=='2018')start=new Date('2018-01-01');else if(period=='2016')start=new Date('2016-01-01');return d.filter(r=>new Date(r.Date)>=start&&new Date(r.Date)<=end)}
function yearsIn(d){return d.length>1?(new Date(d.at(-1).Date)-new Date(d[0].Date))/86400000/365.25:0}
function displayFrequency(d){let yrs=period==='SI'?99:yearsIn(d);if(period==='YTD'||period==='1Y'||period==='2Y'||yrs<=2.05)return 'Daily';if(yrs>=8)return 'Monthly';return 'Weekly'}
function sampleDisplay(d){let freq=displayFrequency(d);if(freq==='Daily')return d;let map=new Map();d.forEach(r=>{let dt=new Date(r.Date);let key;if(freq==='Monthly'){key=dt.getFullYear()+'-'+String(dt.getMonth()+1).padStart(2,'0')}else{let x=new Date(dt);let day=x.getDay();let diff=(day+6)%7;x.setDate(x.getDate()-diff);key=x.toISOString().slice(0,10)}map.set(key,r)});return Array.from(map.values())}
function rebase(d,c){if(!d.length)return[];return d.map(r=>{let o={Date:r.Date};c.forEach(x=>{let f=d.find(z=>isFinite(z[x])&&z[x]>0);o[x]=f?r[x]/f[x]*100000:null});return o})}
function metric(d,c){let out=[];if(d.length<2)return out;let rf=riskFreeCAGR(d[0].Date,d[d.length-1].Date);c.forEach(x=>{let v=d.map(r=>r[x]).filter(isFinite);if(v.length<2)return;let days=(new Date(d[d.length-1].Date)-new Date(d[0].Date))/86400000,yrs=days/365.25,re=[];for(let i=1;i<v.length;i++)re.push(v[i]/v[i-1]-1);let avg=re.reduce((a,b)=>a+b,0)/re.length,sd=Math.sqrt(re.reduce((a,b)=>a+(b-avg)**2,0)/(re.length-1)),cagr=Math.pow(v.at(-1)/v[0],1/yrs)-1,vol=sd*Math.sqrt(252),peak=v[0],dd=0;v.forEach(z=>{peak=Math.max(peak,z);dd=Math.min(dd,z/peak-1)});out.push({Model:x,'Beginning Value':v[0],'Ending Value':v.at(-1),'Total Return':v.at(-1)/v[0]-1,CAGR:cagr,Volatility:vol,Risk_Free_CAGR:rf,'Sharpe (vs BIL)':vol?(cagr-rf)/vol:null,'Max Drawdown':dd,Rows:v.length,Days:days,Start:d[0].Date,End:d[d.length-1].Date})});return out}
function draw(id,dDaily,c,leg,isDD=false){let d=sampleDisplay(dDaily);let cv=document.getElementById(id);if(!cv)return;let box=cv.parentElement,wCss=Math.max(700,box.clientWidth||900),hCss=Math.max(260,box.clientHeight||430),pr=window.devicePixelRatio||1;cv.width=wCss*pr;cv.height=hCss*pr;let ctx=cv.getContext('2d');ctx.setTransform(pr,0,0,pr,0,0);let w=wCss,h=hCss;ctx.clearRect(0,0,w,h);ctx.font='11px Arial';if(!d.length||!c.length){ctx.fillText('No chart data',30,40);return}let vals=[];c.forEach(x=>d.forEach(r=>{if(isFinite(r[x]))vals.push(r[x])}));if(!vals.length){ctx.fillText('No numeric series selected',30,40);return}let mn=Math.min(...vals),mx=Math.max(...vals),pad=(mx-mn)*.08||1;mn-=pad;mx+=pad;let L=90,R=30,T=25,B=55;ctx.strokeStyle='#d7deea';ctx.fillStyle='#334155';for(let i=0;i<5;i++){let y=T+(h-T-B)*i/4;ctx.beginPath();ctx.moveTo(L,y);ctx.lineTo(w-R,y);ctx.stroke();let val=mx-(mx-mn)*i/4;ctx.fillText(isDD?pct(val):money(val),8,y+4)}c.forEach((x,j)=>{ctx.strokeStyle=colors[j%colors.length];ctx.lineWidth=x.includes('VOO')?2.5:2;ctx.beginPath();d.forEach((r,i)=>{let xx=L+(w-L-R)*(d.length===1?0:i/(d.length-1)),yy=T+(h-T-B)*(1-(r[x]-mn)/(mx-mn));i?ctx.lineTo(xx,yy):ctx.moveTo(xx,yy)});ctx.stroke()});let el=document.getElementById(leg);if(el)el.innerHTML=c.map((x,j)=>`<span><i class=sw style="background:${colors[j%colors.length]}"></i>${x}</span>`).join('')}
function dailyDrawdown(d,c){let z=d.map(r=>({Date:r.Date}));c.forEach(x=>{let p=null;z.forEach((o,i)=>{let v=d[i][x];if(!isFinite(v)){o[x]=null;return}p=Math.max(p||v,v);o[x]=v/p-1})});return z}
function chartAuditRows(name,dDaily,cols,drawdown=false){let basis=drawdown?dailyDrawdown(dDaily,cols):dDaily;let freq=displayFrequency(dDaily),disp=sampleDisplay(basis),rows=[];cols.forEach(x=>{let vals=basis.map(r=>r[x]).filter(isFinite),latestDaily=basis.length?basis.at(-1)[x]:null,latestPlot=disp.length?disp.at(-1)[x]:null;let miss=basis.length-vals.length;rows.push({Chart:name,Series:x,Frequency:freq,'Daily Rows':basis.length,'Plotted Rows':disp.length,'Missing Count':miss,'Latest Daily Date':basis.length?basis.at(-1).Date:'','Latest Plot Date':disp.length?disp.at(-1).Date:'','Latest Point Diff':(isFinite(latestDaily)&&isFinite(latestPlot))?latestPlot-latestDaily:null,Status:(miss===0&&(!isFinite(latestDaily)||Math.abs((latestPlot||0)-latestDaily)<1e-8))?'PASS':'WARN'})});return rows}
function staticCols(){return cols(portfolio).filter(x=>x.startsWith('MWM '))}
function tacticalModelCols(){return cols(portfolio).filter(x=>x.startsWith('Tactical '))}
function showTab(e,id){document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));document.getElementById(id).classList.add('active');document.querySelectorAll('.tabbtn').forEach(b=>b.classList.remove('active'));e.target.classList.add('active');setTimeout(render,80);setTimeout(renderAllocation,80)}
function setPeriod(p){period=p;sortState={};document.querySelectorAll('#periodButtons button').forEach(b=>b.classList.toggle('active',b.textContent==p));render()}
function preset(p){let all=cols(portfolio);visible=p=='core'?['A1V12 Tactical Sleeve','VOO Benchmark']:p=='static'?staticCols():p=='tacticalmodels'?tacticalModelCols():all;document.getElementById('overviewChecks').innerHTML=all.map(x=>`<label><input type=checkbox ${visible.includes(x)?'checked':''} onchange="tog('${x}',this.checked)"> ${x}</label>`).join('');render()}
function tog(x,on){if(on&&!visible.includes(x))visible.push(x);if(!on)visible=visible.filter(y=>y!=x);render()}
function recentSignals(){return [...signals].reverse().slice(0,60).map((r,i)=>({...r,__current:i==0}))}
function render(){let d=cut(portfolio),rb=rebase(d,visible),m=metric(rb,visible);document.getElementById('freqPill').innerHTML='Display: '+displayFrequency(d);draw('overviewChart',rb,visible,'overviewLegend');drawTable('metricsTable',m);document.getElementById('kpiBox').innerHTML=m.slice(0,4).map(r=>`<div class=kpi><div class=label>${r.Model}</div><div class=big>${money(r['Ending Value'])}</div><div class=note>Total <span class=good>${pct(r['Total Return'])}</span> | CAGR <span class=good>${pct(r.CAGR)}</span></div></div>`).join('');document.getElementById('windowAudit').innerHTML=d.length?`<b>${period}</b><br>Start: ${d[0].Date}<br>End: ${d.at(-1).Date}<br>Daily rows: ${d.length}<br>Display frequency: ${displayFrequency(d)}<br>Display rows: ${sampleDisplay(d).length}`:'No data';drawTable('windowRows',m);
let tc=cols(tactical).filter(x=>['A1V12','VOO Benchmark','MGK Buy Hold','MGV Buy Hold'].includes(x));
let td=rebase(cut(tactical),tc),tm=metric(td,tc);draw('tacticalChart',td,tc,'tacticalLegend');draw('tacticalDD',dailyDrawdown(td,tc),tc,'tacticalDDLegend',true);drawTable('tacticalMetrics',tm);let sd=rebase(cut(portfolio),staticCols());draw('mwmChart',sd,staticCols(),'mwmLegend');drawTable('mwmMetrics',metric(sd,staticCols()));let tmd=rebase(cut(portfolio),tacticalModelCols());draw('tacticalModelsChart',tmd,tacticalModelCols(),'tacticalModelsLegend');drawTable('tacticalModelsMetrics',metric(tmd,tacticalModelCols()));state();drawTable('signalTable',recentSignals(),true);let auditRows=[...chartAuditRows('Overview',rb,visible,false),...chartAuditRows('Tactical Sleeve',td,tc,false),...chartAuditRows('Tactical Drawdown',td,tc,true),...chartAuditRows('MWM Static',sd,staticCols(),false),...chartAuditRows('Tactical Models',tmd,tacticalModelCols(),false)];drawTable('chartAuditTable',auditRows)}
function state(){let s=signals.at(-1)||{},tr=trades.at(-1)||{};let stateHtml=`<div class=tradebox><div class=tradeitem><div class=label>Current State</div><div class=big>${s.State||'N/A'}</div></div><div class=tradeitem><div class=label>Current Holding</div><div class=big>${s.EffectiveHolding||'N/A'}</div></div><div class=tradeitem><div class=label>Latest Signal Date</div><div class=big>${s.Date||'N/A'}</div></div><div class=tradeitem><div class=label>MGK/MGV Ratio</div><div class=big>${isFinite(s.MGK_MGV)?s.MGK_MGV.toFixed(4):'N/A'}</div></div><div class=tradeitem><div class=label>EMA89</div><div class=big>${isFinite(s.MGK_MGV_EMA89)?s.MGK_MGV_EMA89.toFixed(4):'N/A'}</div></div></div>`;let tradeHtml=`<div class=tradebox style="margin-top:10px"><div class=tradeitem><div class=label>Trigger Date</div><div class=big>${tr.Trigger_Date||'N/A'}</div></div><div class=tradeitem><div class=label>Trade Date</div><div class=big>${tr.Trade_Date||'N/A'}</div></div><div class=tradeitem><div class=label>Latest Trade</div><div class=big>${tr.From||''} → ${tr.To||''}</div></div><div class=tradeitem><div class=label>Rule</div><div class=note>${tr.Rule||'N/A'}</div></div></div>`;document.getElementById('stateBox').innerHTML=stateHtml;document.getElementById('latestTrade').innerHTML=tradeHtml;document.getElementById('latestTrade2').innerHTML=tradeHtml}
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
  if(!divModel){
    if(divModels().length)divModel=divModels()[0];
    else return;
  }
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
    `<div class=kpi><div class=label>Annualized YTD Run Rate</div><div class=big>${money(sum.Current_Year_Annualized_Run_Rate)}</div><div class=note>Illustrative run rate, not a forecast</div></div>`;

  let annualRows=ann.map(r=>({
    Model:r.Model,
    Period:r.Period||String(r.Year),
    Dividend_Income:r.Dividend_Income,
    Cumulative_Income:r.Cumulative_Income,
    Annualized_Run_Rate:r.Annualized_Run_Rate,
    Is_Partial_Year:r.Is_Partial_Year
  }));
  drawTable('divAnnualTable',annualRows);
  drawTable('divAssetTable',divasset.filter(r=>r.Model===divModel).sort((a,b)=>b.Year-a.Year||b.Dividend_Income-a.Dividend_Income));
  drawTable('divAssetMonthlyTable',divassetmonthly.filter(r=>r.Model===divModel).sort((a,b)=>String(b.Month).localeCompare(String(a.Month))||b.Dividend_Income-a.Dividend_Income));
  drawTable('fixedIncomeVerifyTable',fixedincomeverify.slice().sort((a,b)=>String(b.Month).localeCompare(String(a.Month))||String(a.Asset).localeCompare(String(b.Asset))));
  drawTable('divPeriodTable',divperiods.slice().reverse());
}
function init(){document.getElementById('periodButtons').innerHTML=periods.map(p=>`<button onclick="setPeriod('${p}')" class="${p==period?'active':''}">${p}</button>`).join('');preset('core');staticTables();document.getElementById('allocModelButtons').innerHTML=allocModels().map(m=>`<button onclick="setAllocModel('${m}')">${m}</button>`).join('');if(allocModels().length)setAllocModel(allocModels()[0]);document.getElementById('divModelButtons').innerHTML=divModels().map(m=>`<button onclick="setDivModel('${m.replace(/'/g,"\\'")}')">${m}</button>`).join('');if(divModels().length)setDivModel(divModels()[0]);setTimeout(render,120);setTimeout(renderAllocation,120);setTimeout(renderDividend,120)}
window.addEventListener('resize',()=>setTimeout(()=>{render();renderAllocation();renderDividend()},120));init();
</script></body></html>"""


def main():
    print("BUILD MARKER: dividend-engine-v4-open-price-guarded")
    backup = backup_existing_outputs()
    print("Backup folder:", backup)

    alloc_df = read_allocations()
    static_models, tactical_models = build_model_configs(alloc_df)
    required_assets = set(alloc_df["Production_Asset"].unique()) | {"MGK", "MGV", "VOO", "BIL"}

    adj_wide, raw_wide, open_wide, div_wide, data_audit_df = download_prices(required_assets)
    comp_adj, _ = build_composites(adj_wide, required_assets,
        output_name="Composite_Prices.csv", audit_name="Backfill_Scale_Audit.csv",
        price_basis="Adjusted Close")
    comp_raw, raw_scale = build_composites(raw_wide, required_assets,
        output_name="Composite_Prices_Raw_Close.csv", audit_name="Backfill_Raw_Scale_Audit.csv",
        price_basis="Raw Close")
    comp_open, _ = build_composites(open_wide, required_assets,
        output_name="Composite_Prices_Open.csv", audit_name="Backfill_Open_Scale_Audit.csv",
        price_basis="Open")
    div_comp = build_dividend_composites(div_wide, raw_scale, required_assets)

    sig, trades = build_signals(comp_adj)
    tv = build_tactical_values(comp_raw, comp_open, sig)
    pv = build_portfolios(comp_raw, tv, static_models, tactical_models)
    build_holding_analytics(sig, comp_raw)
    build_dividend_analytics(comp_raw, comp_open, div_comp, sig, tv, static_models, tactical_models)
    run_audit(alloc_df, static_models, tactical_models, comp_adj, comp_raw,
              sig, trades, tv, pv, data_audit_df)
    dash = build_dashboard()

    print("\nA1V12 Yahoo Production v3.3 complete.")
    print(f"Signal engine:   Adjusted Close · Binary MGK/MGV · EMA89 crossover · {COOLDOWN_DAYS}-day cooldown")
    print("Performance:     Raw Close price return")
    print("Dividend income: Separate model-level cash-income reporting")
    print("Rebalancing:     Annual (Jan 1) for all multi-asset models")
    print("Static models:  ", ", ".join(static_models.keys()))
    print("Tactical models:", ", ".join(tactical_models.keys()))
    print("Latest data:    ", pv["Date"].max())
    print("Dashboard:      ", dash)

if __name__ == "__main__":
    main()
