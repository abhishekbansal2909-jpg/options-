import yfinance as yf
import pandas as pd
import numpy as np

class SpotAndExpiryEngine:
    """Synchronizes live cash prices, filters active expiries, and calculates market context."""

    @staticmethod
    def filter_front_month_expiry(df: pd.DataFrame) -> pd.DataFrame:
        """Isolates the nearest active expiry date to ensure high liquidity."""
        if 'Expiry_Date' not in df.columns or df['Expiry_Date'].isnull().all():
            print("⚠️ Expiry dates missing or invalid. Skipping expiry filter.")
            return df

        active_expiry = df['Expiry_Date'].min()
        print(f"📅 Locking onto active front-month expiry: {active_expiry.date()}")
        
        return df[df['Expiry_Date'] == active_expiry].copy()

    @staticmethod
    def sync_market_context(df: pd.DataFrame) -> pd.DataFrame:
        """
        Fetches 1-year historical data to calculate Spot Price, 14-Day ATR, 
        and Asset Beta relative to the Nifty 50 benchmark.
        """
        symbols = df['Symbol'].unique().tolist()
        tickers = [f"{s}.NS" for s in symbols]
        
        # Append Nifty 50 benchmark for Beta calculation
        fetch_list = tickers + ["^NSEI"]

        print(f"📥 Fetching 1-year market data for {len(symbols)} F&O stocks + Nifty 50...")
        
        # Download 1-year period data for Beta and ATR calculations
        hist_data = yf.download(fetch_list, period="1y", group_by="ticker", progress=False)
        
        # Pre-calculate Nifty 50 daily returns
        try:
            nifty_returns = hist_data["^NSEI"]["Close"].pct_change().dropna()
        except KeyError:
            nifty_returns = None
            print("⚠️ Failed to fetch Nifty 50 benchmark. Beta will default to 1.0")

        spot_records = []
        for sym in symbols:
            ticker = f"{sym}.NS"
            try:
                # Handle yfinance multi-ticker vs single-ticker structure
                stk_data = hist_data[ticker] if len(fetch_list) > 1 else hist_data
                stk_data = stk_data.dropna(subset=['Close'])
                
                if stk_data.empty: 
                    continue
                    
                # 1. Spot Price (Latest Close)
                spot_price = stk_data['Close'].iloc[-1]
                
                # 2. 14-Day ATR Calculation
                high_low = stk_data['High'] - stk_data['Low']
                high_close = np.abs(stk_data['High'] - stk_data['Close'].shift())
                low_close = np.abs(stk_data['Low'] - stk_data['Close'].shift())
                ranges = pd.concat([high_low, high_close, low_close], axis=1)
                true_range = ranges.max(axis=1)
                atr_14 = true_range.rolling(window=14).mean().iloc[-1]
                
                # 3. Asset Beta Calculation
                beta = 1.0
                if nifty_returns is not None:
                    stk_returns = stk_data['Close'].pct_change().dropna()
                    # Align dates to ensure equal length arrays for covariance
                    aligned = pd.concat([stk_returns, nifty_returns], axis=1, join='inner').dropna()
                    if len(aligned) > 30: 
                        cov = np.cov(aligned.iloc[:, 0], aligned.iloc[:, 1])[0, 1]
                        var = np.var(aligned.iloc[:, 1])
                        beta = cov / var if var > 0 else 1.0
                
                # 4. Catalyst Timer (Placeholder)
                # yfinance earnings API is frequently unstable. Hardcoding a safe bypass
                # value (99) for now until a dedicated Corporate Events API is integrated.
                days_to_event = 99 

                spot_records.append({
                    "Symbol": sym, 
                    "Spot_Price": round(float(spot_price), 2),
                    "ATR_14": round(float(atr_14), 2),
                    "Beta": round(float(beta), 2),
                    "Days_To_Event": days_to_event
                })
            except Exception as e:
                continue
                
        spot_df = pd.DataFrame(spot_records)
        
        if spot_df.empty:
            print("❌ Failed to calculate market context.")
            return df

        return pd.merge(df, spot_df, on="Symbol", how="inner")
