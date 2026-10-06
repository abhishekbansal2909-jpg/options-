import glob
import os
import re
import zipfile
import pandas as pd
import numpy as np
from scipy.stats import norm

INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}

class OptionsDataIngestion:
    """Handles discovery, unzipping, normalization, and filtering of NSE F&O Bhavcopy data."""

    def __init__(self, file_path=None, max_symbols=None):
        self.file_path = file_path or self._locate_bhavcopy()
        self.max_symbols = max_symbols

    def _locate_bhavcopy(self) -> str | None:
        patterns = [
            "/content/BhavCopy*.zip", "/content/BhavCopy*.csv", "/content/op*.csv",
            "BhavCopy*.zip", "BhavCopy*.csv", "op*.csv"
        ]
        for pat in patterns:
            matches = glob.glob(pat)
            if matches:
                return sorted(matches)[-1]
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
            "CHNGINOI": "OI_Change", "CHANGEINOI": "OI_Change",
            "CLSPRIC": "LTP", "SETTLMPRIC": "LTP", "CLOSEPRIC": "LTP", "CLOSE": "LTP", "LTP": "LTP",
            "FININSTRMACTLXPRYDT": "Expiry_Date", "EXPIRYDT": "Expiry_Date", "XPIRYDT": "Expiry_Date",
            "EXPRYDT": "Expiry_Date", "EXPIRATIONDATE": "Expiry_Date", "EXPIRYDATE": "Expiry_Date"
        }
        df = df.rename(columns={k: v for k, v in column_map.items() if k in df.columns})

        if "OI_Change" not in df.columns:
            fuzzy_oi_cols = [c for c in df.columns if ("CHG" in c or "CHNG" in c) and ("OI" in c or "OPN" in c)]
            if fuzzy_oi_cols:
                df["OI_Change"] = df[fuzzy_oi_cols[0]]

        if "CONTRACTD" in df.columns:
            if "Option_Type" not in df.columns:
                df["Option_Type"] = df["CONTRACTD"].astype(str).str.extract(r"\b(CE|PE)\b", flags=re.IGNORECASE)
            if "Strike" not in df.columns:
                extracted = df["CONTRACTD"].astype(str).str.extract(r"(\d+(?:\.\d+)?)\s*(?:CE|PE)|\b(?:CE|PE)\s*(\d+(?:\.\d+)?)\b", flags=re.IGNORECASE)
                df["Strike"] = extracted[0].fillna(extracted[1])

        if "Option_Type" not in df.columns or "Symbol" not in df.columns:
            raise KeyError("❌ Failed to parse required fields ('Option_Type', 'Symbol').")

        df["Option_Type"] = df["Option_Type"].astype(str).str.strip().str.upper()
        df = df[df["Option_Type"].isin(["CE", "PE"])].copy()

        df["Symbol"] = df["Symbol"].astype(str).str.strip().str.upper()
        df = df[~df["Symbol"].isin(INDEX_SYMBOLS)].copy()

        df["Strike"] = pd.to_numeric(df["Strike"], errors="coerce")
        df["OI"] = pd.to_numeric(df["OI"], errors="coerce").fillna(0)
        
        if "LTP" not in df.columns:
            df["LTP"] = 0.0
        df["LTP"] = pd.to_numeric(df["LTP"], errors="coerce").fillna(0.0)
        
        if "OI_Change" not in df.columns:
            df["OI_Change"] = 0.0
        df["OI_Change"] = pd.to_numeric(df["OI_Change"], errors="coerce").fillna(0)

        if "Expiry_Date" in df.columns:
            df["Expiry_Date"] = pd.to_datetime(df["Expiry_Date"], errors="coerce")
        else:
            df["Expiry_Date"] = pd.NaT

        df = df.dropna(subset=["Symbol", "Option_Type", "Strike"])
        
        # QUANTITATIVE LIQUIDITY FILTER (Drastically reduces memory & API overhead)
        if self.max_symbols:
            top_syms = df.groupby('Symbol')['OI'].sum().nlargest(self.max_symbols).index
            df = df[df['Symbol'].isin(top_syms)]
        
        return df[['Symbol', 'Expiry_Date', 'Option_Type', 'Strike', 'LTP', 'OI', 'OI_Change']]


