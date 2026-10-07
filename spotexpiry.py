import pandas as pd
import sqlite3
import os

class SpotAndExpiryEngine:
    @staticmethod
    def filter_active_expiries(df, include_next_month=True):
        if df.empty or 'Expiry_Date' not in df.columns:
            return df
            
        # Sort expiries
        expiries = sorted(df['Expiry_Date'].dropna().unique())
        if not expiries:
            return df
            
        near_expiry = expiries[0]
        valid_expiries = [near_expiry]
        
        if include_next_month and len(expiries) > 1:
            next_expiry = expiries[1]
            valid_expiries.append(next_expiry)
            
        filtered_df = df[df['Expiry_Date'].isin(valid_expiries)].copy()
        
        # Tag Expiry Cycle
        filtered_df['Expiry_Cycle'] = filtered_df['Expiry_Date'].apply(
            lambda x: "Near" if x == near_expiry else "Next"
        )
        
        return filtered_df

    @staticmethod
    def sync_market_context(df, db_path='market_data.db'):
        if not os.path.exists(db_path):
            print("⚠️ market_data.db not found. Run data_pipeline.py first.")
            return df
            
        conn = sqlite3.connect(db_path)
        try:
            market_ctx = pd.read_sql_query("SELECT * FROM market_context", conn)
            # Merge context into the main dataframe
            merged_df = pd.merge(df, market_ctx, on='Symbol', how='inner')
            return merged_df
        except Exception as e:
            print(f"⚠️ Error reading from DB: {e}")
            return df
        finally:
            conn.close()
