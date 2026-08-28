import streamlit as st
import pandas as pd
import os
from engine import OptionsDataIngestion
from spotexpiry import SpotAndExpiryEngine
from spread_builder import SpreadBuilderEngine

# ==========================================
# FII Macro Tide Helper
# ==========================================
def get_fii_tide(filepath):
    """Calculates FII Long/Short Ratio from Participant OI CSV safely."""
    try:
        df = pd.read_csv(filepath)
        
        # Scan first 5 rows for header indicator safely
        header_row_idx = None
        for idx in range(min(5, len(df))):
            row_vals = [str(x).upper() for x in df.iloc[idx].dropna().tolist()]
            row_str = " ".join(row_vals)
            if "CLIENT" in row_str or "PARTICIPANT" in row_str or "FUTURE" in row_str:
                header_row_idx = idx + 1
                break
                
        if header_row_idx is not None:
            df = pd.read_csv(filepath, skiprows=header_row_idx)
                    
        df.columns = df.columns.astype(str).str.strip().str.upper().str.replace(" ", "_").str.replace("\t", "")
        
        client_col = next((c for c in df.columns if any(k in c for k in ["CLIENT", "PARTIC"])), df.columns[0] if len(df.columns) > 0 else None)
        fut_long_col = next((c for c in df.columns if "FUTURE_INDEX_LONG" in c or "FUTIDX_LONG" in c), None)
        fut_short_col = next((c for c in df.columns if "FUTURE_INDEX_SHORT" in c or "FUTIDX_SHORT" in c), None)

        if not client_col or not fut_long_col or not fut_short_col:
            return "UNAVAILABLE", 50.0
            
        df[client_col] = df[client_col].astype(str).str.strip().str.upper()
        fii_rows = df[df[client_col].str.contains("FII|FPI|FOREIGN", case=False, na=False)]
        
        if fii_rows.empty:
            return "UNAVAILABLE", 50.0
            
        fii_long = float(pd.to_numeric(fii_rows[fut_long_col].values[0], errors="coerce") or 0)
        fii_short = float(pd.to_numeric(fii_rows[fut_short_col].values[0], errors="coerce") or 0)
        
        total = fii_long + fii_short
        fii_ratio = round((fii_long / total) * 100, 2) if total > 0 else 50.0
        
        if fii_ratio >= 60.0:
            return "🟢 GREEN TIDE (FII Net Long)", fii_ratio
        elif fii_ratio <= 40.0:
            return "🔴 RED TIDE (FII Net Short)", fii_ratio
        else:
            return "⚪ NEUTRAL TIDE (Balanced)", fii_ratio
    except Exception as e:
        return f"ERROR ({str(e)})", 50.0

# ==========================================
# UI Configuration
# ==========================================
st.set_page_config(page_title="Quantitative Spread Engine", layout="wide")
st.title("🦅 Options Credit Spread Dashboard")
st.markdown("Ranked by Risk-to-Reward efficiency with institutional wall validation.")

# ==========================================
# Sidebar Controls
# ==========================================
st.sidebar.header("1. Data Ingestion")
bhavcopy_file = st.sidebar.file_uploader("Upload NSE Bhavcopy (ZIP/CSV)", type=['csv', 'zip'])
participant_file = st.sidebar.file_uploader("Upload Participant OI (CSV)", type=['csv'])

st.sidebar.header("2. Strategy Filter")
strategy_filter = st.sidebar.multiselect(
    "Select Strategies to Display", 
    options=["Bear Call Spread", "Bull Put Spread"],
    default=["Bear Call Spread", "Bull Put Spread"]
)

# ==========================================
# Core Execution Engine
# ==========================================
if st.sidebar.button("Run Quantitative Scan"):
    if bhavcopy_file is None:
        st.sidebar.error("⚠️ Please upload a Bhavcopy file to proceed.")
    else:
        with st.spinner("Processing option chains, scraping lot sizes, and computing Risk/Reward ratios..."):
            temp_path = None
            temp_part_path = None
            try:
                # 1. Macro Tide
                if participant_file is not None:
                    temp_part_path = f"temp_{participant_file.name}"
                    with open(temp_part_path, "wb") as f:
                        f.write(participant_file.getbuffer())
                    
                    tide_status, tide_ratio = get_fii_tide(temp_part_path)
                    st.info(f"**MACRO TIDE:** {tide_status} | **FII Long Ratio:** {tide_ratio}%")
                else:
                    st.warning("No Participant OI file uploaded. Macro Tide filter skipped.")

                # 2. Bhavcopy Options Pipeline
                temp_path = f"temp_{bhavcopy_file.name}"
                with open(temp_path, "wb") as f:
                    f.write(bhavcopy_file.getbuffer())
                    
                ingestion = OptionsDataIngestion(file_path=temp_path)
                raw_df = ingestion.load_bhavcopy()
                
                active_df = SpotAndExpiryEngine.filter_front_month_expiry(raw_df)
                synced_df = SpotAndExpiryEngine.sync_spot_prices(active_df)
                
                spreads_df = SpreadBuilderEngine.build_spreads(synced_df)
                
                if not spreads_df.empty:
                    # Apply optional UI strategy filter without hard-deleting underlying data
                    display_df = spreads_df[spreads_df["Strategy"].isin(strategy_filter)].copy()
                    
                    st.success(f"Generated {len(display_df)} setups ranked by Risk:Reward efficiency.")
                    
                    # Columns to render
                    cols_to_show = [
                        'Symbol', 'Strategy', 'Setup', 'Spot_Price', 
                        'Risk_Reward', 'Safety_Buffer_%', 'Lot_Size', 
                        'Max_Profit_₹', 'Max_Risk_₹', 'Wall_Strength', 'Wall_OI'
                    ]
                    
                    st.dataframe(
                        display_df[cols_to_show].style.background_gradient(
                            subset=['Safety_Buffer_%'], cmap='RdYlGn'
                        ).format({
                            'Spot_Price': '₹{:.2f}',
                            'Safety_Buffer_%': '{:.2f}%',
                            'Lot_Size': '{:,.0f}',
                            'Max_Profit_₹': '₹{:,.2f}',
                            'Max_Risk_₹': '₹{:,.2f}',
                            'Wall_OI': '{:,}'
                        }),
                        use_container_width=True,
                        hide_index=True
                    )
                else:
                    st.error("Engine failed to pair any valid credit spreads. Check data integrity.")
                    
            except Exception as e:
                st.error(f"Pipeline Error: {str(e)}")
            finally:
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)
                if temp_part_path and os.path.exists(temp_part_path):
                    os.remove(temp_part_path)
