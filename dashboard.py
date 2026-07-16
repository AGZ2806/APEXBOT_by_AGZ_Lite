"""
Pairs Trader Dashboard — Streamlit
Reads live data from data/pairs_signals.json
and trade history from data/trades_pairs.csv
"""
import streamlit as st
import pandas as pd
import time
import os
import yaml
import collections
from io import StringIO
import json

try:
    import plotly.express as px
    import plotly.graph_objects as go
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

# Page Config
st.set_page_config(
    page_title="Pairs Trader Dashboard",
    page_icon="⚖️",
    layout="wide",
)

# Constants
CONFIG_FILE = "phase23.yaml"
TRADES_FILE = "data/trades_pairs.csv"
SIGNALS_FILE = "data/pairs_signals.json"

# Try to connect to SHM
shm_reader = None
try:
    from phase23_shm import SharedMemoryManager, SIGNALS_SHM_NAME, SIGNALS_SHM_SIZE
    shm_reader = SharedMemoryManager(is_writer=False, name=SIGNALS_SHM_NAME, size=SIGNALS_SHM_SIZE)
except Exception:
    pass

# --- Helper Functions ---

@st.cache_data(ttl=1)
def load_trades():
    """Load trades from CSV."""
    if not os.path.exists(TRADES_FILE):
        return pd.DataFrame()
    try:
        with open(TRADES_FILE, "r") as f:
            header = f.readline()
            lines = collections.deque(f, maxlen=500)
        if not lines:
            return pd.DataFrame()
        content = header + "".join(lines)
        df = pd.read_csv(StringIO(content))
        if "timestamp" in df.columns:
            df["datetime"] = pd.to_datetime(df["timestamp"])
        return df
    except Exception:
        return pd.DataFrame()

def compute_stats(df):
    """Compute trading statistics from trade log."""
    if df.empty:
        return {}

    if "action" not in df.columns:
        return {"total_trades": 0}

    df = df.sort_values("datetime").reset_index(drop=True)
    is_entry = df["action"].str.startswith("ENTRY")
    entries = df[is_entry].copy()
    exits = df[~is_entry].copy()

    if exits.empty:
        return {"total_trades": 0}

    # Calculate hold time by finding the most recent entry before each exit
    hold_times = []
    for idx, exit_row in exits.iterrows():
        prior_entries = entries[entries["datetime"] <= exit_row["datetime"]]
        if not prior_entries.empty:
            last_entry = prior_entries.iloc[-1]
            hold_sec = (exit_row["datetime"] - last_entry["datetime"]).total_seconds()
            hold_times.append(hold_sec / 3600.0)
        else:
            hold_times.append(0.0)
    
    exits["hold_time_hours"] = hold_times

    exits["pnl"] = pd.to_numeric(exits["pnl"], errors="coerce").fillna(0)
    wins = exits[exits["pnl"] > 0]
    losses = exits[exits["pnl"] <= 0]

    total_pnl = exits["pnl"].sum()
    win_rate = len(wins) / len(exits) * 100 if len(exits) > 0 else 0
    avg_win = wins["pnl"].mean() if len(wins) > 0 else 0
    avg_loss = losses["pnl"].mean() if len(losses) > 0 else 0

    exits = exits.sort_values("datetime")
    exits["cumulative_pnl"] = exits["pnl"].cumsum()

    peak = exits["cumulative_pnl"].cummax()
    drawdown = (exits["cumulative_pnl"] - peak)
    max_dd = drawdown.min() if len(drawdown) > 0 else 0

    return {
        "total_trades": len(exits),
        "entries": len(entries),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "max_dd": max_dd,
        "exits": exits,
    }

def tail_log(filename, lines=30):
    filepath = filename
    if not os.path.exists(filepath):
        return ["Log file not found."]
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.readlines()[-lines:]
    except Exception as e:
        return [f"Error reading log: {e}"]

# --- Sidebar ---
REFRESH_PRESETS = {"⚡ Realtime (1s)": 1, "🔄 Fast (3s)": 3, "📊 Normal (10s)": 10,
                   "🐢 Slow (30s)": 30, "💤 Power Save (60s)": 60}