class QuantitativeScoringEngine:
    """
    Applies the 'Glass-Box' quantitative filters: ATR moats, Delta limits, 
    Premium yields, and Institutional Wall detection without dropping rows.
    """
    def __init__(self, options_df, market_data_df, risk_free_rate=0.07):
        """
        market_data_df requires columns: 
        ['Symbol', 'Spot_Price', 'ATR_14', 'Beta', 'Days_To_Event']
        EMA metrics are fetched statelessly via yfinance.
        """
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
        # 1. Merge Spot, ATR, Beta, Event data from the UI stream
        self.df = pd.merge(self.df, self.market_data, on='Symbol', how='left')
        
        # 2. STATELESS CLOUD FIX (Render Optimized): Chunked sequential fetching
        if 'EMA_20' not in self.df.columns or self.df['EMA_20'].isna().all():
            try:
                import yfinance as yf
                import warnings
                warnings.filterwarnings('ignore')
                
                symbols = self.df['Symbol'].unique().tolist()
                ema_dict = {}
                
                # Fast sequential fetching for the filtered liquidity universe
                chunk_size = 40
                for i in range(0, len(symbols), chunk_size):
                    chunk = symbols[i:i + chunk_size]
                    yf_tickers = " ".join([f"{sym}.NS" for sym in chunk])
                    
                    data = yf.download(
                        yf_tickers, 
                        period="2mo", 
                        progress=False, 
                        threads=False, 
                        timeout=15
                    )
                    
                    if 'Close' in data:
                        closes = data['Close']
                        for sym in chunk:
                            yf_sym = f"{sym}.NS"
                            
                            if isinstance(closes, pd.DataFrame):
                                if yf_sym in closes.columns:
                                    ticker_closes = closes[yf_sym].dropna()
                                else:
                                    continue
                            else:
                                ticker_closes = closes.dropna()
                                
                            if not ticker_closes.empty:
                                ema_20 = ticker_closes.ewm(span=20, adjust=False).mean().iloc[-1]
                                ema_dict[sym] = round(ema_20, 2)
                                
                self.df['EMA_20'] = self.df['Symbol'].map(ema_dict)
                self.df['EMA_DIST_PCT'] = np.round(((self.df['Spot_Price'] - self.df['EMA_20']) / self.df['EMA_20']) * 100, 2)
            except Exception as e:
                print(f"Cloud EMA Fetch Failed: {e}")

        # 3. Graceful fallback for missing tickers
        if 'EMA_20' not in self.df.columns:
            self.df['EMA_20'] = self.df['Spot_Price']
            self.df['EMA_DIST_PCT'] = 0.0
        else:
            self.df['EMA_20'] = self.df['EMA_20'].fillna(self.df['Spot_Price'])
            self.df['EMA_DIST_PCT'] = self.df['EMA_DIST_PCT'].fillna(0.0)
            
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
        if 'IV' not in self.df.columns:
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
        
        self.df['Pass_Delta'] = np.where(
            self.df['Option_Type'] == 'CE',
            np.abs(self.df['Delta']) <= 0.20,
            np.abs(self.df['Delta']) <= 0.13
        )

    def _generate_composite_score(self):
        self.df['Pass_Event'] = self.df['Days_To_Event'] > 14
        
        self.df['Regime_Aligned'] = np.where(
            self.df['Option_Type'] == 'CE',
            self.df['Spot_Price'] < self.df['EMA_20'],
            self.df['Spot_Price'] > self.df['EMA_20']
        )
        
        self.df['Missing_EMA'] = self.df['Spot_Price'] == self.df['EMA_20']
        
        score = np.zeros(len(self.df))
        score += np.where(self.df['Pass_Delta'], 25, 0)
        score += np.where(self.df['Pass_Moat'], 25, 0)
        score += np.where(self.df['Outside_Inst_Wall'], 20, 0)
        score += np.where(self.df['Pass_Event'], 15, 0)
        
        score += np.where(
            self.df['Regime_Aligned'], 15, 
            np.where(self.df['Missing_EMA'], 0, -40)
        ) 
        
        self.df['Composite_Score'] = score
