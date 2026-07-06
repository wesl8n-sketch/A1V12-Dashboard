#!/usr/bin/env python3
"""
A1V12 Yahoo Production v3.2 (+ Allocation tab)

Complete integrated package.

v3.2 includes:
- v3.0 configuration-driven model engine using Config/MWM_Allocations.csv
- Static MWM and Tactical Models separated
- Tactical model names: Tactical Growth, Tactical Moderate, etc.
- Chart Audit integrated
- Daily calculations retained for metrics/signals/trades
- Charts downsampled for display only:
    YTD/1Y/2Y = Daily
    >2Y and <8Y = Weekly
    >=8Y or SI = Monthly
- Drawdown computed from daily series first, then downsampled

Update (previous revision):
- Added an "Allocation" tab: per-model pie chart (with on-slice percentage
  labels) + legend + weight table, built from the same Allocation_Config_
  Normalized.csv payload already embedded in the dashboard. No new data
  files or external chart libraries required.

Update (this revision):
- True Sharpe ratio: "Sharpe" was previously CAGR / Volatility with no
  risk-free adjustment. It is now (CAGR - Risk_Free_CAGR) / Volatility,
  where Risk_Free_CAGR is computed from the BIL Buy Hold series already
  embedded in the tactical data payload, matched to each metric's own date
  window. Column relabeled "Sharpe (vs BIL)"; a visible Risk_Free_CAGR
  column was added alongside it for auditability.
- Chart Audit drawdown bug fix: drawdown-chart audit rows previously
  compared the latest *portfolio value* (dollars) against the latest
  *drawdown* value (percentage), always producing a nonsensical diff and
  spurious WARN status. Both sides of the comparison now come from the
  same basis (the daily drawdown series) when auditing a drawdown chart.
- Whipsaw test #1 (MIN_HOLD_DAYS cooldown, Value-mode scoped): Holding_
  Periods.csv showed a cluster of 9 holding changes across ~3 months (Aug-
  Oct 2024), including three same-day round-trips returning exactly 0.00%,
  coinciding with the Tactical Growth vs VOO underperformance window seen
  on the dashboard. A first attempt cooled down ALL holding switches
  (including Growth<->Value transitions) and, while it did collapse the
  whipsaw cluster, it also delayed one Growth exit by 3 days into a market
  move against it (+0.41% became -2.65% on that leg), roughly offsetting
  the win elsewhere. This revision scopes MIN_HOLD_DAYS (default 10
  trading days) to ONLY gate MGV<->JIVE switching once already inside the
  Value state -- Growth<->Value transitions still fire immediately on a
  confirmed 3-day streak, unchanged from the original design. Set
  MIN_HOLD_DAYS = 0 to fully restore the original, uncooled behavior for
  comparison. The setting is written to Audit/Production_Audit.csv as
  "Minimum holding cooldown" for visibility on the dashboard's Audit tab.
"""

from pathlib import Path
import sys, subprocess, importlib.util, json, shutil
from datetime import datetime

PROJECT = Path(__file__).resolve().parents[1]
DATA = PROJECT / "Data"
DASH = PROJECT / "Dashboard"
AUDIT = PROJECT / "Audit"
BACKUPS = PROJECT / "Backups"
CONFIG = PROJECT / "Config"
for p in [DATA, DASH, AUDIT, BACKUPS, CONFIG]:
    p.mkdir(exist_ok=True)

START_DATE = "2011-01-01"
BASE_VALUE = 100000.0

CORE_ASSETS = ["MGK","MGV","JIVE","VOO","BIL","VEU","AVUV","JPIE","JBND","FIWDX","FIKQX","FBTC","XLG","IMCB","XLF","XLV","SPHB","MTUM","PIMIX"]
RESEARCH_ASSETS = ["EFV","DFSVX","JMSIX","WOBDX","FSRIX","FGBPX","XLRE","DXY","VIX","NERYX","VFINX","JMSFX","FRDM"]
YMAP = {"DXY":"DX-Y.NYB", "VIX":"^VIX"}

BACKFILLS = {
    "JIVE": ("EFV", "2023-12-31"),
    "AVUV": ("DFSVX", "2019-09-23"),
    "JPIE": ("JMSIX", "2021-10-27"),
    "JBND": ("WOBDX", "2023-11-30"),
    "FIWDX": ("FSRIX", "2010-12-31"),
    "FIKQX": ("FGBPX", "2010-12-31"),
    "FBTC": ("XLRE", "2025-11-30"),
    "VOO": ("VFINX", "2010-12-31"),
}

ASSET_ALIASES = {
    "NERYX": "JPIE",
    "JMSFX": "JPIE",
    "JMSIX": "JPIE",
    "DFSVX": "AVUV",
    "EFV": "JIVE",
    "XLRE": "FBTC",
    "WOBDX": "JBND",
    "FSRIX": "FIWDX",
    "FGBPX": "FIKQX",
    "FBTC_HIST": "FBTC",  # FIX: Allocation_Config used "FBTC_HIST" as the asset
    # name, but no ticker/backfill by that name exists -- Yahoo download failed
    # silently and the resulting NaN column was pct_change().fillna(0)'d into a
    # permanent flat 0% return for the 4-5% Bitcoin sleeve in MWM Growth,
    # Aggressive, Growth Plus, and Ultra Aggressive. The real, correctly
    # backfilled series lives under "FBTC" (blended with XLRE pre-2025-12-01).
    "TACTICAL": "A1V12",
    "A1V12": "A1V12",
}

TACTICAL_REPLACEMENT_CANDIDATES = {"MGK", "XLG", "VOO"}

# Test #1 (v2 - scoped) for the whipsaw cluster identified in
# Holding_Periods.csv (e.g. the 9 switches across MGV/JIVE/MGK in
# Aug-Oct 2024, including three same-day round-trips with 0.00% return).
# MIN_HOLD_DAYS blocks MGV<->JIVE switching *within the Value state only*
# until this many trading days have passed since the last such switch.
# Growth<->Value transitions are NOT cooled down -- they still fire
# immediately on a confirmed 3-day streak, same as the original behavior.
# (An earlier version of this test also cooled Growth<->Value transitions;
# that version delayed one Growth exit by 3 days into a market move against
# it, turning a +0.41% MGK holding period into -2.65%. Scoping the cooldown
# to Value-mode only avoids that side effect.) Set to 0 to fully restore
# the original, uncooled behavior for comparison.
MIN_HOLD_DAYS = 10

