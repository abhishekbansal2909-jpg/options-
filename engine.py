import pandas as pd
import numpy as np
from scipy.stats import norm
import zipfile
import glob
import os

INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}

class OptionsDataIngestion:
    def __init__(self, file_path):
        self.file_path = file_path

    def load_bhavcopy(self) -> pd.DataFrame:
        if self.file_path.endswith(".zip"):
            with zipfile.ZipFile(self.file_path, 'r') as z:
                csv_file = [f for f in z.namelist() if f.endswith('.csv')][0]
                with z.open(csv_file) as f:
                    df = pd.read_csv(f, low_memory=False)
        else:
            df = pd.read_csv(self.file_path, low_memory=False)

        df.columns = df.columns.astype(str).str.strip().str.upper().str.replace("_", "").str.replace(" ", "")
        
        rename_dict = {
            "TCKRSYMB": "Symbol", "TRADGSYMB": "Symbol", "SYMBOL": "Symbol", 
            "OPTNTP": "Option_Type", "OPTIONTYP": "Option_Type", "STRKPRIC": "Strike",
            "OPNINTRST": "OI", "OPENINT": "OI", "CLSPRIC": "LTP", "CLOSE": "LTP", 
            "FININSTRMACTLXPRYDT": "Expiry_Date", "EXPIRYDT": "Expiry_Date"
        }
        for old, new in rename_dict.items():
            if old in df.columns: df.rename(columns={old: new}, inplace=True)
            
        if "CONTRACTD" in df.columns:
            if "Option_Type" not in df.columns:
                df["Option_Type"] = df["CONTRACTD"].astype(str).str.extract(r"\b(CE|PE)\b", flags=re.IGNORECASE)
            if "Strike" not in df.columns:
                ext = df["CONTRACTD"].astype(str).str.extract(r"(\d+(?:\.\d+)?)\s*(?:CE|PE)|\b(?:CE|PE)\s*(\d+(?:\.\d+)?)\b", flags=re.IGNORECASE)
                df["Strike"] = ext[0].fillna(ext[1])

        df["Option_Type"] = df["Option_Type"].astype(str).str.strip().str.upper()
        df = df[df["Option_Type"].isin(["CE", "PE"])].copy()
        df["Symbol"] = df["Symbol"].astype(str).str.strip().str.upper()
        df = df[~df["Symbol"].isin(INDEX_SYMBOLS)].copy()

        df["Strike"] = pd.to_numeric(df["Strike"], errors="coerce")
        df["OI"] = pd.to_numeric(df["OI"], errors="coerce").fillna(0)
        df["LTP"] = pd.to_numeric(df.get("LTP", 0.0), errors="coerce").fillna(0.0)
        df["Expiry_Date"] = pd.to_datetime(df.get("Expiry_Date", pd.NaT), errors="coerce")
        
        return df.dropna(subset=["Symbol", "Option_Type", "Strike"])


class QuantitativeScoringEngine:
    def __init__(self, options_df, market_data_df, risk_free_rate=0.07):
        self.df = options_df.copy()
        self.market_data = market_data_df
        self.r = risk_free_rate

    def run_glass_box_pipeline(self, current_date):
        self._merge_market_context()
        if self.df.empty: return self.df
        self._detect_institutional_walls()
        self._compute_moats()
        self._compute_delta(current_date)
        self._generate_composite_score()
        return self.df

    def _merge_market_context(self):
        self.df = pd.merge(self.df, self.market_data, on='Symbol', how='inner')
        self.df.dropna(subset=['Spot_Price'], inplace=True)

    def _detect_institutional_walls(self):
        idx_call = self.df[self.df['Option_Type'] == 'CE'].groupby('Symbol')['OI'].idxmax()
        idx_put = self.df[self.df['Option_Type'] == 'PE'].groupby('Symbol')['OI'].idxmax()
        
        call_walls = self.df.loc[idx_call, ['Symbol', 'Strike']].rename(columns={'Strike': 'Call_Wall'})
        put_walls = self.df.loc[idx_put, ['Symbol', 'Strike']].rename(columns={'Strike': 'Put_Wall'})
        
        self.df = pd.merge(self.df, call_walls, on='Symbol', how='left')
        self.df = pd.merge(self.df, put_walls, on='Symbol', how='left')
        
        self.df['Outside_Inst_Wall'] = np.where(
            self.df['Option_Type'] == 'CE',
            self.df['Strike'] >= self.df['Call_Wall'],
            self.df['Strike'] <= self.df['Put_Wall']
        )

    def _compute_moats(self):
        distance = np.abs(self.df['Strike'] - self.df['Spot_Price'])
        self.df['Moat_Percent'] = (distance / self.df['Spot_Price']) * 100
        self.df['Moat_ATR'] = distance / self.df['ATR_14']
        self.df['Pass_Moat'] = np.where(
            self.df['Beta'] > 1.2,
            (self.df['Moat_Percent'] >= 7.0) & (self.df['Moat_ATR'] >= 2.0),
            (self.df['Moat_Percent'] >= 5.0) & (self.df['Moat_ATR'] >= 1.5)
        )

    def _compute_delta(self, current_date):
        self.df['IV'] = 0.30 
        dte = (pd.to_datetime(self.df['Expiry_Date']) - pd.to_datetime(current_date)).dt.days
        dte = np.where(dte <= 0, 1, dte)
        T = dte / 365.0
        
        S = self.df['Spot_Price'].values
        K = self.df['Strike'].values
        sigma = self.df['IV'].values
        
        d1 = (np.log(S / K) + (self.r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
        
        call_delta = norm.cdf(d1)
        put_delta = call_delta - 1
        
        self.df['Delta'] = np.where(self.df['Option_Type'] == 'CE', call_delta, put_delta)
        self.df['Pass_Delta'] = np.abs(self.df['Delta']) <= 0.20

    def _generate_composite_score(self):
        self.df['Pass_Event'] = self.df['Days_To_Event'] > 14
        
        score = np.zeros(len(self.df))
        score += np.where(self.df['Pass_Delta'], 25, 0)
        score += np.where(self.df['Pass_Moat'], 25, 0)
        score += np.where(self.df['Outside_Inst_Wall'], 25, 0)
        score += np.where(self.df['Pass_Event'], 25, 0)
        self.df['Composite_Score'] = score
