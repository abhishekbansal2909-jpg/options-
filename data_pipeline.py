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
    
    # Format dates for the new UDiFF nomenclature (YYYYMMDD)
    yyyymmdd = date_obj.strftime("%Y%m%d")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br"
    }

    # Updated URLs for NSE UDiFF Format
    urls = {
        "cash": f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip",
        "fo": f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{yyyymmdd}_F_0000.csv.zip"
    }
    
    file_paths = {}

    for market, url in urls.items():
        print(f"Downloading {market.upper()} UDiFF Bhavcopy for {yyyymmdd}...")
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                z.extractall(base_dir)
                extracted_file = os.path.join(base_dir, url.split('/')[-1].replace('.zip', ''))
                file_paths[market] = extracted_file
        else:
            print(f"Failed to download {market} data. Status Code: {response.status_code}")
            return None
            
        time.sleep(2) 

    return file_paths

# ==============================================================================
# 2. DATA TRANSFORMATION (CLEANING)
# ==============================================================================
def clean_bhavcopy_for_db(fo_path, cash_path, current_date):
    """
    Filters out noise, keeps near-month liquid stock options, and merges cash spot price.
    Note: You may need to map UDiFF column headers (e.g., 'FinInstrmNm' to 'INSTRUMENT') 
    depending on the exact CSV output.
    """
    df_fo = pd.read_csv(fo_path)
    df_cash = pd.read_csv(cash_path)
    
    df_fo.columns = df_fo.columns.str.strip()
    df_cash.columns = df_cash.columns.str.strip()

    # Keep only Stock Options
    df = df_fo[df_fo['INSTRUMENT'] == 'OPTSTK'].copy()
    
    df['EXPIRY_DT'] = pd.to_datetime(df['EXPIRY_DT'])
    current_expiry = df['EXPIRY_DT'].min()
    
    # Isolate Near-Month Options
    df = df[df['EXPIRY_DT'] == current_expiry]
    
    # Calculate DTE (Annualized for IV math)
    dte = (current_expiry - pd.to_datetime(current_date)).days
    if dte <= 0: dte = 1 / 365 
    df['T'] = dte / 365.0 

    # Liquidity Gate
    df = df[(df['CONTRACTS'] > 0) & (df['OPEN_INT'] > 0)]

    columns_to_keep = [
        'SYMBOL', 'EXPIRY_DT', 'STRIKE_PR', 'OPTION_TYP', 
        'CLOSE', 'CONTRACTS', 'OPEN_INT', 'TIMESTAMP', 'T'
    ]
    df = df[columns_to_keep]
    
    cash_spot = df_cash[['SYMBOL', 'CLOSE']].rename(columns={'CLOSE': 'SPOT_PRICE'})
    df = pd.merge(df, cash_spot, on='SYMBOL', how='left')
    
    df.rename(columns={
        'OPTION_TYP': 'TYPE',
        'CLOSE': 'OPT_PRICE',
        'CONTRACTS': 'VOLUME',
        'STRIKE_PR': 'STRIKE'
    }, inplace=True)
    
    df.dropna(subset=['SPOT_PRICE'], inplace=True)
    
    # CRITICAL FIX: Cast inputs to float for Black-Scholes arrays
    df['SPOT_PRICE'] = df['SPOT_PRICE'].astype(float)
    df['STRIKE'] = df['STRIKE'].astype(float)
    df['OPT_PRICE'] = df['OPT_PRICE'].astype(float)
    
    return df

# ==============================================================================
# 3. QUANTITATIVE ENGINE (BLACK-SCHOLES IV CALCULATOR)
# ==============================================================================
def calculate_iv_vectorized(df, risk_free_rate=0.07):
    """
    Uses a vectorized Newton-Raphson method to estimate IV for the entire DataFrame instantly.
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

    for i in range(MAX_ITER):
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

    df['IV'] = np.round(sigma * 100, 2)
    return df

# ==============================================================================
# 4. STORAGE (DATABASE PIPELINE)
# ==============================================================================
def run_daily_ingestion(target_date_str):
    print(f"--- Starting Underwriting Engine Pipeline for {target_date_str} ---")
    
    date_obj = datetime.strptime(target_date_str, "%d-%b-%Y")
    
    file_paths = download_nse_bhavcopy(date_obj)
    if not file_paths:
        print("Pipeline aborted due to download failure.")
        return

    print("Cleaning data and applying liquidity filters...")
    clean_df = clean_bhavcopy_for_db(file_paths['fo'], file_paths['cash'], date_obj)
    
    print("Calculating Implied Volatility via Black-Scholes...")
    final_df = calculate_iv_vectorized(clean_df, risk_free_rate=0.07) 
    
    print("Pushing to SQLite database...")
    db_path = "market_data.db"
    conn = sqlite3.connect(db_path)
    
    final_df.drop(columns=['T'], inplace=True)
    final_df.to_sql("options_history", conn, if_exists="append", index=False)
    
    cursor = conn.cursor()
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_symbol_time ON options_history(SYMBOL, TIMESTAMP)")
    
    conn.commit()
    conn.close()
    
    print(f"Success! {len(final_df)} highly liquid contracts processed and safely stored.")

if __name__ == "__main__":
    run_daily_ingestion("11-SEP-2026")