def ensure(pkg):
    if importlib.util.find_spec(pkg) is None:
        print(f"Installing {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])

def backup_existing_outputs():
    tag = datetime.now().strftime("%Y%m%d_%H%M%S")
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
    model_col = cols.get("model")
    asset_col = cols.get("asset") or cols.get("ticker")
    weight_col = cols.get("weight") or cols.get("allocation")
    if not (model_col and asset_col and weight_col):
        raise ValueError("Allocation file must contain Model, Asset, Weight columns.")
    out = df[[model_col, asset_col, weight_col]].copy()
    out.columns = ["Model", "Asset", "Weight"]
    out["Model"] = out["Model"].astype(str).str.strip()
    out["Asset"] = out["Asset"].astype(str).str.strip().str.upper()
    out["Weight"] = pd.to_numeric(out["Weight"].astype(str).str.replace("%","", regex=False), errors="coerce")
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
            weights = {k: v/total for k, v in weights.items()}
        static[model] = weights

    tactical = {}
    map_rows = []
    for model, weights in static.items():
        clean = model.replace("MWM ", "").strip()
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
            neww = {k: v/total for k, v in neww.items()}
        tactical[tactical_name] = neww
        map_rows.append([model, tactical_name, replaced_weight, "MGK/XLG/VOO sleeve replaced by A1V12 tactical sleeve"])

    import pandas as pd
    pd.DataFrame(map_rows, columns=["Static_Model","Tactical_Model","Tactical_Weight","Rule"]).to_csv(DATA / "Tactical_Model_Map.csv", index=False)
    return static, tactical

def download_prices(required_assets):
    ensure("pandas"); ensure("numpy"); ensure("yfinance")
    import pandas as pd
    import yfinance as yf
    all_assets = sorted(set(required_assets) | set(CORE_ASSETS) | set(RESEARCH_ASSETS))
    frames, audit = [], []
    for asset in all_assets:
        if asset in {"TACTICAL", "A1V12"}:
            continue
        sym = YMAP.get(asset, asset)
        print(f"Downloading {asset} ({sym}) adjusted close...")
        try:
            raw = yf.download(sym, start=START_DATE, auto_adjust=False, progress=False, threads=False)
            if raw.empty and asset == "DXY":
                raw = yf.download("^DXY", start=START_DATE, auto_adjust=False, progress=False, threads=False)
            if raw.empty:
                audit.append([asset, sym, "FAIL", "", "", 0, "No data returned"])
                continue
            series = raw["Adj Close"] if "Adj Close" in raw.columns else raw["Close"]
            f = series.reset_index()
            f.columns = ["Date", asset]
            f["Date"] = pd.to_datetime(f["Date"]).dt.normalize()
            f[asset] = pd.to_numeric(f[asset], errors="coerce")
            f = f.dropna(subset=[asset])
            frames.append(f)
            audit.append([asset, sym, "OK", f["Date"].min().date().isoformat(), f["Date"].max().date().isoformat(), len(f), "Adj Close"])
        except Exception as e:
            audit.append([asset, sym, "ERROR", "", "", 0, str(e)])
    if not frames:
        raise RuntimeError("No Yahoo data downloaded.")
    wide = frames[0]
    for f in frames[1:]:
        wide = wide.merge(f, on="Date", how="outer")
    wide = wide.sort_values("Date").reset_index(drop=True)
    wide.to_csv(DATA / "Price_Master_Wide.csv", index=False, date_format="%Y-%m-%d")
    wide.melt(id_vars=["Date"], var_name="Asset", value_name="Adj_Close").dropna().to_csv(DATA / "Price_Master_Long.csv", index=False, date_format="%Y-%m-%d")
    audit_df = pd.DataFrame(audit, columns=["Asset","Yahoo_Symbol","Status","First_Date","Last_Date","Rows","Notes"])
    audit_df.to_csv(AUDIT / "Data_Audit.csv", index=False)
    return wide, audit_df

def build_composites(wide, required_assets):
    """
    Build continuous composite price series with ratio-scaled backfills.

    Backfill legs are scaled so that the backfill proxy aligns with the first
    live observation after the cutoff. This prevents artificial transition jumps
    in performance charts and portfolio values.
    """
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

            live = pd.to_numeric(df[asset], errors="coerce") if asset in df.columns else pd.Series(np.nan, index=df.index)
            bfv = pd.to_numeric(df[bf], errors="coerce") if bf in df.columns else pd.Series(np.nan, index=df.index)

            scale = 1.0
            live_date = None
            bf_date = None
            status = "UNSCALED"

            # Anchor on first live observation after cutoff.
            live_mask = (df["Date"] > cutoff) & live.notna()
            if live_mask.any() and bfv.notna().any():
                live_idx = live_mask[live_mask].index[0]
                live_date = df.loc[live_idx, "Date"]
                prior_bf_mask = (df["Date"] <= live_date) & bfv.notna()
                if prior_bf_mask.any():
                    bf_idx = prior_bf_mask[prior_bf_mask].index[-1]
                    bf_date = df.loc[bf_idx, "Date"]
                    live_val = live.loc[live_idx]
                    bf_val = bfv.loc[bf_idx]
                    if pd.notna(live_val) and pd.notna(bf_val) and bf_val != 0:
                        scale = float(live_val / bf_val)
                        status = "SCALED"

            scaled_bf = bfv * scale
            comp[asset] = np.where(df["Date"] <= cutoff, scaled_bf, live)
            scale_rows.append([
                asset, bf, until, scale, status,
                live_date.date().isoformat() if live_date is not None else "",
                bf_date.date().isoformat() if bf_date is not None else ""
            ])
        else:
            comp[asset] = df[asset] if asset in df.columns else np.nan

    for asset in RESEARCH_ASSETS:
        if asset in df.columns and asset not in comp.columns:
            comp[asset] = df[asset]

    comp.to_csv(DATA / "Composite_Prices.csv", index=False, date_format="%Y-%m-%d")
    pd.DataFrame(
        scale_rows,
        columns=["Asset","Backfill_Asset","Cutoff","Scale_Factor","Status","First_Live_Date","Backfill_Anchor_Date"]
    ).to_csv(AUDIT / "Backfill_Scale_Audit.csv", index=False)
    return comp

def ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=n).mean()

