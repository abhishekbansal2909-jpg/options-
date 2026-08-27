import yfinance as yf
import pandas as pd

class SpotAndExpiryEngine:
    """Synchronizes live cash prices and filters for the active front-month expiry."""

    @staticmethod
    def filter_front_month_expiry(df: pd.DataFrame) -> pd.DataFrame:
        """Isolates the nearest active expiry date to ensure high liquidity."""
        if 'Expiry_Date' not in df.columns or df['Expiry_Date'].isnull().all():
            print("⚠️ Expiry dates missing or invalid. Skipping expiry filter.")
            return df

        # Find the earliest upcoming expiry date across the dataset
        active_expiry = df['Expiry_Date'].min()
        print(f"📅 Locking onto active front-month expiry: {active_expiry.date()}")
        
        # Filter the DataFrame to only include this specific expiry
        return df[df['Expiry_Date'] == active_expiry].copy()

    @staticmethod
    def sync_spot_prices(df: pd.DataFrame) -> pd.DataFrame:
        """Fetches the latest cash market closing prices for all symbols."""
        symbols = df['Symbol'].unique().tolist()
        tickers = [f"{s}.NS" for s in symbols]

        print(f"📥 Fetching live spot prices for {len(symbols)} optionable stocks...")
        
        # Download 1-day period data; yfinance handles bulk downloads efficiently
        price_data = yf.download(tickers, period="1d", group_by="ticker", progress=False)
        
        spot_records = []
        for sym in symbols:
            ticker = f"{sym}.NS"
            try:
                # Handle yfinance data structure (single vs. multi-ticker)
                if len(symbols) == 1:
                    close_price = price_data['Close'].iloc[-1]
                else:
                    close_price = price_data[ticker]['Close'].iloc[-1]
                    
                if pd.notna(close_price):
                    spot_records.append({
                        "Symbol": sym, 
                        "Spot_Price": round(float(close_price), 2)
                    })
            except Exception:
                # Skip symbol if yfinance fails to return data
                continue
                
        spot_df = pd.DataFrame(spot_records)
        
        if spot_df.empty:
            print("❌ Failed to fetch spot prices.")
            return df

        # Merge the spot prices back into the main options DataFrame
        merged_df = pd.merge(df, spot_df, on="Symbol", how="inner")
        return merged_df
