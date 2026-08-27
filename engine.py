
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
        """Locates the latest F&O Bhavcopy in the workspace."""
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
        """Loads and cleans the Bhavcopy into a standardized options DataFrame."""
        if not self.file_path or not os.path.exists(self.file_path):
            raise FileNotFoundError("❌ F&O Bhavcopy file not found in directory.")

        print(f"🔄 Ingesting options data from: {self.file_path}")

        # Read CSV or extracted ZIP
        if self.file_path.endswith(".zip"):
            with zipfile.ZipFile(self.file_path, 'r') as z:
                csv_filename = [f for f in z.namelist() if f.endswith('.csv')][0]
                with z.open(csv_filename) as f:
                    df = pd.read_csv(f, low_memory=False)
        else:
            df = pd.read_csv(self.file_path, low_memory=False)

        # Standardize column headers
        df.columns = df.columns.astype(str).str.strip().str.upper()

        # UDiFF to standard format mapping
        column_map = {
            "TCKRSYMB": "Symbol", "TRADGSYMB": "Symbol", "UNDRLNG_ST": "Symbol",
            "OPTNTP": "Option_Type", "OPTION_TYP": "Option_Type",
            "STRKPRIC": "Strike", "STRK_PRC": "Strike",
            "OPNINTRST": "OI", "OI_NO_CON": "OI",
            "CHG_IN_OI": "OI_Change", "CHGIN_OI": "OI_Change", "CHG_OI": "OI_Change", "CHNG_IN_OI": "OI_Change",
            "CLSPRIC": "LTP", "SETTLMPRIC": "LTP", "CLOSE_PRIC": "LTP",
            "EXPIRY_DT": "Expiry_Date", "XPIRY_DT": "Expiry_Date", "EXPRY_DT": "Expiry_Date"
        }
        df = df.rename(columns={k: v for k, v in column_map.items() if k in df.columns})

        # Regex fallback for contract descriptions (if unified schema is missing)
        if "CONTRACT_D" in df.columns:
            if "Option_Type" not in df.columns:
                df["Option_Type"] = df["CONTRACT_D"].str.extract(r"\b(CE|PE)\b", flags=re.IGNORECASE)
            if "Strike" not in df.columns:
                extracted = df["CONTRACT_D"].str.extract(r"(\d+(?:\.\d+)?)\s*(?:CE|PE)|\b(?:CE|PE)\s*(\d+(?:\.\d+)?)\b", flags=re.IGNORECASE)
                df["Strike"] = extracted[0].fillna(extracted[1])

        # Mandatory sanity checks
        if "Option_Type" not in df.columns or "Symbol" not in df.columns:
            raise KeyError("❌ Failed to parse required fields ('Option_Type', 'Symbol').")

        # Clean types and filter out non-stock / non-option rows
        df["Option_Type"] = df["Option_Type"].astype(str).str.strip().str.upper()
        df = df[df["Option_Type"].isin(["CE", "PE"])].copy()

        df["Symbol"] = df["Symbol"].astype(str).str.strip().str.upper()
        df = df[~df["Symbol"].isin(INDEX_SYMBOLS)].copy()

        # Typecasting numeric values
        df["Strike"] = pd.to_numeric(df["Strike"], errors="coerce")
        df["OI"] = pd.to_numeric(df["OI"], errors="coerce").fillna(0)
        df["LTP"] = pd.to_numeric(df["LTP"], errors="coerce").fillna(0.0)
        
        if "OI_Change" in df.columns:
            df["OI_Change"] = pd.to_numeric(df["OI_Change"], errors="coerce").fillna(0)
        else:
            df["OI_Change"] = 0

        # Parse expiry dates if available
        if "Expiry_Date" in df.columns:
            df["Expiry_Date"] = pd.to_datetime(df["Expiry_Date"], errors="coerce")

        df = df.dropna(subset=["Symbol", "Option_Type", "Strike"])
        
        return df[['Symbol', 'Expiry_Date', 'Option_Type', 'Strike', 'LTP', 'OI', 'OI_Change']]