def build_signals(comp):
    import pandas as pd
    df = comp.dropna(subset=["MGK","MGV"]).copy().sort_values("Date").reset_index(drop=True)
    sig = pd.DataFrame({"Date": df["Date"], "MGK": df["MGK"], "MGV": df["MGV"], "JIVE": df["JIVE"]})
    sig["MGK_MGV"] = sig["MGK"] / sig["MGV"]
    sig["MGK_MGV_EMA89"] = ema(sig["MGK_MGV"], 89)
    sig["Growth_Threshold"] = 1.005 * sig["MGK_MGV_EMA89"]
    sig["Value_Threshold"] = 0.990 * sig["MGK_MGV_EMA89"]
    sig["Growth_Qualifies"] = sig["MGK_MGV"] >= sig["Growth_Threshold"]
    sig["Value_Qualifies"] = sig["MGK_MGV"] <= sig["Value_Threshold"]
    g = v = 0
    gs, vs = [], []
    for _, r in sig.iterrows():
        g = g + 1 if bool(r["Growth_Qualifies"]) else 0
        v = v + 1 if bool(r["Value_Qualifies"]) else 0
        gs.append(g); vs.append(v)
    sig["Growth_Streak"] = gs
    sig["Value_Streak"] = vs
    sig["JIVE_MGV"] = sig["JIVE"] / sig["MGV"]
    sig["JIVE_MGV_EMA89"] = ema(sig["JIVE_MGV"], 89)
    sig["Use_JIVE"] = sig["JIVE_MGV"] >= 1.005 * sig["JIVE_MGV_EMA89"]

    state, holding, pending = "Growth", "MGK", None
    trades, holdings = [], []
    days_since_value_switch = MIN_HOLD_DAYS  # allows immediate MGV/JIVE pick the first time Value is entered
    for _, r in sig.iterrows():
        date = r["Date"]
        if pending is not None:
            ns, nh, trigger_date = pending
            if nh != holding:
                rule = "Next trading day after trigger"
                if ns == "Value":
                    rule += f" (Value-mode MGV/JIVE cooldown={MIN_HOLD_DAYS}d)"
                trades.append([date, trigger_date, holding, nh, ns, rule])
                state, holding = ns, nh
                if ns == "Value":
                    days_since_value_switch = 0
            pending = None
        holdings.append([date, state, holding])
        days_since_value_switch += 1
        value_cooldown_clear = days_since_value_switch >= MIN_HOLD_DAYS
        # Growth<->Value transitions fire immediately on a confirmed streak,
        # same as the original (uncooled) behavior -- the cooldown below only
        # gates MGV<->JIVE switching once already inside the Value state,
        # which is where the whipsaw cluster in Holding_Periods.csv actually
        # occurred.
        if state != "Growth" and r["Growth_Streak"] >= 3:
            pending = ("Growth", "MGK", date)
        elif state != "Value" and r["Value_Streak"] >= 3:
            pending = ("Value", "JIVE" if bool(r["Use_JIVE"]) else "MGV", date)
        elif state == "Value" and holding == "MGV" and bool(r["Use_JIVE"]) and value_cooldown_clear:
            pending = ("Value", "JIVE", date)
        elif state == "Value" and holding == "JIVE" and not bool(r["Use_JIVE"]) and value_cooldown_clear:
            pending = ("Value", "MGV", date)

    h = pd.DataFrame(holdings, columns=["Date","State","EffectiveHolding"])
    sig = sig.merge(h, on="Date", how="left")
    front = ["Date","State","EffectiveHolding","MGK","MGV","JIVE","MGK_MGV","MGK_MGV_EMA89","Growth_Threshold","Value_Threshold"]
    sig = sig[front + [c for c in sig.columns if c not in front]]
    trades_df = pd.DataFrame(trades, columns=["Trade_Date","Trigger_Date","From","To","New_State","Rule"])
    sig.to_csv(DATA / "Signal_History.csv", index=False, date_format="%Y-%m-%d")
    h.to_csv(DATA / "Daily_Holdings.csv", index=False, date_format="%Y-%m-%d")
    trades_df.to_csv(DATA / "Trade_Ledger.csv", index=False, date_format="%Y-%m-%d")
    return sig, trades_df

def build_tactical_values(comp, sig):
    import pandas as pd
    df = comp.merge(sig[["Date","EffectiveHolding"]], on="Date", how="inner")
    df = df.dropna(subset=["MGK","MGV","JIVE","VOO"]).sort_values("Date").reset_index(drop=True)
    val = xmgv = xjive = BASE_VALUE
    rows = []
    for i, r in df.iterrows():
        if i > 0:
            p = df.iloc[i-1]
            h = r["EffectiveHolding"]
            val *= r[h] / p[h]
            hx = "MGK" if h == "MGK" else "JIVE"
            hj = "MGK" if h == "MGK" else "MGV"
            xmgv *= r[hx] / p[hx]
            xjive *= r[hj] / p[hj]
        rows.append([r["Date"], val, xmgv, xjive, r["EffectiveHolding"]])
    tv = pd.DataFrame(rows, columns=["Date","A1V12","A1V12-XMGV","A1V12-XJIVE","EffectiveHolding"])
    start = df.iloc[0]
    for a in ["MGK","MGV","JIVE","VOO","BIL"]:
        if a in df.columns and pd.notna(start[a]) and start[a] != 0:
            tv["VOO Benchmark" if a == "VOO" else f"{a} Buy Hold"] = BASE_VALUE * df[a] / start[a]
    tv.to_csv(DATA / "Tactical_Daily_Values.csv", index=False, date_format="%Y-%m-%d")
    return tv

def build_portfolios(comp, tv, static_models, tactical_models):
    import pandas as pd
    all_models = {"VOO Benchmark":{"VOO":1.0}, "A1V12 Tactical Sleeve":{"TACTICAL":1.0}}
    all_models.update(static_models)
    all_models.update(tactical_models)
    df = comp.merge(tv[["Date","A1V12"]], on="Date", how="inner").sort_values("Date").reset_index(drop=True)
    ret = pd.DataFrame({"Date": df["Date"], "TACTICAL": df["A1V12"].pct_change().fillna(0), "A1V12": df["A1V12"].pct_change().fillna(0)})
    for a in [c for c in df.columns if c != "Date"]:
        ret[a] = df[a].pct_change().fillna(0)
    vals = pd.DataFrame({"Date": df["Date"]})
    for name, weights in all_models.items():
        v = [BASE_VALUE]
        for i in range(1, len(df)):
            dr = 0.0
            for a, w in weights.items():
                key = "TACTICAL" if a in {"TACTICAL","A1V12"} else a
                if key in ret.columns:
                    dr += w * ret.loc[i, key]
            v.append(v[-1] * (1 + dr))
        vals[name] = v
    vals.to_csv(DATA / "Portfolio_Daily_Values.csv", index=False, date_format="%Y-%m-%d")
    return vals

def build_holding_analytics(sig):
    import pandas as pd
    df = sig[["Date","EffectiveHolding","MGK","MGV","JIVE"]].copy()
    rows, start, current = [], 0, df.loc[0, "EffectiveHolding"]
    def period(st, en, asset):
        sub = df.iloc[st:en+1]
        sp = sub[asset].iloc[0]
        ep = sub[asset].iloc[-1]
        return {"Start_Date": sub["Date"].iloc[0], "End_Date": sub["Date"].iloc[-1], "Asset": asset, "Trading_Days": len(sub), "Start_Price": sp, "End_Price": ep, "Return": ep / sp - 1 if sp else None}
    for i in range(1, len(df)):
        if df.loc[i, "EffectiveHolding"] != current:
            rows.append(period(start, i-1, current))
            start, current = i, df.loc[i, "EffectiveHolding"]
    rows.append(period(start, len(df)-1, current))
    hp = pd.DataFrame(rows)
    hp.to_csv(DATA / "Holding_Periods.csv", index=False, date_format="%Y-%m-%d")
    hs = hp.groupby("Asset").agg(
        Periods=("Asset","count"),
        Avg_Trading_Days=("Trading_Days","mean"),
        Median_Trading_Days=("Trading_Days","median"),
        Min_Trading_Days=("Trading_Days","min"),
        Max_Trading_Days=("Trading_Days","max"),
        Avg_Return=("Return","mean"),
        Best_Return=("Return","max"),
        Worst_Return=("Return","min"),
    ).reset_index()
    hs["Pct_Time"] = hs["Asset"].map(hp.groupby("Asset")["Trading_Days"].sum() / hp["Trading_Days"].sum())
    hs.to_csv(DATA / "Holding_Summary.csv", index=False)

