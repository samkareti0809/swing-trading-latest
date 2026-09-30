import pandas as pd
import yfinance as yf
import datetime
import os
import requests

def send_telegram(message):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if bot_token and chat_id:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            print(f"Telegram error: {e}")

def check_intraday_setups():
    history_file = 'screening_history.csv'
    if not os.path.exists(history_file):
        print("No screening history database found.")
        return

    df = pd.read_csv(history_file)
    if df.empty or 'Status' not in df.columns:
        print("No active status columns found in database.")
        return

    active_mask = df['Status'] == 'Active'
    if not active_mask.any():
        print("No active trades in database to monitor.")
        return

    alerts = []
    
    for idx, row in df[active_mask].iterrows():
        ticker = f"{row['Ticker']}.NS"
        entry_price = row['LTP']
        stop_loss = row['Stop']
        target = row['Target_1']
        
        try:
            # Fetch intraday / latest price action
            data = yf.download(ticker, period="1d", interval="15m", progress=False)
            if data.empty:
                data = yf.download(ticker, period="5d", progress=False) # Fallback to daily if intraday unavailable
                
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)

            current_price = float(data['Close'].iloc[-1])
            day_high = float(data['High'].max())
            day_low = float(data['Low'].min())
            
            # Check Stop Loss breach
            if day_low <= stop_loss:
                df.at[idx, 'Status'] = 'Closed (SL Hit)'
                alerts.append(f"❌ **INTRA-DAY STOP LOSS HIT: {row['Ticker']}**\n  • Current LTP: ₹{current_price}\n  • Stop Level: ₹{stop_loss}")
            
            # Check Target reached
            elif day_high >= target:
                df.at[idx, 'Status'] = 'Closed (Target Hit)'
                alerts.append(f"🎯 **INTRA-DAY TARGET REACHED: {row['Ticker']}**\n  • Current LTP: ₹{current_price}\n  • Target Level: ₹{target}")
            else:
                pnl = round(((current_price - entry_price) / entry_price) * 100, 2)
                print(f"[{row['Ticker']}] Active | LTP: ₹{current_price} | P&L: {pnl}%")

        except Exception as e:
            print(f"Error checking {ticker}: {e}")

    df.to_csv(history_file, index=False)
    
    if alerts:
        send_telegram("\n\n".join(alerts))

if __name__ == "__main__":
    check_intraday_setups()
