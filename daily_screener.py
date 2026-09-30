import os
import io
import requests
import datetime
import yfinance as yf
import pandas as pd
import numpy as np
import time
import gc

# --- CONFIGURATION ---
CAPITAL = 1000000                   # ₹10,00,000 Base Capital
RISK_PER_TRADE = 0.0125             # 1.25% Max Account Risk (₹12,500)
TOP_N_PICKS = 5                     # Display & alert the top 5 highest-scoring setups

def send_telegram(message):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if bot_token and chat_id:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code != 200:
                print(f"Telegram API Error: {response.text}")
        except Exception as e:
            print(f"Failed to send Telegram message: {e}")
    else:
        print("Telegram tokens not found. Skipping message alert.")

# 1. Dynamically Load Official NSE Universe
def get_live_nse_universe(series_filter: str = "EQ") -> list:
    """
    Streams official NSE listed equities directly into memory.
    Filters for standard common equity (EQ) and formats for Yahoo Finance (.NS).
    """
    url = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    try:
        session = requests.Session()
        # Hit homepage first to set required Cloudflare/Akamai cookies
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        response = session.get(url, headers=headers, timeout=10)

        if response.status_code != 200:
            raise RuntimeError(f"HTTP Status: {response.status_code}")

        df = pd.read_csv(io.StringIO(response.text))
        df.columns = df.columns.str.strip()

        if "SYMBOL" in df.columns and "SERIES" in df.columns:
            filtered_df = df[df["SERIES"].str.strip() == series_filter]
            universe = [f"{sym.strip()}.NS" for sym in filtered_df["SYMBOL"].dropna().unique()]
            if universe:
                print(f"Successfully loaded {len(universe)} live equities from NSE master list.")
                return universe

    except Exception as e:
        print(f"Warning: Failed to fetch live NSE master list ({e}). Falling back to curated basket.")

    # Fallback to high-liquidity default basket if NSE blocks or fails
    return [
        "VOLTAMP.NS", "PGIL.NS", "KTKBANK.NS", "KARURVYSYA.NS", "BHARATFORG.NS",
        "RAJRATAN.NS", "MAHABANK.NS", "DATAPATTNS.NS", "HAL.NS", "BEL.NS",
        "DIXON.NS", "HEG.NS", "GAEL.NS", "TRENT.NS", "POLYCAB.NS", "MTARTECH.NS"
    ]

UNIVERSE = get_live_nse_universe("EQ")

# 2. Macro Market Regime Check (Nifty 50) - Non-Blocking Warning Mode
print("Checking macro market regime (^NSEI)...")
bench = yf.download("^NSEI", period="1y", auto_adjust=False, progress=False)
if isinstance(bench.columns, pd.MultiIndex):
    bench.columns = bench.columns.get_level_values(0)

bench['EMA50'] = bench['Close'].ewm(span=50, adjust=False).mean()
bench_bullish = bool(bench['Close'].iloc[-1] > bench['EMA50'].iloc[-1])
bench_6m_ret = float(bench['Close'].iloc[-1] / bench['Close'].iloc[-120])

if not bench_bullish:
    warning_msg = f"⚠️ **MARKET CAUTION WARNING ({datetime.date.today()})**\n\nNifty 50 is **BELOW its 50 EMA**. (Cash protection mode bypassed by user request from UC Hunter Pro ).\nContinuing stock hunt anyway..."
    print(f"\n{warning_msg.replace('**', '').replace('**', '')}\n")
    send_telegram(warning_msg)
else:
    print("Market regime is BULLISH. Proceeding with scan...\n")

# 3. Batch Screening & Quantitative Scoring
candidates = []
BATCH_SIZE = 25  # Safe batch size to prevent OOM/rate limits

print(f"Starting scan across {len(UNIVERSE)} tickers in batches of {BATCH_SIZE}...")