def run_audit(alloc_df, static_models, tactical_models, comp, sig, trades, tv, pv, data_audit_df):
    import pandas as pd
    checks = []
    def add(name, status, detail): checks.append([name, status, detail])
    add("Price basis", "PASS", "Yahoo Adjusted Close")
    add("Allocation file", "PASS", "Config/MWM_Allocations.csv")
    add("Allocation rows", "PASS", str(len(alloc_df)))
    add("Static MWM models", "PASS", ", ".join(static_models.keys()))
    add("Tactical models", "PASS", ", ".join(tactical_models.keys()))
    add("Minimum holding cooldown", "PASS" if MIN_HOLD_DAYS >= 0 else "FAIL", f"{MIN_HOLD_DAYS} trading days between MGV<->JIVE switches within Value mode (Growth<->Value transitions uncooled)")

    # FIX: every Production_Asset referenced by the allocation config must have
    # actually downloaded. This is what would have caught FBTC_HIST immediately
    # instead of it silently defaulting to a flat 0% return in production.
    required = set(alloc_df["Production_Asset"].unique())
    ok_assets = set(data_audit_df.loc[data_audit_df["Status"] == "OK", "Asset"])
    failed_downloads = sorted(a for a in data_audit_df.loc[data_audit_df["Status"] != "OK", "Asset"] if a in required)
    unresolved = sorted(a for a in required if a not in ok_assets and a not in BACKFILLS and a not in {"TACTICAL", "A1V12"})
    missing = sorted(set(failed_downloads) | set(unresolved))
    add("Allocation assets resolve to live data", "PASS" if not missing else "FAIL",
        "All Production_Assets have OK price data" if not missing else f"Unresolved/failed: {', '.join(missing)}")

    add("Composite rows", "PASS" if len(comp) else "FAIL", str(len(comp)))
    add("Signal rows", "PASS" if len(sig) else "FAIL", str(len(sig)))
    add("Trade ledger rows", "PASS" if len(trades) else "WARN", str(len(trades)))
    add("Portfolio values rows", "PASS" if len(pv) else "FAIL", str(len(pv)))
    add("Latest signal date", "PASS", str(sig["Date"].max()) if len(sig) else "N/A")
    add("Latest portfolio date", "PASS", str(pv["Date"].max()) if len(pv) else "N/A")
    add("Backfill scaling", "PASS", "Backfill legs ratio-scaled to first live observation")
    add("Chart downsampling", "PASS", "Daily <=2Y, Weekly >2Y, Monthly >=8Y/SI")
    add("Drawdown chart", "PASS", "Daily drawdown computed before downsampling")
    add("MCI production status", "PASS", "Research-only / unused")
    pd.DataFrame(checks, columns=["Check","Status","Detail"]).to_csv(AUDIT / "Production_Audit.csv", index=False)

def csv_payload(name):
    p = DATA / name
    return p.read_text() if p.exists() else ""

def build_dashboard():
    payload = {
        "tactical": csv_payload("Tactical_Daily_Values.csv"),
        "portfolio": csv_payload("Portfolio_Daily_Values.csv"),
        "signals": csv_payload("Signal_History.csv"),
        "trades": csv_payload("Trade_Ledger.csv"),
        "holdsum": csv_payload("Holding_Summary.csv"),
        "holdperiods": csv_payload("Holding_Periods.csv"),
        "dataaudit": (AUDIT / "Data_Audit.csv").read_text() if (AUDIT / "Data_Audit.csv").exists() else "",
        "prodaudit": (AUDIT / "Production_Audit.csv").read_text() if (AUDIT / "Production_Audit.csv").exists() else "",
        "modelmap": csv_payload("Tactical_Model_Map.csv"),
        "alloc": csv_payload("Allocation_Config_Normalized.csv"),
        "backfillaudit": (AUDIT / "Backfill_Scale_Audit.csv").read_text() if (AUDIT / "Backfill_Scale_Audit.csv").exists() else "",
    }
    html = DASHBOARD_HTML.replace("__PAYLOAD__", json.dumps(payload))
    out = DASH / "A1V12_Yahoo_Production_v3_2_Dashboard.html"
    out.write_text(html)
    return out

