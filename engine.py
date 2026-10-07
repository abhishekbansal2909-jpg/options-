import glob
import os
import re
import zipfile
import pandas as pd
import numpy as np
from scipy.stats import norm

INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}

class OptionsDataIngestion:
    def __init__(self, file_path=None):
        self.file_path = file_path or self._locate_bhavcopy()

    def _locate_bhavcopy(self) -> str | None:
        patterns = ["/content/BhavCopy*.zip", "/content/BhavCopy*.csv", "/content/op*.csv", "BhavCopy*.zip", "BhavCopy*.csv", "op*.csv"]
        for pat in patterns:
            matches = glob.glob(pat)
            if matches: return sorted(matches)[-1]
        return None

    def load_bhavcopy(self) -> pd.DataFrame:
        if not self.file_path or not os.path.exists(self.file_path):
            raise FileNotFoundError("❌ F&O Bhavcopy file not found.")

        if self.file_path.endswith(".zip"):
            with zipfile.ZipFile(self.file_path, 'r') as z:
                csv_filename = [f for f in z.namelist() if f.endswith('.csv')][0]
                with z.open(csv_filename) as f:
                    df = pd.read_csv(f, low_memory=False)
        else:
            df = pd.read_csv(self.file_path, low_memory=False)

        df.columns = df.columns.astype(str).str.strip().str.upper().str.replace("_", "").str.replace(" ", "")

        column_map = {
            "TCKRSYMB": "Symbol", "TRADGSYMB": "Symbol", "UNDRLNGST": "Symbol", "SYMBOL": "Symbol",
            "OPTNTP": "Option_Type", "OPTIONTYP": "Option_Type", "OPTIONTYPE": "Option_Type",
            "STRKPRIC": "Strike", "STRKPRC": "Strike", "STRIKEPRC": "Strike", "STRIKE": "Strike",
            "OPNINTRST": "OI", "OINOCON": "OI", "OPENINT": "OI", "OI": "OI",
            "CHNGINOPNINTRST": "OI_Change", "CHGINOI": "OI_Change", "CHGOI": "OI_Change", 
            "CLSPRIC": "LTP", "SETTLMPRIC": "LTP", "CLOSEPRIC": "LTP", "CLOSE": "LTP", "LTP": "LTP",
            "FININSTRMACTLXPRYDT": "Expiry_Date", "EXPIRYDT": "Expiry_Date", "XPIRYDT": "Expiry_Date",
            "EXPRYDT": "Expiry_Date", "EXPIRATIONDATE": "Expiry_Date", "EXPIRYDATE": "Expiry_Date"
        }
        df = df.rename(columns={k: v for k, v in column_map.items() if k in df.columns})

        if "CONTRACTD" in df.columns:
            if "Option_Type" not in df.columns:
                df["Option_Type"] = df["CONTRACTD"].astype(str).str.extract(r"\b(CE|PE)\b", flags=re.IGNORECASE)
            if "Strike" not in df.columns:
                extracted = df["CONTRACTD"].astype(str).str.extract(r"(\d+(?:\.\d+)?)\s*(?:CE|PE)|\b(?:CE|PE)\s*(\d+(?:\.\d+)?)\b", flags=re.IGNORECASE)
                df["Strike"] = extracted[0].fillna(extracted[1])

        df["Option_Type"] = df["Option_Type"].astype(str).str.strip().str.upper()
        df = df[df["Option_Type"].isin(["CE", "PE"])].copy()

        df["Symbol"] = df["Symbol"].astype(str).str.strip().str.upper()
        df = df[~df["Symbol"].isin(INDEX_SYMBOLS)].copy()

        df["Strike"] = pd.to_numeric(df["Strike"], errors="coerce")
        df["OI"] = pd.to_numeric(df["OI"], errors="coerce").fillna(0)
        df["LTP"] = pd.to_numeric(df.get("LTP", 0.0), errors="coerce").fillna(0.0)
        df["Expiry_Date"] = pd.to_datetime(df.get("Expiry_Date", pd.NaT), errors="coerce")

        return df.dropna(subset=["Symbol", "Option_Type", "Strike"])[['Symbol', 'Expiry_Date', 'Option_Type', 'Strike', 'LTP', 'OI']]

