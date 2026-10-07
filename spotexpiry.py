import pandas as pd
import yfinance as yf
import numpy as np
import streamlit as st

class SpotAndExpiryEngine:
    @staticmethod
    def filter_active_expiries(df, include_next_month=True):
        if df.empty or 'Expiry_Date' not in df.columns:
            return df
            
        expiries = sorted(df['Expiry_Date'].dropna().unique())
        if not expiries:
            return df
            
        near_expiry = expiries[0]
        valid_expiries = [near_expiry]
        
        if include_next_month and len(expiries) > 1:
            valid_expiries.append(expiries[1])
            
        filtered_df = df[df['Expiry_Date'].isin(valid_expiries)].copy()
        filtered_df['Expiry_Cycle'] = filtered_df['Expiry_Date'].apply(
            lambda x: "Near" if x == near_expiry else "Next"
        )
        return filtered_df

    @staticmethod
    @st.cache_data(ttl=3600, show_spinner=False)
    def _fetch_live_market_data(symbols):
        """Fetches real cash prices and ATR in a single high-speed batch call."""
        valid_symbols = [s for s in symbols if isinstance(s, str)]
        yf_tickers = " ".join([f"{sym}.NS" for sym in valid_symbols])
        
        market_data = []
        try:
            # 1mo period ensures we have enough days to calculate the 14-day ATR
            data = yf.download(yf_tickers, period="1mo", progress=False)
            
            if data.empty:
                return pd.DataFrame()
                
            closes = data['Close']
            highs = data['High']
            lows = data['Low']
            
            for sym in valid_symbols:
                yf_sym = f"{sym}.NS"
                if isinstance(closes, pd.DataFrame) and yf_sym in closes.columns:
                    sym_close = closes[yf_sym].dropna()
                    sym_high = highs[yf_sym].dropna()
                    sym_low = lows[yf_sym].dropna()
                elif isinstance(closes, pd.Series) and len(valid_symbols) == 1:
                    sym_close = closes.dropna()
                    sym_high = highs.dropna()
                    sym_low = lows.dropna()
                else:
                    continue
                    
                if len(sym_close) < 14:
                    continue
                    
                # Exact cash market Spot Price
                spot_price = sym_close.iloc[-1]
                
                # True 14-Day ATR
                df_calc = pd.DataFrame({'High': sym_high, 'Low': sym_low, 'Close': sym_close})
                tr1 = df_calc['High'] - df_calc['Low']
                tr2 = np.abs(df_calc['High'] - df_calc['Close'].shift())
                tr3 = np.abs(df_calc['Low'] - df_calc['Close'].shift())
                true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
                atr_14 = true_range.rolling(14).mean().iloc[-1]
                
                market_data.append({
                    'Symbol': sym,
                    'Spot_Price': spot_price,
                    'ATR_14': atr_14,
                    'Beta': 1.0,          # Placeholder for scoring
                    'Days_To_Event': 30   # Placeholder for scoring
                })
                
            return pd.DataFrame(market_data)
        except Exception as e:
            print(f"yfinance fetch error: {e}")
            return pd.DataFrame()

    @staticmethod
    def sync_market_context(df):
        # Dynamically pulls live prices based on the options chain symbols
        unique_symbols = df['Symbol'].dropna().unique().tolist()
        
        market_context_df = SpotAndExpiryEngine._fetch_live_market_data(unique_symbols)
        
        if not market_context_df.empty:
            merged_df = pd.merge(df, market_context_df, on='Symbol', how='inner')
            return merged_df
        else:
            # Fallback if there is no internet connection
            df['Spot_Price'] = df['Strike'] 
            df['ATR_14'] = df['Strike'] * 0.025
            df['Beta'] = 1.0
            df['Days_To_Event'] = 30
            return df