DASHBOARD_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>A1V12 Yahoo Production v3.2</title>
<style>
body{font-family:Arial;margin:0;background:#f5f7fb;color:#111827}.wrap{max-width:1680px;margin:auto;padding:18px}h1{color:#17365d;margin:0}.sub{color:#64748b;font-size:13px}.card{background:white;border:1px solid #d7deea;border-radius:13px;padding:14px;margin:12px 0}.tabs,.controls,.checks{display:flex;gap:7px;flex-wrap:wrap;margin:10px 0}button{border:1px solid #cbd5e1;background:white;border-radius:9px;padding:8px 11px;font-weight:700;cursor:pointer}button.active{background:#17365d;color:white}.tab{display:none}.tab.active{display:block}.grid{display:grid;gap:12px}.grid2{grid-template-columns:2fr 1fr}.kpis{grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}.kpi{background:#f8fafc;border:1px solid #e5e7eb;border-radius:10px;padding:10px}.label{font-size:11px;text-transform:uppercase;color:#64748b;font-weight:800}.big{font-size:22px;font-weight:900}.chartbox{height:430px;width:100%;border:1px solid #eef2f7;border-radius:10px;background:white}.chartbox.short{height:300px}canvas{width:100%;height:100%;display:block}.legend{display:flex;flex-wrap:wrap;gap:16px;font-size:12px;margin-top:10px}.sw{width:18px;height:4px;border-radius:2px;display:inline-block;margin-right:5px}.scroll{max-height:560px;overflow:auto;border:1px solid #eef2f7;border-radius:10px}table{border-collapse:collapse;width:100%;font-size:12px}th,td{border-bottom:1px solid #e5e7eb;padding:7px;text-align:right;white-space:nowrap}th{background:#f3f4f6;position:sticky;top:0;cursor:pointer;z-index:2}td:first-child,th:first-child{text-align:left}.freeze1{position:sticky;left:0;background:white;z-index:1;min-width:120px}.freeze2{position:sticky;left:120px;background:white;z-index:1;min-width:90px}.freeze3{position:sticky;left:210px;background:white;z-index:1;min-width:180px}.good{color:#15803d;font-weight:800}.bad{color:#b91c1c;font-weight:800}.pass{color:#15803d;font-weight:900}.fail{color:#b91c1c;font-weight:900}.warn{color:#a16207;font-weight:900}.note{font-size:12px;color:#64748b}.pill{display:inline-block;background:#eef2ff;border:1px solid #c7d2fe;border-radius:999px;padding:4px 8px;margin:2px;font-size:12px;font-weight:700}.state-growth{background:#ecfdf5}.state-value{background:#eff6ff}.state-jive{background:#fefce8}.tradebox{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}.tradeitem{background:#f8fafc;border:1px solid #e5e7eb;border-radius:10px;padding:10px}
</style></head><body><div class="wrap">
<h1>A1V12 Yahoo Production v3.2</h1><div class="sub">Configuration-driven models plus chart audit. Daily calculations retained; chart downsampling is display-only.</div>
<div class="tabs"><button class="tabbtn active" onclick="showTab(event,'overview')">Overview</button><button class="tabbtn" onclick="showTab(event,'tactical')">Tactical Sleeve</button><button class="tabbtn" onclick="showTab(event,'mwm')">MWM Static</button><button class="tabbtn" onclick="showTab(event,'tacticalmodels')">Tactical Models</button><button class="tabbtn" onclick="showTab(event,'signals')">Signals</button><button class="tabbtn" onclick="showTab(event,'holding')">Holding Analytics</button><button class="tabbtn" onclick="showTab(event,'trade')">Trade Log</button><button class="tabbtn" onclick="showTab(event,'chartaudit')">Chart Audit</button><button class="tabbtn" onclick="showTab(event,'audit')">Audit</button><button class="tabbtn" onclick="showTab(event,'allocation')">Allocation</button><button class="tabbtn" onclick="showTab(event,'config')">Config</button></div>
<div class="controls"><b class="note">Period</b><span id="periodButtons"></span><span id="freqPill" class="pill">Display: Daily</span><span class="pill">Metrics use daily rows</span><span class="pill">Drawdown before downsample</span></div>
<section id="overview" class="tab active"><div class="grid kpis" id="kpiBox"></div><div class="grid grid2"><div class="card"><h2>Primary Comparison</h2><div class="controls"><button onclick="preset('core')">Core</button><button onclick="preset('static')">MWM Static</button><button onclick="preset('tacticalmodels')">Tactical Models</button><button onclick="preset('all')">All</button></div><div id="overviewChecks" class="checks"></div><div class="chartbox"><canvas id="overviewChart"></canvas></div><div id="overviewLegend" class="legend"></div></div><div class="card"><h2>Current State & Latest Trade</h2><div id="stateBox"></div><div id="latestTrade"></div></div></div><div class="card"><h2>Sortable Metrics</h2><div class="scroll"><table id="metricsTable"></table></div></div></section>
<section id="tactical" class="tab"><div class="card"><h2>Tactical Sleeve Research</h2><div class="chartbox"><canvas id="tacticalChart"></canvas></div><div id="tacticalLegend" class="legend"></div></div><div class="card"><h2>Tactical Drawdown</h2><div class="chartbox short"><canvas id="tacticalDD"></canvas></div><div id="tacticalDDLegend" class="legend"></div><div class="note">Daily drawdown is computed before chart downsampling.</div></div><div class="card"><h2>Tactical Metrics</h2><div class="scroll"><table id="tacticalMetrics"></table></div></div></section>
<section id="mwm" class="tab"><div class="card"><h2>MWM Static Models</h2><div class="chartbox"><canvas id="mwmChart"></canvas></div><div id="mwmLegend" class="legend"></div></div><div class="card"><h2>MWM Static Metrics</h2><div class="scroll"><table id="mwmMetrics"></table></div></div></section>
<section id="tacticalmodels" class="tab"><div class="card"><h2>Tactical Models</h2><div class="chartbox"><canvas id="tacticalModelsChart"></canvas></div><div id="tacticalModelsLegend" class="legend"></div></div><div class="card"><h2>Tactical Model Metrics</h2><div class="scroll"><table id="tacticalModelsMetrics"></table></div></div></section>
<section id="signals" class="tab"><div class="card"><h2>Recent Signals</h2><div class="scroll"><table id="signalTable"></table></div></div></section>
<section id="holding" class="tab"><div class="card"><h2>Holding Summary</h2><div class="scroll"><table id="holdingSummary"></table></div></div><div class="card"><h2>Holding Period Details</h2><div class="scroll"><table id="holdingPeriods"></table></div></div></section>
<section id="trade" class="tab"><div class="card"><h2>Trade Ledger</h2><div id="latestTrade2"></div><div class="scroll"><table id="tradeTable"></table></div></div></section>
<section id="chartaudit" class="tab"><div class="card"><h2>Chart Audit</h2><div class="scroll"><table id="chartAuditTable"></table></div></div><div class="card"><h2>Chart Rules</h2><table><tr><th>Window</th><th>Display frequency</th><th>Calculation basis</th></tr><tr><td>YTD, 1Y, 2Y</td><td>Daily</td><td>Full daily values</td></tr><tr><td>&gt;2Y and &lt;8Y</td><td>Weekly, last trading observation of week</td><td>Full daily values</td></tr><tr><td>≥8Y or SI</td><td>Monthly, last trading observation of month</td><td>Full daily values</td></tr><tr><td>Drawdown</td><td>Downsample after drawdown is computed</td><td>Daily running peak first</td></tr></table></div></section>
<section id="audit" class="tab"><div class="card"><h2>Metric Window Audit</h2><div id="windowAudit"></div><div class="scroll"><table id="windowRows"></table></div></div><div class="card"><h2>Production Audit</h2><div class="scroll"><table id="prodAuditTable"></table></div></div><div class="card"><h2>Data Audit</h2><div class="scroll"><table id="auditTable"></table></div></div></section>
<section id="allocation" class="tab">
<div class="card">
<h2>Model Allocation</h2>
<div class="controls">
  <b class="note">Model</b><span id="allocModelButtons"></span>
</div>
<div class="grid grid2">
  <div>
    <div class="chartbox"><canvas id="allocPie"></canvas></div>
    <div id="allocLegend" class="legend"></div>
  </div>
  <div class="scroll"><table id="allocTable"></table></div>
</div>
</div>
</section>
<section id="config" class="tab"><div class="card"><h2>Backfill Scale Audit</h2><div class="note">Backfilled series are ratio-scaled to prevent artificial jumps at live/backfill transition dates.</div><div class="scroll"><table id="backfillAuditTable"></table></div></div><div class="card"><h2>Static to Tactical Model Map</h2><div class="scroll"><table id="modelMapTable"></table></div></div><div class="card"><h2>Normalized Allocation Config</h2><div class="scroll"><table id="allocationTable"></table></div></div></section>
</div><script>
const EMBEDDED=__PAYLOAD__;
const colors=['#6d35c4','#15803d','#0057b8','#e11d1d','#17365d','#a16207','#0f766e','#1d4ed8','#be123c','#7c3aed','#2563eb','#ea580c'];
const STR=new Set(['Date','Trade_Date','Trigger_Date','Start','End','Start_Date','End_Date','Asset','Production_Asset','State','EffectiveHolding','From','To','New_State','Rule','Status','Yahoo_Symbol','Notes','Check','Detail','Model','Static_Model','Tactical_Model','Chart','Series','Frequency']);
let sortState={}, tableData={}, period='3Y', periods=['YTD','1Y','2Y','3Y','5Y','2018','2016','SI'], visible=[];
function parseCSV(t){if(!t)return[];let L=t.trim().split(/\r?\n/);if(!L[0])return[];let H=L[0].split(',');return L.slice(1).filter(Boolean).map(l=>{let V=[],c='',q=false;for(let i=0;i<l.length;i++){let ch=l[i];if(ch=='"')q=!q;else if(ch==','&&!q){V.push(c);c=''}else c+=ch}V.push(c);let o={};H.forEach((h,i)=>{let v=V[i]??'',n=parseFloat(v);o[h]=(!STR.has(h)&&!isNaN(n)&&v.trim()!=='')?n:v});return o})}
let tactical=parseCSV(EMBEDDED.tactical), portfolio=parseCSV(EMBEDDED.portfolio), signals=parseCSV(EMBEDDED.signals), trades=parseCSV(EMBEDDED.trades), holdsum=parseCSV(EMBEDDED.holdsum), holdperiods=parseCSV(EMBEDDED.holdperiods), audit=parseCSV(EMBEDDED.dataaudit), prodaudit=parseCSV(EMBEDDED.prodaudit), modelmap=parseCSV(EMBEDDED.modelmap), alloc=parseCSV(EMBEDDED.alloc), backfillaudit=parseCSV(EMBEDDED.backfillaudit);
/* Risk-free series (BIL Buy Hold) used for true Sharpe ratio calculations. */
let bilSeries=tactical.filter(r=>isFinite(r['BIL Buy Hold'])).map(r=>({Date:r.Date,BIL:r['BIL Buy Hold']}));
function riskFreeCAGR(startDateStr,endDateStr){
  if(!bilSeries.length)return 0;
  let start=new Date(startDateStr), end=new Date(endDateStr);
  let inRange=bilSeries.filter(r=>{let dt=new Date(r.Date); return dt>=start&&dt<=end});
  if(inRange.length<2)return 0;
  let yrs=(new Date(inRange.at(-1).Date)-new Date(inRange[0].Date))/86400000/365.25;
  if(!(yrs>0))return 0;
  let ratio=inRange.at(-1).BIL/inRange[0].BIL;
  if(!(ratio>0))return 0;
  return Math.pow(ratio,1/yrs)-1;
}
function money(v){return isFinite(v)?'$'+v.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}):''}function pct(v){return isFinite(v)?(v*100).toFixed(2)+'%':''}function ratio(v){return isFinite(v)?v.toFixed(2):''}function num(v,d=2){return isFinite(v)?v.toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d}):v||''}
function fmt(h,v){if(v==null||v==='')return ''; if(h.includes('Value')||h.includes('Price'))return money(v); if(h.includes('CAGR')||h.includes('Volatility')||h.includes('Drawdown')||h.includes('Return')||h.includes('Pct')||h.includes('Weight')||h.includes('Diff'))return pct(v); if(h.includes('Sharpe')||h.includes('Ratio'))return ratio(v); if(h.includes('Days'))return num(v,1); if(h.includes('Rows')||h.includes('Periods')||h.includes('Count'))return isFinite(v)?Math.round(v).toLocaleString():v; return isFinite(v)?num(v,2):v}
function cls(h,v,row){let out='';if(h=='Status')out=v=='PASS'?'pass':(v=='FAIL'?'fail':'warn'); else if(isFinite(v)){if(h.includes('Drawdown')||h.includes('Worst'))out='bad'; else if(h.includes('CAGR')||h.includes('Sharpe')||h.includes('Return')||h.includes('Best'))out=v>=0?'good':'bad'} if(row&&row.__current)out+=' '+(row.EffectiveHolding=='JIVE'?'state-jive':row.State=='Value'?'state-value':'state-growth');return out}
function sortVal(v){if(v==null||v==='')return null;if(typeof v==='number')return v;let s=String(v);if(/^\d{4}-\d{2}-\d{2}/.test(s)){let d=Date.parse(s);if(!isNaN(d))return d;}let n=parseFloat(s.replace(/[$,%]/g,''));if(!isNaN(n))return n;return s.toLowerCase()}
function sortRows(id,h){let rows=tableData[id]||[],key=id+'|'+h,dir=sortState[key]=='asc'?'desc':'asc';sortState={};sortState[key]=dir;let sorted=[...rows].sort((a,b)=>{let av=sortVal(a[h]),bv=sortVal(b[h]);if(av==null&&bv==null)return 0;if(av==null)return 1;if(bv==null)return -1;if(av<bv)return dir=='asc'?-1:1;if(av>bv)return dir=='asc'?1:-1;return 0});drawTable(id,sorted)}
function drawTable(id,rows,freeze=false){let e=document.getElementById(id);if(!e)return;if(!rows||!rows.length){e.innerHTML='<tr><td class=note>No data</td></tr>';return}tableData[id]=rows;let H=Object.keys(rows[0]).filter(h=>h!='__current');let sk=Object.keys(sortState).find(k=>k.startsWith(id+'|')),active=sk?sk.split('|')[1]:null,dir=sk?sortState[sk]:null;e.innerHTML='<thead><tr>'+H.map((h,i)=>`<th class="${freeze&&i<3?'freeze'+(i+1):''}" onclick="sortRows('${id}','${String(h).replace(/'/g,"\\'")}')">${h}${active==h?(dir=='asc'?' ▲':' ▼'):''}</th>`).join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+H.map((h,i)=>`<td class="${(freeze&&i<3?'freeze'+(i+1)+' ':'')+cls(h,r[h],r)}">${fmt(h,r[h])}</td>`).join('')+'</tr>').join('')+'</tbody>'}
function cols(d){return d.length?Object.keys(d[0]).filter(k=>k!='Date'):[]}
function cut(d){if(!d.length)return[];let end=new Date(d[d.length-1].Date),start=new Date(d[0].Date);if(period=='YTD')start=new Date(end.getFullYear(),0,1);else if(period.endsWith('Y')){start=new Date(end);start.setFullYear(start.getFullYear()-parseInt(period))}else if(period=='2018')start=new Date('2018-01-01');else if(period=='2016')start=new Date('2016-01-01');return d.filter(r=>new Date(r.Date)>=start&&new Date(r.Date)<=end)}
function yearsIn(d){return d.length>1?(new Date(d.at(-1).Date)-new Date(d[0].Date))/86400000/365.25:0}
function displayFrequency(d){let yrs=period==='SI'?99:yearsIn(d); if(period==='YTD'||period==='1Y'||period==='2Y'||yrs<=2.05)return 'Daily'; if(yrs>=8)return 'Monthly'; return 'Weekly'}
function sampleDisplay(d){let freq=displayFrequency(d); if(freq==='Daily')return d; let map=new Map(); d.forEach(r=>{let dt=new Date(r.Date); let key; if(freq==='Monthly'){key=dt.getFullYear()+'-'+String(dt.getMonth()+1).padStart(2,'0')} else {let x=new Date(dt); let day=x.getDay(); let diff=(day+6)%7; x.setDate(x.getDate()-diff); key=x.toISOString().slice(0,10)} map.set(key,r)}); return Array.from(map.values())}
function rebase(d,c){if(!d.length)return[];return d.map(r=>{let o={Date:r.Date};c.forEach(x=>{let f=d.find(z=>isFinite(z[x])&&z[x]>0);o[x]=f?r[x]/f[x]*100000:null});return o})}
function metric(d,c){let out=[];if(d.length<2)return out;let rf=riskFreeCAGR(d[0].Date,d[d.length-1].Date);c.forEach(x=>{let v=d.map(r=>r[x]).filter(isFinite);if(v.length<2)return;let days=(new Date(d[d.length-1].Date)-new Date(d[0].Date))/86400000,yrs=days/365.25,re=[];for(let i=1;i<v.length;i++)re.push(v[i]/v[i-1]-1);let avg=re.reduce((a,b)=>a+b,0)/re.length,sd=Math.sqrt(re.reduce((a,b)=>a+(b-avg)**2,0)/(re.length-1)),cagr=Math.pow(v.at(-1)/v[0],1/yrs)-1,vol=sd*Math.sqrt(252),peak=v[0],dd=0;v.forEach(z=>{peak=Math.max(peak,z);dd=Math.min(dd,z/peak-1)});out.push({Model:x,'Beginning Value':v[0],'Ending Value':v.at(-1),'Total Return':v.at(-1)/v[0]-1,CAGR:cagr,Volatility:vol,Risk_Free_CAGR:rf,'Sharpe (vs BIL)':vol?(cagr-rf)/vol:null,'Max Drawdown':dd,Rows:v.length,Days:days,Start:d[0].Date,End:d[d.length-1].Date})});return out}
function draw(id,dDaily,c,leg,isDD=false){let d=sampleDisplay(dDaily);let cv=document.getElementById(id);if(!cv)return;let box=cv.parentElement,wCss=Math.max(700,box.clientWidth||900),hCss=Math.max(260,box.clientHeight||430),pr=window.devicePixelRatio||1;cv.width=wCss*pr;cv.height=hCss*pr;let ctx=cv.getContext('2d');ctx.setTransform(pr,0,0,pr,0,0);let w=wCss,h=hCss;ctx.clearRect(0,0,w,h);ctx.font='11px Arial';if(!d.length||!c.length){ctx.fillText('No chart data',30,40);return}let vals=[];c.forEach(x=>d.forEach(r=>{if(isFinite(r[x]))vals.push(r[x])}));if(!vals.length){ctx.fillText('No numeric series selected',30,40);return}let mn=Math.min(...vals),mx=Math.max(...vals),pad=(mx-mn)*.08||1;mn-=pad;mx+=pad;let L=90,R=30,T=25,B=55;ctx.strokeStyle='#d7deea';ctx.fillStyle='#334155';for(let i=0;i<5;i++){let y=T+(h-T-B)*i/4;ctx.beginPath();ctx.moveTo(L,y);ctx.lineTo(w-R,y);ctx.stroke();let val=mx-(mx-mn)*i/4;ctx.fillText(isDD?pct(val):money(val),8,y+4)}c.forEach((x,j)=>{ctx.strokeStyle=colors[j%colors.length];ctx.lineWidth=x.includes('VOO')?2.5:2;ctx.beginPath();d.forEach((r,i)=>{let xx=L+(w-L-R)*(d.length===1?0:i/(d.length-1)),yy=T+(h-T-B)*(1-(r[x]-mn)/(mx-mn));i?ctx.lineTo(xx,yy):ctx.moveTo(xx,yy)});ctx.stroke()});let el=document.getElementById(leg);if(el)el.innerHTML=c.map((x,j)=>`<span><i class=sw style="background:${colors[j%colors.length]}"></i>${x}</span>`).join('')}
function dailyDrawdown(d,c){let z=d.map(r=>({Date:r.Date}));c.forEach(x=>{let p=null;z.forEach((o,i)=>{let v=d[i][x];if(!isFinite(v)){o[x]=null;return}p=Math.max(p||v,v);o[x]=v/p-1})});return z}
function chartAuditRows(name,dDaily,cols,drawdown=false){
  /* FIX: previously latestDaily was always read from the raw dDaily (portfolio
     value) series even when auditing a drawdown chart, while latestPlot came
     from the downsampled drawdown series -- comparing dollars against a
     percentage and producing spurious WARN rows. Both sides now come from the
     same basis series. */
  let basis = drawdown ? dailyDrawdown(dDaily,cols) : dDaily;
  let freq=displayFrequency(dDaily), disp=sampleDisplay(basis), rows=[];
  cols.forEach(x=>{
    let vals=basis.map(r=>r[x]).filter(isFinite), latestDaily=basis.length?basis.at(-1)[x]:null, latestPlot=disp.length?disp.at(-1)[x]:null;
    let miss=basis.length-vals.length;
    rows.push({Chart:name,Series:x,Frequency:freq,'Daily Rows':basis.length,'Plotted Rows':disp.length,'Missing Count':miss,'Latest Daily Date':basis.length?basis.at(-1).Date:'','Latest Plot Date':disp.length?disp.at(-1).Date:'','Latest Point Diff':(isFinite(latestDaily)&&isFinite(latestPlot))?latestPlot-latestDaily:null,Status:(miss===0 && (!isFinite(latestDaily)||Math.abs((latestPlot||0)-latestDaily)<1e-8))?'PASS':'WARN'});
  });
  return rows;
}
function staticCols(){return cols(portfolio).filter(x=>x.startsWith('MWM '))}
function tacticalModelCols(){return cols(portfolio).filter(x=>x.startsWith('Tactical '))}
function showTab(e,id){document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));document.getElementById(id).classList.add('active');document.querySelectorAll('.tabbtn').forEach(b=>b.classList.remove('active'));e.target.classList.add('active');setTimeout(render,80);setTimeout(renderAllocation,80)}
function setPeriod(p){period=p;sortState={};document.querySelectorAll('#periodButtons button').forEach(b=>b.classList.toggle('active',b.textContent==p));render()}
function preset(p){let all=cols(portfolio);visible=p=='core'?['A1V12 Tactical Sleeve','VOO Benchmark']:p=='static'?staticCols():p=='tacticalmodels'?tacticalModelCols():all;document.getElementById('overviewChecks').innerHTML=all.map(x=>`<label><input type=checkbox ${visible.includes(x)?'checked':''} onchange="tog('${x}',this.checked)"> ${x}</label>`).join('');render()}
function tog(x,on){if(on&&!visible.includes(x))visible.push(x);if(!on)visible=visible.filter(y=>y!=x);render()}
function recentSignals(){return [...signals].reverse().slice(0,60).map((r,i)=>({...r,__current:i==0}))}
function render(){let d=cut(portfolio),rb=rebase(d,visible),m=metric(rb,visible);document.getElementById('freqPill').innerHTML='Display: '+displayFrequency(d);draw('overviewChart',rb,visible,'overviewLegend');drawTable('metricsTable',m);document.getElementById('kpiBox').innerHTML=m.slice(0,4).map(r=>`<div class=kpi><div class=label>${r.Model}</div><div class=big>${money(r['Ending Value'])}</div><div class=note>Total <span class=good>${pct(r['Total Return'])}</span> | CAGR <span class=good>${pct(r.CAGR)}</span></div></div>`).join('');document.getElementById('windowAudit').innerHTML=d.length?`<b>${period}</b><br>Start: ${d[0].Date}<br>End: ${d.at(-1).Date}<br>Daily rows: ${d.length}<br>Display frequency: ${displayFrequency(d)}<br>Display rows: ${sampleDisplay(d).length}`:'No data';drawTable('windowRows',m);let tc=cols(tactical).filter(x=>['A1V12','A1V12-XMGV','A1V12-XJIVE','VOO Benchmark'].includes(x));let td=rebase(cut(tactical),tc),tm=metric(td,tc);draw('tacticalChart',td,tc,'tacticalLegend');draw('tacticalDD',dailyDrawdown(td,tc),tc,'tacticalDDLegend',true);drawTable('tacticalMetrics',tm);let sd=rebase(cut(portfolio),staticCols());draw('mwmChart',sd,staticCols(),'mwmLegend');drawTable('mwmMetrics',metric(sd,staticCols()));let tmd=rebase(cut(portfolio),tacticalModelCols());draw('tacticalModelsChart',tmd,tacticalModelCols(),'tacticalModelsLegend');drawTable('tacticalModelsMetrics',metric(tmd,tacticalModelCols()));state();drawTable('signalTable',recentSignals(),true);let auditRows=[...chartAuditRows('Overview',rb,visible,false),...chartAuditRows('Tactical Growth',td,tc,false),...chartAuditRows('Tactical Drawdown',td,tc,true),...chartAuditRows('MWM Static',sd,staticCols(),false),...chartAuditRows('Tactical Models',tmd,tacticalModelCols(),false)];drawTable('chartAuditTable',auditRows)}
function state(){let s=signals.at(-1)||{},tr=trades.at(-1)||{};let stateHtml=`<div class=tradebox><div class=tradeitem><div class=label>Current State</div><div class=big>${s.State||'N/A'}</div></div><div class=tradeitem><div class=label>Current Holding</div><div class=big>${s.EffectiveHolding||'N/A'}</div></div><div class=tradeitem><div class=label>Latest Signal Date</div><div class=big>${s.Date||'N/A'}</div></div><div class=tradeitem><div class=label>MGK/MGV</div><div class=big>${ratio(s.MGK_MGV)}</div></div></div>`;let tradeHtml=`<div class=tradebox style="margin-top:10px"><div class=tradeitem><div class=label>Trigger Date</div><div class=big>${tr.Trigger_Date||'N/A'}</div></div><div class=tradeitem><div class=label>Trade Date</div><div class=big>${tr.Trade_Date||'N/A'}</div></div><div class=tradeitem><div class=label>Latest Trade</div><div class=big>${tr.From||''} → ${tr.To||''}</div></div><div class=tradeitem><div class=label>Rule</div><div class=note>${tr.Rule||'N/A'}</div></div></div>`;document.getElementById('stateBox').innerHTML=stateHtml;document.getElementById('latestTrade').innerHTML=tradeHtml;document.getElementById('latestTrade2').innerHTML=tradeHtml}
function staticTables(){drawTable('tradeTable',trades.slice().reverse());drawTable('holdingSummary',holdsum);drawTable('holdingPeriods',holdperiods.slice().reverse());drawTable('auditTable',audit);drawTable('prodAuditTable',prodaudit);drawTable('modelMapTable',modelmap);drawTable('allocationTable',alloc);drawTable('backfillAuditTable',backfillaudit)}

