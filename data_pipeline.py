import sqlite3
import pandas as pd
import yfinance as yf
import numpy as np
import warnings
from datetime import datetime

warnings.filterwarnings('ignore')

def calculate_atr(df, period=14):
    high_low = df['High'] - df['Low']
    high_close = np.abs(df['High'] - df['Close'].shift())
    low_close = np.abs(df['Low'] - df['Close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    return true_range.rolling(period).mean()

def update_market_database(bhavcopy_path):
    print("🚀 Initializing Version 2.0 Local Data Pipeline...")
    
    # 1. Extract Symbols from Bhavcopy
    try:
        if bhavcopy_path.endswith('.zip'):
            import zipfile
            with zipfile.ZipFile(bhavcopy_path, 'r') as z:
                csv_filename = [f for f in z.namelist() if f.endswith('.csv')][0]
                with z.open(csv_filename) as f:
                    bhav_df = pd.read_csv(f)
        else:
            bhav_df = pd.read_csv(bhavcopy_path)
            
        # Adjust header mapping for legacy formats
        bhav_df.columns = bhav_df.columns.astype(str).str.strip().str.upper().str.replace("_", "")
        sym_col = next((c for c in bhav_df.columns if 'SYMB' in c), None)
        symbols = bhav_df[sym_col].dropna().unique().tolist()
        
        # Remove Indices
        symbols = [s for s in symbols if s not in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"]]
        
    except Exception as e:
        print(f"❌ Failed to read symbols from Bhavcopy: {e}")
        return

    # 2. Fetch Data from yfinance
    yf_tickers = " ".join([f"{sym}.NS" for sym in symbols])
    print(f"📡 Fetching historical data for {len(symbols)} symbols...")
    data = yf.download(yf_tickers, period="3mo", progress=True, threads=True)
    
    records = []
    closes = data['Close']
    highs = data['High']
    lows = data['Low']
    
    # Optional: fetch NIFTY for Beta calculation
    nifty_data = yf.download("^NSEI", period="3mo", progress=False)['Close']
    if not nifty_data.empty:
        nifty_returns = nifty_data.pct_change().dropna()
    else:
        nifty_returns = None
        
    for sym in symbols:
        yf_sym = f"{sym}.NS"
        try:
            if isinstance(closes, pd.DataFrame) and yf_sym in closes.columns:
                sym_close = closes[yf_sym].dropna()
                sym_high = highs[yf_sym].dropna()
                sym_low = lows[yf_sym].dropna()
            else:
                continue
                
            if len(sym_close) < 20:
                continue
                
            spot_price = sym_close.iloc[-1]
            ema_20 = sym_close.ewm(span=20, adjust=False).mean().iloc[-1]
            
            # ATR
            df_calc = pd.DataFrame({'High': sym_high, 'Low': sym_low, 'Close': sym_close})
            atr_14 = calculate_atr(df_calc).iloc[-1]
            
            # Beta
            beta = 1.0
            if nifty_returns is not None:
                sym_returns = sym_close.pct_change().dropna()
                aligned_returns = pd.concat([sym_returns, nifty_returns], axis=1).dropna()
                if len(aligned_returns) > 20:
                    cov = np.cov(aligned_returns.iloc[:, 0], aligned_returns.iloc[:, 1])[0][1]
                    var = np.var(aligned_returns.iloc[:, 1])
                    beta = cov / var if var != 0 else 1.0
            
            # Mock Event DTE (Normally fetched from earnings calendar)
            days_to_event = 30 
            
            records.append((sym, spot_price, atr_14, beta, days_to_event, ema_20))
            
        except Exception:
            continue

    # 3. Save to SQLite
    print("💾 Saving context to market_data.db...")
    conn = sqlite3.connect('market_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS market_context (
            Symbol TEXT PRIMARY KEY,
            Spot_Price REAL,
            ATR_14 REAL,
            Beta REAL,
            Days_To_Event INTEGER,
            EMA_20 REAL
        )
    ''')
    cursor.execute('DELETE FROM market_context') # Clear old data
    cursor.executemany('''
        INSERT INTO market_context (Symbol, Spot_Price, ATR_14, Beta, Days_To_Event, EMA_20)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', records)
    conn.commit()
    conn.close()
    print("✅ Database built successfully. Ready for Streamlit.")

if __name__ == "__main__":
    # Provide the path to your downloaded Bhavcopy here before running
    update_market_database("BhavCopy.csv") 