for i in range(0, len(UNIVERSE), BATCH_SIZE):
    batch = UNIVERSE[i:i+BATCH_SIZE]
    try:
        data = yf.download(batch, period="1y", auto_adjust=False, group_by='ticker', progress=False)
        
        for ticker in batch:
            try:
                if len(batch) > 1:
                    if hasattr(data, "columns") and isinstance(data.columns, pd.MultiIndex):
                        if ticker in data.columns.levels[0]:
                            df = data[ticker].dropna(how='all')
                        else:
                            continue
                    else:
                        df = data.dropna(how='all')
                else:
                    df = data.dropna(how='all')

                if df.empty or len(df) < 120:
                    continue
                    
                df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()
                df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()
                df['EMA200'] = df['Close'].ewm(span=200, adjust=False).mean()
                df['Vol_SMA20'] = df['Volume'].rolling(20).mean()
                df['Pivot20_High'] = df['High'].shift(1).rolling(20).max()
                
                delta = df['Close'].diff()
                gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
                loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
                df['RSI'] = 100 - (100 / (1 + (gain / loss.replace(0, np.nan))))
                df['RSI_MA'] = df['RSI'].rolling(9).mean()
                
                tr = pd.concat([
                    df['High'] - df['Low'],
                    (df['High'] - df['Close'].shift(1)).abs(),
                    (df['Low'] - df['Close'].shift(1)).abs()
                ], axis=1).max(axis=1)
                df['ATR'] = tr.rolling(14).mean()
                
                last = df.iloc[-1]
                
                # --- Hard Filters ---
                c_trend = (last['Close'] > last['EMA20']) and (last['EMA20'] > last['EMA50']) and (last['EMA50'] > last['EMA200'])
                c_slope = last['EMA200'] > df['EMA200'].iloc[-20]
                stock_6m_ret = last['Close'] / df['Close'].iloc[-120]
                c_rs = stock_6m_ret > bench_6m_ret
                c_vol = last['Volume'] >= (1.3 * last['Vol_SMA20'])
                c_rsi = (last['RSI'] >= 58.0) and (last['RSI'] <= 72.0) and (last['RSI'] > last['RSI_MA'])
                c_breakout = last['Close'] > last['Pivot20_High']
                c_not_extended = ((last['Close'] - last['EMA200']) / last['EMA200']) < 0.35
                
                if c_trend and c_slope and c_rs and c_vol and c_rsi and c_breakout and c_not_extended:
                    # 4-Pillar Composite Ranking
                    rs_score = min((stock_6m_ret / bench_6m_ret) * 30, 35)
                    vol_mult = last['Volume'] / last['Vol_SMA20']
                    vol_score = min(vol_mult * 10, 25)
                    candle_range = max(last['High'] - last['Low'], 0.01)
                    close_qual = ((last['Close'] - last['Low']) / candle_range) * 20
                    pivot_clear = min(((last['Close'] - last['Pivot20_High']) / last['ATR']) * 10, 20)
                    total_score = round(rs_score + vol_score + close_qual + pivot_clear, 1)
                    
                    # Position Sizing & Target Logic
                    stop_loss = max(last['Low'] - (0.5 * last['ATR']), last['Close'] * 0.935)
                    risk_per_share = last['Close'] - stop_loss
                    shares = int((CAPITAL * RISK_PER_TRADE) / risk_per_share) if risk_per_share > 0 else 0
                    dyn_t1 = max(0.10, (2.5 * last['ATR']) / last['Close'])
                    target_1 = last['Close'] * (1 + dyn_t1)
                    
                    candidates.append({
                        "Ticker": ticker.replace(".NS", ""),
                        "Score": total_score,
                        "LTP": round(last['Close'], 2),
                        "Stop": round(stop_loss, 2),
                        "Risk_Pct": round(((last['Close'] - stop_loss) / last['Close']) * 100, 1),
                        "Shares": shares,
                        "Capital_Req": round(shares * last['Close'], 0),
                        "Target_1": round(target_1, 2),
                        "T1_Gain_Pct": round(dyn_t1 * 100, 1),
                        "Vol_Mult": round(vol_mult, 1)
                    })
            except Exception:
                continue
    except Exception as e:
        print(f"Skipped batch due to error: {e}")
    
    time.sleep(1)
    gc.collect()

# 4. Save Database (CSV) & Format Telegram / Console Outputs
print("\n" + "="*60)
if candidates:
    df_ranked = pd.DataFrame(candidates).sort_values(by="Score", ascending=False).head(TOP_N_PICKS)
    
    # Save/Append to Database (screening_history.csv)
    df_ranked['Date'] = str(datetime.date.today())
    cols = ['Date', 'Ticker', 'Score', 'LTP', 'Stop', 'Risk_Pct', 'Shares', 'Capital_Req', 'Target_1', 'T1_Gain_Pct', 'Vol_Mult']
    df_ranked_db = df_ranked[[c for c in cols if c in df_ranked.columns]]
    
    history_file = 'screening_history.csv'
    if os.path.exists(history_file):
        df_history = pd.read_csv(history_file)
        df_combined = pd.concat([df_history, df_ranked_db]).drop_duplicates(subset=['Date', 'Ticker'], keep='last')
    else:
        df_combined = df_ranked_db
    df_combined.to_csv(history_file, index=False)
    print("Database Updated: Successfully recorded today's setups into screening_history.csv")

    # Build Console Output
    console_lines = [f"🚀 TOP {len(df_ranked)} SWING SETUPS ({datetime.date.today()})\n"]
    for idx, row in df_ranked.reset_index(drop=True).iterrows():
        console_lines.append(
            f"{idx + 1}. Ticker: {row['Ticker']} | Score: {row['Score']}/100 | Volume Mult: {row['Vol_Mult']}x\n"
            f"   • Buy Zone (LTP): ₹{row['LTP']} (CNC Order)\n"
            f"   • Stop Loss: ₹{row['Stop']} (-{row['Risk_Pct']}%)\n"
            f"   • Order Size: {row['Shares']} shares (₹{row['Capital_Req']:,.0f})\n"
            f"   • Tranche 1 Target: ₹{row['Target_1']} (+{row['T1_Gain_Pct']}%)\n"
        )
    console_lines.append("Action: Review & execute delivery orders between 3:20 PM and 3:28 PM IST.")
    print("\n".join(console_lines))
    
    # Build HTML Telegram Output (Includes exact Entry, Stop Loss, and Targets)
    tg_lines = [f"🚀 **TOP {len(df_ranked)} SWING SETUPS ({datetime.date.today()})**\n"]
    for idx, row in df_ranked.reset_index(drop=True).iterrows():
        tg_lines.append(
            f"**{idx + 1}. {row['Ticker']}** (Score: **{row['Score']}/100** | Vol: **{row['Vol_Mult']}x**)\n"
            f"  • **Buy Zone (LTP):** ₹{row['LTP']} (CNC)\n"
            f"  • **Stop Loss:** ₹{row['Stop']} (-{row['Risk_Pct']}%)\n"
            f"  • **Order Size:** **{row['Shares']} shares** (₹{row['Capital_Req']:,.0f})\n"
            f"  • **Tranche 1 Target:** ₹{row['Target_1']} (+{row['T1_Gain_Pct']}%)\n"
        )
    tg_lines.append("*Action: Review & execute delivery orders between 3:20 PM and 3:28 PM IST.*")
    
    send_telegram("\n".join(tg_lines))
else:
    no_setup_msg = f"⚪ **Daily Scan ({datetime.date.today()}):** No setups met all quality filters today."
    print(no_setup_msg.replace("**", "").replace("**", ""))
    send_telegram(no_setup_msg)
print("="*60)
