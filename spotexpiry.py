import pandas as pd
import numpy as np

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
    def sync_market_context(df):
        # OPTION B: Dynamically Derive Spot Price & Context from Bhavcopy
        df = df.copy()
        
        # 1. Reverse-engineer Spot Price (Fixing the 0-LTP penny option bug)
        try:
            valid_df = df[df['LTP'] > 0]
            pivot_df = valid_df.pivot_table(index=['Symbol', 'Strike'], columns='Option_Type', values='LTP', aggfunc='first').reset_index()
            
            if 'CE' in pivot_df.columns and 'PE' in pivot_df.columns:
                pivot_df = pivot_df.dropna(subset=['CE', 'PE'])
                pivot_df['Diff'] = np.abs(pivot_df['CE'] - pivot_df['PE'])
                idx_min = pivot_df.groupby('Symbol')['Diff'].idxmin().dropna()
                atm_map = pivot_df.loc[idx_min].set_index('Symbol')['Strike']
                df['Spot_Price'] = df['Symbol'].map(atm_map)
            else:
                df['Spot_Price'] = df['Strike']
        except Exception:
            df['Spot_Price'] = df['Strike']

        df['Spot_Price'] = df['Spot_Price'].fillna(df['Strike'])
        
        # 2. Inject robust defaults so app.py doesn't crash on line 87
        df['ATR_14'] = df['Spot_Price'] * 0.025  # 2.5% of spot as safe fallback ATR
        df['Beta'] = 1.0
        df['Days_To_Event'] = 30
        
        return df