/* --- Allocation tab: pie chart + labels + legend + table --- */
let allocModel = null;
function allocModels(){return [...new Set(alloc.map(r=>r.Model))]}
function allocForModel(m){
  let rows = alloc.filter(r=>r.Model===m);
  let byAsset = {};
  rows.forEach(r=>{byAsset[r.Production_Asset]=(byAsset[r.Production_Asset]||0)+Number(r.Weight)});
  return Object.entries(byAsset).map(([Asset,Weight])=>({Asset,Weight})).sort((a,b)=>b.Weight-a.Weight);
}
function setAllocModel(m){
  allocModel=m;
  document.querySelectorAll('#allocModelButtons button').forEach(b=>b.classList.toggle('active',b.textContent===m));
  renderAllocation();
}
function drawPie(id,rows){
  let cv=document.getElementById(id); if(!cv) return;
  let box=cv.parentElement, wCss=Math.max(300,box.clientWidth||400), hCss=Math.max(260,box.clientHeight||400), pr=window.devicePixelRatio||1;
  cv.width=wCss*pr; cv.height=hCss*pr;
  let ctx=cv.getContext('2d'); ctx.setTransform(pr,0,0,pr,0,0);
  ctx.clearRect(0,0,wCss,hCss);
  ctx.font='11px Arial';
  if(!rows.length){ctx.fillText('No allocation data',30,40); return}
  let total=rows.reduce((a,r)=>a+r.Weight,0);
  if(!total){ctx.fillText('Allocation weights sum to zero',30,40); return}
  let cx=wCss/2, cy=hCss/2, r=Math.min(wCss,hCss)/2-20, start=-Math.PI/2;
  rows.forEach((row,i)=>{
    let slice=(row.Weight/total)*2*Math.PI;
    ctx.beginPath();
    ctx.moveTo(cx,cy);
    ctx.arc(cx,cy,r,start,start+slice);
    ctx.closePath();
    ctx.fillStyle=colors[i%colors.length];
    ctx.fill();
    ctx.strokeStyle='#ffffff';
    ctx.lineWidth=1.5;
    ctx.stroke();
    if(slice>0.14){
      let mid=start+slice/2;
      let lx=cx+Math.cos(mid)*r*0.65, ly=cy+Math.sin(mid)*r*0.65;
      ctx.fillStyle='#ffffff';
      ctx.font='bold 12px Arial';
      ctx.textAlign='center';
      ctx.fillText(pct(row.Weight/total),lx,ly+4);
      ctx.font='11px Arial';
    }
    start+=slice;
  });
  ctx.textAlign='left';
}
function renderAllocation(){
  if(!allocModel){ if(allocModels().length){allocModel=allocModels()[0]} else {return} }
  let rows=allocForModel(allocModel);
  let total=rows.reduce((a,r)=>a+r.Weight,0)||1;
  drawPie('allocPie',rows);
  let el=document.getElementById('allocLegend');
  if(el)el.innerHTML=rows.map((r,j)=>`<span><i class=sw style="background:${colors[j%colors.length]}"></i>${r.Asset} (${pct(r.Weight/total)})</span>`).join('');
  drawTable('allocTable',rows.map(r=>({Asset:r.Asset,Weight:r.Weight})));
}