with st.sidebar:
    st.header("⚙️ Settings")
    auto_refresh = st.checkbox("Auto-refresh", value=True)
    refresh_preset = st.radio("Refresh Speed", list(REFRESH_PRESETS.keys()), index=1, horizontal=True)
    refresh_rate = REFRESH_PRESETS[refresh_preset]

# --- Title ---
st.title("⚖️ Pairs Trader Dashboard")

# Load data
df = load_trades()
stats = compute_stats(df)

sig_data = {}
if shm_reader:
    data = shm_reader.read()
    if data:
        sig_data = data
else:
    if os.path.exists(SIGNALS_FILE):
        try:
            with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
                sig_data = json.load(f)
        except Exception:
            pass

# --- Tabs ---
tab_signals, tab_performance, tab_trades, tab_config = st.tabs(
    ["📊 Signals & Live Tracking", "📈 Performance", "📜 Trades", "⚙️ Config"]
)

# ═══════════════════════════════════════════════════════
# Tab 1: Signals & Live Tracking
# ═══════════════════════════════════════════════════════
with tab_signals:
    if sig_data:
        sig_age = time.time() - sig_data.get("ts", 0)
        color = "normal" if sig_age < 300 else "off"
        
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("Heartbeat", f"{sig_age:.0f}s ago", delta_color=color)
        with c2:
            st.metric("Status", sig_data.get("status", "UNKNOWN"))
        with c3:
            st.metric(f"{sig_data.get('sym_a', 'Leg 1').split('/')[0]} Price", f"${sig_data.get('mark_a', 0):.2f}")
        with c4:
            st.metric(f"{sig_data.get('sym_b', 'Leg 2').split('/')[0]} Price", f"${sig_data.get('mark_b', 0):.2f}")
            
        st.divider()

        c1, c2, c3, c4 = st.columns(4)
        z = sig_data.get("z_score", 0)
        with c1:
            st.metric("Live Z-Score", f"{z:+.2f}", delta=f"{z:+.2f}", delta_color="normal" if abs(z) < 3.0 else "inverse")
        with c2:
            st.metric("Current Ratio", f"{sig_data.get('current_ratio', 0):.5f}")
        with c3:
            st.metric("24h Mean Ratio", f"{sig_data.get('mean_ratio', 0):.5f}")
        with c4:
            st.metric("Std Deviation", f"{sig_data.get('std_ratio', 0):.5f}")

        # Render Z-Score Chart
        history = sig_data.get("ratio_history", [])
        if history and HAS_PLOTLY:
            mean = sig_data.get("mean_ratio", 0)
            std = sig_data.get("std_ratio", 1)
            
            z_history = [(r - mean) / std for r in history] if std > 0 else []
            
            fig = go.Figure()
            fig.add_trace(go.Scatter(y=z_history, mode="lines", name="Z-Score", line=dict(color="#00E5FF", width=2)))
            
            # Read Z threshold from live signal data if available, fallback to 3.5
            z_thresh = sig_data.get("z_threshold", 3.5)

            fig.add_hline(y=0, line_dash="dash", line_color="white", annotation_text="Mean Reversion (0)")
            fig.add_hline(y=z_thresh, line_dash="dot", line_color="red", annotation_text=f"Short Spread Entry (+{z_thresh})")
            fig.add_hline(y=-z_thresh, line_dash="dot", line_color="green", annotation_text=f"Long Spread Entry (-{z_thresh})")

            fig.update_layout(
                title=f"Spread Z-Score Convergence — {sig_data.get('sym_a', '')} vs {sig_data.get('sym_b', '')}",
                yaxis_title="Z-Score",
                xaxis_title="Time (5-min intervals)",
                height=450,
                template="plotly_dark",
            )
            st.plotly_chart(fig, width="stretch")
            
        if sig_data.get("open_position"):
            st.markdown("### 🟢 Active Position")
            direction = "LONG SPREAD" if sig_data.get("position_direction", 0) > 0 else "SHORT SPREAD"
            st.info(f"**{direction}** — Waiting for mean reversion to **{sig_data.get('entry_mean_ratio', 0):.5f}**")
    else:
        st.warning("Waiting for data. Pairs Trader needs to run for at least 5 minutes to dump the first signal snapshot.")

