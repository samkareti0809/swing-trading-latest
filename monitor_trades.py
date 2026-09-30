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

def monitor_performance():
    history_file = 'screening_history.csv'
    if not os.path.exists(history_file):
        print("No screening history found yet.")
        return

    df = pd.read_csv(history_file)
    if df.empty or 'Status' not in df.columns:
        if 'Status' not in df.columns:
            df['Status'] = 'Active'
        df.to_csv(history_file, index=False)
        print("Initialized Status column in history file.")
        return

    active_mask = df['Status'] == 'Active'
    alert_msgs = []

    # 1. Check Active Trades
    if active_mask.any():
        for idx, row in df[active_mask].iterrows():
            ticker = f"{row['Ticker']}.NS"
            entry_date = row['Date']
            entry_price = row['LTP']
            stop_loss = row['Stop']
            target = row['Target_1']
            
            try:
                data = yf.download(ticker, start=entry_date, progress=False)
                if data.empty:
                    continue
                
                if isinstance(data.columns, pd.MultiIndex):
                    data.columns = data.columns.get_level_values(0)

                max_high = data['High'].max()
                min_low = data['Low'].min()
                latest_close = data['Close'].iloc[-1]
                
                # Check Stop Loss
                if min_low <= stop_loss:
                    df.at[idx, 'Status'] = 'Closed (SL Hit)'
                    alert_msgs.append(
                        f"❌ **STOP LOSS HIT: {row['Ticker']}**\n"
                        f"  • Entry Price: ₹{entry_price}\n"
                        f"  • Stop Level: ₹{stop_loss}\n"
                        f"  • Exit / Low: ₹{round(min_low, 2)}"
                    )
                # Check Target 1
                elif max_high >= target:
                    df.at[idx, 'Status'] = 'Closed (Target Hit)'
                    alert_msgs.append(
                        f"🎯 **TARGET REACHED: {row['Ticker']}**\n"
                        f"  • Entry Price: ₹{entry_price}\n"
                        f"  • Target Level: ₹{target}\n"
                        f"  • High Reached: ₹{round(max_high, 2)}"
                    )
                else:
                    pnl_pct = round(((latest_close - entry_price) / entry_price) * 100, 2)
                    print(f"🔄 {row['Ticker']}: Active | Current P&L: {pnl_pct}% (LTP: ₹{round(latest_close, 2)})")

            except Exception as e:
                print(f"Error checking {ticker}: {e}")

    # --- 2. AUTOMATIC DATABASE CLEANUP & ARCHIVING ---
    if not df.empty and 'Date' in df.columns:
        df['Date_Parsed'] = pd.to_datetime(df['Date'], errors='coerce')
        
        # Keep active trades OR trades closed within the last 45 days
        retention_days = 45
        cutoff = pd.Timestamp.today() - pd.Timedelta(days=retention_days)
        
        is_active = df['Status'] == 'Active'
        is_recent_closed = df['Status'].str.contains('Closed', na=False) & (df['Date_Parsed'] >= cutoff)
        
        df_retained = df[is_active | is_recent_closed].copy()
        df_old = df[~(is_active | is_recent_closed)].copy()
        
        if 'Date_Parsed' in df_retained.columns:
            df_retained = df_retained.drop(columns=['Date_Parsed'])
        if 'Date_Parsed' in df_old.columns:
            df_old = df_old.drop(columns=['Date_Parsed'])
            
        # Move old records to archived_history.csv
        if not df_old.empty:
            archive_file = 'archived_history.csv'
            if os.path.exists(archive_file):
                df_arch_existing = pd.read_csv(archive_file)
                df_combined_arch = pd.concat([df_arch_existing, df_old]).drop_duplicates(subset=['Date', 'Ticker'], keep='last')
                df_combined_arch.to_csv(archive_file, index=False)
            else:
                df_old.to_csv(archive_file, index=False)
            print(f"Archived {len(df_old)} old closed trade(s) to {archive_file}")
            
        df_retained.to_csv(history_file, index=False)
    else:
        df.to_csv(history_file, index=False)

    print("Screening history updated and pruned successfully.")

    if alert_msgs:
        send_telegram("\n\n".join(alert_msgs))

if __name__ == "__main__":
    monitor_performance()
