
import sqlite3
import pandas as pd
import yfinance as yf
import numpy as np

def calculate_atr(df, period=14):
    high_low = df['High'] - df['Low']
    high_close = np.abs(df['High'] - df['Close'].shift())
    low_close = np.abs(df['Low'] - df['Close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    return true_range.rolling(period).mean()

def update_market_database(bhavcopy_path):
    print("Building market_data.db...")
    df = pd.read_csv(bhavcopy_path, low_memory=False)
    df.columns = df.columns.astype(str).str.strip().str.upper().str.replace("_", "")
    
    sym_col = next((c for c in df.columns if 'SYMB' in c), None)
    symbols = df[sym_col].dropna().unique().tolist()
    symbols = [s for s in symbols if s not in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]]
    
    yf_tickers = " ".join([f"{sym}.NS" for sym in symbols])
    data = yf.download(yf_tickers, period="3mo", progress=False, threads=True)
    
    records = []
    closes = data['Close']
    highs = data['High']
    lows = data['Low']
    
    for sym in symbols:
        yf_sym = f"{sym}.NS"
        try:
            if isinstance(closes, pd.DataFrame) and yf_sym in closes.columns:
                sym_close = closes[yf_sym].dropna()
                sym_high = highs[yf_sym].dropna()
                sym_low = lows[yf_sym].dropna()
            else:
                continue
                
            if len(sym_close) < 20: continue
                
            spot_price = sym_close.iloc[-1]
            ema_20 = sym_close.ewm(span=20, adjust=False).mean().iloc[-1]
            
            df_calc = pd.DataFrame({'High': sym_high, 'Low': sym_low, 'Close': sym_close})
            atr_14 = calculate_atr(df_calc).iloc[-1]
            
            records.append((sym, spot_price, atr_14, 1.0, 30, ema_20))
        except Exception:
            continue

    conn = sqlite3.connect('market_data.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS market_context (
        Symbol TEXT PRIMARY KEY, Spot_Price REAL, ATR_14 REAL, Beta REAL, Days_To_Event INTEGER, EMA_20 REAL)''')
    cursor.execute('DELETE FROM market_context')
    cursor.executemany('INSERT INTO market_context VALUES (?, ?, ?, ?, ?, ?)', records)
    conn.commit()
    conn.close()
    print("Database built successfully.")

if __name__ == "__main__":
    update_market_database("BhavCopy.csv")