# ═══════════════════════════════════════════════════════
# Tab 2: Performance
# ═══════════════════════════════════════════════════════
with tab_performance:
    st.subheader("📈 Statistical Arbitrage Performance")
    
    if stats.get("total_trades", 0) > 0:
        s1, s2, s3, s4, s5 = st.columns(5)
        with s1:
            st.metric("Total Trades", stats["total_trades"])
        with s2:
            st.metric("Win Rate", f"{stats['win_rate']:.1f}%")
        with s3:
            pnl = stats["total_pnl"]
            st.metric("Net PnL", f"${pnl:+.2f}", delta=f"${pnl:+.2f}", delta_color="normal" if pnl >= 0 else "inverse")
        with s4:
            wl_ratio = abs(stats["avg_win"] / stats["avg_loss"]) if stats["avg_loss"] != 0 else 0
            st.metric("W/L Ratio", f"{wl_ratio:.2f}x")
        with s5:
            st.metric("Max Drawdown", f"${stats['max_dd']:.2f}")

        if HAS_PLOTLY:
            st.divider()
            exits = stats["exits"]
            
            # Equity Curve
            st.markdown("### Cumulative PnL")
            fig = px.line(exits, x="datetime", y="cumulative_pnl", title="Equity Curve (Cumulative PnL)")
            fig.add_hline(y=0, line_dash="dash", line_color="gray")
            fig.update_layout(height=400)
            st.plotly_chart(fig, width="stretch")
            
            # Trade Duration vs PnL Scatter Plot
            if "hold_time_hours" in exits.columns:
                st.markdown("### Trade Duration vs Profit/Loss")
                plot_df = exits.copy()
                plot_df["Outcome"] = plot_df["pnl"].apply(lambda x: "Win" if x > 0 else "Loss")
                
                fig_scatter = px.scatter(
                    plot_df, 
                    x="hold_time_hours", 
                    y="pnl", 
                    color="Outcome",
                    title="Time in Trade vs Realized PnL",
                    color_discrete_map={"Win": "mediumseagreen", "Loss": "tomato"},
                    labels={"hold_time_hours": "Hold Time (Hours)", "pnl": "Realized PnL ($)"},
                    hover_data=["action"]
                )
                fig_scatter.add_hline(y=0, line_dash="dash", line_color="white", opacity=0.5)
                fig_scatter.update_layout(height=350)
                st.plotly_chart(fig_scatter, width="stretch")
    else:
        st.info("No trades recorded yet.")

# ═══════════════════════════════════════════════════════
# Tab 3: Trades
# ═══════════════════════════════════════════════════════
with tab_trades:
    st.subheader("📜 Trade History")
    if not df.empty:
        display_df = df.iloc[::-1].head(100)
        st.dataframe(display_df, width="stretch", hide_index=True)

        csv_data = df.to_csv(index=False)
        st.download_button("📥 Download Full CSV", csv_data, "trades_pairs.csv", "text/csv")
    else:
        st.info("No trades recorded yet.")

# ═══════════════════════════════════════════════════════
# Tab 4: Config
# ═══════════════════════════════════════════════════════
with tab_config:
    st.subheader("⚙️ Pairs Strategy Profile")
    # Pull live values from signal file if available
    _z = sig_data.get("z_threshold", 3.5)
    _notional = sig_data.get("notional_per_leg", 150.0)
    _sl = sig_data.get("stop_loss_usd", -15.0)
    _pair = f"{sig_data.get('sym_a','BTC/USDT')} vs {sig_data.get('sym_b','ETH/USDT')}" if sig_data else "BTC/USDT vs ETH/USDT"
    st.json({
        "Pair": _pair,
        "Mode": "Statistical Arbitrage (Mean Reversion)",
        "Z-Score Entry Threshold": f"±{_z}",
        "Take Profit": "Mean Reversion (0.0) + 0.8% Overshoot",
        "Stop Loss Limit": f"${_sl:.2f}",
        "Notional Sizing": f"${_notional:.0f}/leg",
        "Maximum Hold Time": "24 hours",
        "Data Source": "Binance WebSocket L2 Books (100ms)",
        "History Lookback": "24 hours"
    })

# Auto-refresh
if auto_refresh:
    time.sleep(refresh_rate)
    st.rerun()