class QuantitativeScoringEngine:
    def __init__(self, options_df, market_data_df, risk_free_rate=0.07):
        self.df = options_df.copy()
        self.market_data = market_data_df
        self.r = risk_free_rate

    def run_glass_box_pipeline(self, current_date):
        self._merge_market_context()
        self._detect_institutional_walls()
        self._compute_moats()
        self._compute_delta(current_date)
        self._generate_composite_score()
        return self.df

    def _merge_market_context(self):
        # Merge database context
        if not self.market_data.empty:
            self.df = pd.merge(self.df, self.market_data, on='Symbol', how='left')

        # Fallbacks to prevent KeyErrors if database is missing
        if 'Spot_Price' not in self.df.columns:
            self.df['Spot_Price'] = self.df['Strike']
        if 'EMA_20' not in self.df.columns:
            self.df['EMA_20'] = self.df['Spot_Price']
        if 'ATR_14' not in self.df.columns:
            self.df['ATR_14'] = self.df['Spot_Price'] * 0.02
        if 'Beta' not in self.df.columns:
            self.df['Beta'] = 1.0
        if 'Days_To_Event' not in self.df.columns:
            self.df['Days_To_Event'] = 30

        self.df['EMA_DIST_PCT'] = np.where(
            self.df['EMA_20'] > 0, 
            np.round(((self.df['Spot_Price'] - self.df['EMA_20']) / self.df['EMA_20']) * 100, 2), 
            0.0
        )
        self.df.dropna(subset=['Spot_Price', 'EMA_20'], inplace=True)

    def _detect_institutional_walls(self):
        idx_call_wall = self.df[self.df['Option_Type'] == 'CE'].groupby('Symbol')['OI'].idxmax()
        idx_put_wall = self.df[self.df['Option_Type'] == 'PE'].groupby('Symbol')['OI'].idxmax()

        call_walls = self.df.loc[idx_call_wall, ['Symbol', 'Strike']].rename(columns={'Strike': 'Call_Wall'})
        put_walls = self.df.loc[idx_put_wall, ['Symbol', 'Strike']].rename(columns={'Strike': 'Put_Wall'})

        self.df = pd.merge(self.df, call_walls, on='Symbol', how='left')
        self.df = pd.merge(self.df, put_walls, on='Symbol', how='left')

        self.df['Outside_Inst_Wall'] = np.where(
            self.df['Option_Type'] == 'CE',
            (self.df['Strike'] >= self.df['Call_Wall']) | (self.df['Strike'] >= self.df['EMA_20']),
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
        
        S, K, sigma = self.df['Spot_Price'].values, self.df['Strike'].values, self.df['IV'].values
        d1 = (np.log(S / K) + (self.r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
        
        call_delta = norm.cdf(d1)
        put_delta = call_delta - 1
        
        self.df['Delta'] = np.where(self.df['Option_Type'] == 'CE', call_delta, put_delta)
        self.df['Pass_Delta'] = np.where(
            self.df['Option_Type'] == 'CE',
            np.abs(self.df['Delta']) <= 0.20,
            np.abs(self.df['Delta']) <= 0.13
        )

    def _generate_composite_score(self):
        self.df['Pass_Event'] = self.df['Days_To_Event'] > 14
        
        # Guarantees Regime_Aligned exists to prevent pipeline crashes
        self.df['Regime_Aligned'] = np.where(
            self.df['Option_Type'] == 'CE',
            self.df['Spot_Price'] < self.df['EMA_20'],
            self.df['Spot_Price'] > self.df['EMA_20']
        )
            
        score = np.zeros(len(self.df))
        score += np.where(self.df['Pass_Delta'], 25, 0)
        score += np.where(self.df['Pass_Moat'], 25, 0)
        score += np.where(self.df['Outside_Inst_Wall'], 20, 0)
        score += np.where(self.df['Pass_Event'], 15, 0)
        score += np.where(self.df['Regime_Aligned'], 15, -40) 
        
        self.df['Composite_Score'] = score