function init(){document.getElementById('periodButtons').innerHTML=periods.map(p=>`<button onclick="setPeriod('${p}')" class="${p==period?'active':''}">${p}</button>`).join('');preset('core');staticTables();document.getElementById('allocModelButtons').innerHTML=allocModels().map(m=>`<button onclick="setAllocModel('${m}')">${m}</button>`).join('');if(allocModels().length)setAllocModel(allocModels()[0]);setTimeout(render,120);setTimeout(renderAllocation,120)}
window.addEventListener('resize',()=>setTimeout(()=>{render();renderAllocation()},120));init();
</script></body></html>"""

def main():
    backup = backup_existing_outputs()
    print("Backup folder:", backup)
    alloc_df = read_allocations()
    static_models, tactical_models = build_model_configs(alloc_df)
    required_assets = set(alloc_df["Production_Asset"].unique()) | {"MGK","MGV","JIVE","VOO","BIL"}
    wide, data_audit_df = download_prices(required_assets)
    comp = build_composites(wide, required_assets)
    sig, trades = build_signals(comp)
    tv = build_tactical_values(comp, sig)
    pv = build_portfolios(comp, tv, static_models, tactical_models)
    build_holding_analytics(sig)
    run_audit(alloc_df, static_models, tactical_models, comp, sig, trades, tv, pv, data_audit_df)
    dash = build_dashboard()
    print("\nA1V12 Yahoo Production v3.2 complete.")
    print("Static models:", ", ".join(static_models.keys()))
    print("Tactical models:", ", ".join(tactical_models.keys()))
    print("Latest data date:", pv["Date"].max())
    print("Dashboard:", dash)

if __name__ == "__main__":
    main()
