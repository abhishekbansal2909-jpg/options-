import os
import io
import time
import sqlite3
import requests
import zipfile
import pandas as pd
import numpy as np
from datetime import datetime
from scipy.stats import norm

# ==============================================================================
# 1. DATA EXTRACTION (NSE DOWNLOADER)
# ==============================================================================
def download_nse_bhavcopy(date_obj, base_dir="data"):
    """
    Downloads the F&O and Cash Bhavcopy ZIP files from NSE (UDiFF Format) and extracts them.
    """
    os.makedirs(base_dir, exist_ok=True)
    yyyymmdd = date_obj.strftime("%Y%m%d")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br"
    }

    urls = {
        "cash": f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip",
        "fo": f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{yyyymmdd}_F_0000.csv.zip"
    }
    
    file_paths = {}

    for market, url in urls.items():
        print(f"Downloading {market.upper()} UDiFF Bhavcopy for {yyyymmdd}...")
        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                    z.extractall(base_dir)
                    extracted_file = os.path.join(base_dir, url.split('/')[-1].replace('.zip', ''))
                    file_paths[market] = extracted_file
            else:
                print(f"Failed to download {market} data. Status Code: {response.status_code}")
                return None
        except Exception as e:
            print(f"Error fetching {market} data: {e}")
            return None
            
        time.sleep(1)

    return file_paths

# ==============================================================================
# 2. HISTORICAL CASH SPOT & 20-DAY EMA ENGINE (GLASS FOUNDATION)
# ==============================================================================
def sync_cash_and_compute_ema(df_cash, trade_date_str, db_path="market_data.db"):
    """
    Saves daily cash settlement closes into a persistent table and computes
    rolling 20-day EMA per symbol. Nothing is discarded.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 1. Maintain cash settlement history table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cash_history (
            SYMBOL TEXT,
            TRADE_DATE TEXT,
            CLOSE REAL,
            PRIMARY KEY (SYMBOL, TRADE_DATE)
        )
    """)
    
    # Standardize column headers for cash
    cash_records = df_cash[['SYMBOL', 'CLOSE']].copy()
    cash_records['TRADE_DATE'] = trade_date_str
    cash_records['CLOSE'] = cash_records['CLOSE'].astype(float)
    
    # Insert or update today's cash records
    records_to_insert = [
        (row['SYMBOL'], row['TRADE_DATE'], row['CLOSE'])
        for _, row in cash_records.iterrows()
    ]
    cursor.executemany("""
        INSERT OR REPLACE INTO cash_history (SYMBOL, TRADE_DATE, CLOSE)
        VALUES (?, ?, ?)
    """, records_to_insert)
    conn.commit()

    # 2. Pull up to the last 40 trading sessions per symbol to ensure accurate EMA seeding
    query = """
        SELECT SYMBOL, TRADE_DATE, CLOSE 
        FROM cash_history 
        ORDER BY SYMBOL, DATE(TRADE_DATE) ASC
    """
    history_df = pd.read_sql_query(query, conn)
    conn.close()

    if history_df.empty:
        cash_records['EMA_20'] = cash_records['CLOSE']
        cash_records['EMA_DIST_PCT'] = 0.0
        return cash_records[['SYMBOL', 'CLOSE', 'EMA_20', 'EMA_DIST_PCT']].rename(columns={'CLOSE': 'SPOT_PRICE'})

    # 3. Compute 20-day exponential moving average grouped by ticker
    history_df['EMA_20'] = (
        history_df.groupby('SYMBOL')['CLOSE']
        .transform(lambda x: x.ewm(span=20, adjust=False).mean())
    )
    
    # Extract only the latest computed metrics for each symbol
    latest_ema = history_df.groupby('SYMBOL').last().reset_index()
    latest_ema['EMA_DIST_PCT'] = np.round(
        ((latest_ema['CLOSE'] - latest_ema['EMA_20']) / latest_ema['EMA_20']) * 100, 2
    )
    latest_ema['EMA_20'] = np.round(latest_ema['EMA_20'], 2)
    
    latest_ema.rename(columns={'CLOSE': 'SPOT_PRICE'}, inplace=True)
    return latest_ema[['SYMBOL', 'SPOT_PRICE', 'EMA_20', 'EMA_DIST_PCT']]

