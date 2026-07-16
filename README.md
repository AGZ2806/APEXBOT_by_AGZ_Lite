# ApexBot by AGZ (Lite Version)

A high-performance, non-custodial Statistical Arbitrage (Pairs Trading) bot for Binance Futures.

## Overview

ApexBot uses cointegration and Z-Score mean-reversion to trade crypto pairs (e.g., BTC/ETH). 
Because I believe you shouldn't blindly trust `.exe` files with your API keys, I have open-sourced the entire infrastructure here:
- **GUI & Application Layer:** `gui_main.py`
- **Binance WebSocket/REST Connectors:** `phase23_data_collector.py` & `phase23_lib.py`
- **Logging & Dashboards:** `scribe.py` & `dashboard.py`

**Note:** The core proprietary mathematical formulas and thresholds (`trader_pairs.py`) are compiled as a closed-source binary `.pyd` module included in this repository. You can verify that all API calls and key management happen safely in the open-source python files.

## Requirements
- Python 3.10+
- Windows OS (Currently optimized for Windows, Mac coming soon)
- A Binance Futures account

## Installation

1. Clone the repository:
```bash
git clone https://github.com/AGZ2806/APEXBOT_by_AGZ_Lite.git
cd APEXBOT_by_AGZ_Lite
```

2. Install the required packages:
```bash
pip install -r requirements.txt
```

3. Run the bot:
```bash
python gui_main.py
```

## Trust & Security
- **Non-Custodial:** Your API keys are encrypted locally on your machine and only used to connect directly to Binance. We never see them.
- **Open Infrastructure:** You can read the source code to verify exactly how the bot handles your keys and executes trades.

## Verify the Math Yourself (Backtesting Toolkit)
To prove the efficiency of this model, I have included the raw backtesting scripts in the `backtesting_toolkit` folder. You can download historical data from OKX and recreate my performance results on your own machine.

1. Run `python backtesting_toolkit/download_all_okx.py` to download historical market data.
2. Run `python backtesting_toolkit/backtest_pairs.py` to simulate the stat-arb strategy over the downloaded data.
3. Analyze the output to see the Sharpe ratio, max drawdown, and beta-weighted yield.

## Community
Have questions? Want to share your results? Join the discussion on our subreddit: `r/CryptoTradingBot`.
