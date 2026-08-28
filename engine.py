import glob
import os
import re
import zipfile
import pandas as pd

INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}

class OptionsDataIngestion:
    """Handles discovery, unzipping, normalization, and filtering of NSE F&O Bhavcopy data."""

    def __init__(self, file_path=None):
        self.file_path = file_path or self._locate_bhavcopy()

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

        # Standardize headers by removing spaces and underscores
        df.columns = df.columns.astype(str).str.strip().str.upper().str.replace("_", "").str.replace(" ", "")

        # Map known UDiFF & legacy variations
        column_map = {
            "TCKRSYMB": "Symbol", "TRADGSYMB": "Symbol", "UNDRLNGST": "Symbol", "SYMBOL": "Symbol",
            "OPTNTP": "Option_Type", "OPTIONTYP": "Option_Type", "OPTIONTYPE": "Option_Type",
            "STRKPRIC": "Strike", "STRKPRC": "Strike", "STRIKEPRC": "Strike", "STRIKE": "Strike",
            "OPNINTRST": "OI", "OINOCON": "OI", "OPENINT": "OI", "OI": "OI",
            "CHGINOI": "OI_Change", "CHGOI": "OI_Change", "CHNGINOI": "OI_Change", "CHANGEINOI": "OI_Change",
            "CLSPRIC": "LTP", "SETTLMPRIC": "LTP", "CLOSEPRIC": "LTP", "CLOSE": "LTP", "LTP": "LTP",
            "FININSTRMACTLXPRYDT": "Expiry_Date", "EXPIRYDT": "Expiry_Date", "XPIRYDT": "Expiry_Date",
            "EXPRYDT": "Expiry_Date", "EXPIRATIONDATE": "Expiry_Date", "EXPIRYDATE": "Expiry_Date"
        }
        df = df.rename(columns={k: v for k, v in column_map.items() if k in df.columns})

        # Fuzzy search for OI Change if the hardcoded mapper missed it
        if "OI_Change" not in df.columns:
            fuzzy_oi_cols = [c for c in df.columns if "CHG" in c and "OI" in c]
            if fuzzy_oi_cols:
                df["OI_Change"] = df[fuzzy_oi_cols[0]]

        # Fallback extraction from CONTRACT_D
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
        
        return df[['Symbol', 'Expiry_Date', 'Option_Type', 'Strike', 'LTP', 'OI', 'OI_Change']]