# ==============================================================================
# 3. DATA TRANSFORMATION & ENRICHMENT
# ==============================================================================
def clean_bhavcopy_for_db(fo_path, cash_path, date_obj, db_path="market_data.db"):
    """
    Filters out noise, keeps Near & Next Month contracts, and joins SPOT_PRICE,
    EMA_20, and EMA_DIST_PCT without dropping counter-trend setups.
    """
    df_fo = pd.read_csv(fo_path)
    df_cash = pd.read_csv(cash_path)
    
    df_fo.columns = df_fo.columns.str.strip()
    df_cash.columns = df_cash.columns.str.strip()

    # Filter for Stock Options
    df = df_fo[df_fo['INSTRUMENT'] == 'OPTSTK'].copy()
    df['EXPIRY_DT'] = pd.to_datetime(df['EXPIRY_DT'])
    
    # Isolate Near and Next month expiries
    unique_expiries = sorted(df['EXPIRY_DT'].dropna().unique())
    if len(unique_expiries) >= 2:
        df = df[df['EXPIRY_DT'].isin(unique_expiries[:2])].copy()
    elif len(unique_expiries) == 1:
        df = df[df['EXPIRY_DT'] == unique_expiries[0]].copy()

    # Dynamic DTE calculation
    current_date_str = date_obj.strftime("%Y-%m-%d")
    df['DTE_DAYS'] = (df['EXPIRY_DT'] - pd.to_datetime(current_date_str)).dt.days
    df['DTE_DAYS'] = df['DTE_DAYS'].apply(lambda x: 1 if x <= 0 else x)
    df['T'] = df['DTE_DAYS'] / 365.0 

    # Basic liquidity sanity check
    df = df[(df['CONTRACTS'] > 0) & (df['OPEN_INT'] > 0)].copy()

    columns_to_keep = [
        'SYMBOL', 'EXPIRY_DT', 'STRIKE_PR', 'OPTION_TYP', 
        'CLOSE', 'CONTRACTS', 'OPEN_INT', 'TIMESTAMP', 'T'
    ]
    df = df[columns_to_keep]

    # Glass Engine Step: Attach Spot, EMA_20, and EMA_DIST_PCT
    ema_metrics = sync_cash_and_compute_ema(df_cash, current_date_str, db_path=db_path)
    df = pd.merge(df, ema_metrics, on='SYMBOL', how='left')
    
    df.rename(columns={
        'OPTION_TYP': 'TYPE',
        'CLOSE': 'OPT_PRICE',
        'CONTRACTS': 'VOLUME',
        'STRIKE_PR': 'STRIKE'
    }, inplace=True)
    
    df.dropna(subset=['SPOT_PRICE', 'EMA_20'], inplace=True)
    
    # Enforce float typing for Greeks computation
    df['SPOT_PRICE'] = df['SPOT_PRICE'].astype(float)
    df['EMA_20'] = df['EMA_20'].astype(float)
    df['EMA_DIST_PCT'] = df['EMA_DIST_PCT'].astype(float)
    df['STRIKE'] = df['STRIKE'].astype(float)
    df['OPT_PRICE'] = df['OPT_PRICE'].astype(float)
    
    return df

# ==============================================================================
# 4. QUANTITATIVE ENGINE (BLACK-SCHOLES IV & GREEKS)
# ==============================================================================
def calculate_iv_and_greeks_vectorized(df, risk_free_rate=0.07):
    """
    Vectorized Black-Scholes solver. Computes IV and DELTA per row.
    """
    S = df['SPOT_PRICE'].values
    K = df['STRIKE'].values
    T = df['T'].values
    P = df['OPT_PRICE'].values
    types = df['TYPE'].values
    r = risk_free_rate

    sigma = np.full(S.shape, 0.30) 
    MAX_ITER = 100
    TOLERANCE = 1e-4

    for _ in range(MAX_ITER):
        d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)
        
        call_price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
        put_price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        
        price_est = np.where(types == 'CE', call_price, put_price)
        
        vega = S * norm.pdf(d1) * np.sqrt(T)
        vega = np.where(vega < 1e-6, 1e-6, vega) 
        
        diff = price_est - P
        step = diff / vega
        
        sigma -= step
        sigma = np.maximum(sigma, 0.01)
        
        if np.max(np.abs(diff)) < TOLERANCE:
            break

    # Final pass: Calculate IV and Delta
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    call_delta = norm.cdf(d1)
    put_delta = call_delta - 1.0

    df['IV'] = np.round(sigma * 100, 2)
    df['DELTA'] = np.round(np.where(types == 'CE', call_delta, put_delta), 4)
    
    return df

# ==============================================================================
# 5. STORAGE & PIPELINE EXECUTION
# ==============================================================================
def run_daily_ingestion(target_date_str, db_path="market_data.db"):
    print(f"--- Starting Glass Engine Pipeline for {target_date_str} ---")
    
    date_obj = datetime.strptime(target_date_str, "%d-%b-%Y")
    file_paths = download_nse_bhavcopy(date_obj)
    if not file_paths:
        print("Pipeline aborted: failed to download NSE Bhavcopy.")
        return

    print("Cleaning data and computing 20-day EMA from Cash Bhavcopy...")
    clean_df = clean_bhavcopy_for_db(file_paths['fo'], file_paths['cash'], date_obj, db_path=db_path)
    
    print("Calculating Implied Volatility and Option Greeks...")
    final_df = calculate_iv_and_greeks_vectorized(clean_df, risk_free_rate=0.07) 
    
    print("Writing enriched data to SQLite database...")
    conn = sqlite3.connect(db_path)
    final_df.drop(columns=['T'], inplace=True)
    
    final_df.to_sql("options_history", conn, if_exists="append", index=False)
    
    cursor = conn.cursor()
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_symbol_time ON options_history(SYMBOL, TIMESTAMP)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_strike_opt ON options_history(SYMBOL, EXPIRY_DT, STRIKE, TYPE)")
    
    conn.commit()
    conn.close()
    
    print(f"Success! {len(final_df)} contracts enriched with SPOT_PRICE, EMA_20, EMA_DIST_PCT, IV, and DELTA.")

if __name__ == "__main__":
    run_daily_ingestion("06-OCT-2026")
