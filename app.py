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
    """Calculates FII Long/Short Ratio from Participant OI CSV."""
    try:
        df = pd.read_csv(filepath)
        
        # Handle variable header rows dynamically
        if not any("CLIENT" in str(c).upper() for c in df.columns) and not any("FUTURE" in str(c).upper() for c in df.columns):
            for idx in range(min(5, len(df))):
                row_str = " ".join(df.iloc[idx].astype(str).str.upper())
                if "CLIENT" in row_str or "PARTICIPANT" in row_str:
                    df = pd.read_csv(filepath, skiprows=idx + 1)
                    break
                    
        df.columns = df.columns.astype(str).str.strip().str.upper().str.replace(" ", "_").str.replace("\t", "")
        
        client_col = next((c for c in df.columns if any(k in c for k in ["CLIENT", "PARTIC"])), df.columns[0] if len(df.columns) > 0 else None)
        fut_long_col = next((c for c in df.columns if "FUTURE_INDEX_LONG" in c or "FUTIDX_LONG" in c), None)
        fut_short_col = next((c for c in df.columns if "FUTURE_INDEX_SHORT" in c or "FUTIDX_SHORT" in c), None)

        if not client_col or not fut_long_col or not fut_short_col:
            return "UNAVAILABLE (Header Mismatch)", 50.0
            
        df[client_col] = df[client_col].astype(str).str.strip().str.upper()
        fii_rows = df[df[client_col].str.contains("FII|FPI|FOREIGN", case=False, na=False)]
        
        if fii_rows.empty:
            return "UNAVAILABLE (FII Not Found)", 50.0
            
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
st.markdown("Identify high-probability structural walls and calculate optimal OTM credit spreads.")

# ==========================================
# Sidebar Controls (Dynamic Inputs)
# ==========================================
st.sidebar.header("1. Data Ingestion")
bhavcopy_file = st.sidebar.file_uploader("Upload NSE Bhavcopy (ZIP/CSV)", type=['csv', 'zip'])
participant_file = st.sidebar.file_uploader("Upload Participant OI (CSV)", type=['csv'])

st.sidebar.header("2. Execution Parameters")
margin_per_lot = st.sidebar.number_input(
    "Estimated Margin Per Lot (₹)", 
    min_value=10000, 
    max_value=200000, 
    value=40000, 
    step=2000
)

min_roi_target = st.sidebar.slider(
    "Minimum Gross ROI Target (%)", 
    min_value=1.0, 
    max_value=15.0, 
    value=6.0, 
    step=0.5
)

# ==========================================
# Core Execution Engine
# ==========================================
if st.sidebar.button("Run Quantitative Scan"):
    if bhavcopy_file is None:
        st.sidebar.error("⚠️ Please upload a Bhavcopy file to proceed.")
    else:
        with st.spinner("Ingesting Data & Processing..."):
            try:
                # --- Handle Participant OI File (Optional but Recommended) ---
                if participant_file is not None:
                    temp_part_path = f"temp_{participant_file.name}"
                    with open(temp_part_path, "wb") as f:
                        f.write(participant_file.getbuffer())
                    
                    tide_status, tide_ratio = get_fii_tide(temp_part_path)
                    st.info(f"**MACRO TIDE:** {tide_status} | **FII Long Ratio:** {tide_ratio}%")
                    
                    if os.path.exists(temp_part_path):
                        os.remove(temp_part_path)
                else:
                    st.warning("No Participant OI file uploaded. Macro Tide filter skipped.")

                # --- Handle Bhavcopy File ---
                temp_path = f"temp_{bhavcopy_file.name}"
                with open(temp_path, "wb") as f:
                    f.write(bhavcopy_file.getbuffer())
                    
                ingestion = OptionsDataIngestion(file_path=temp_path)
                raw_df = ingestion.load_bhavcopy()
                
                active_df = SpotAndExpiryEngine.filter_front_month_expiry(raw_df)
                synced_df = SpotAndExpiryEngine.sync_spot_prices(active_df)
                
                spreads_df = SpreadBuilderEngine.build_spreads(synced_df, margin_per_lot=margin_per_lot)
                
                if not spreads_df.empty:
                    qualified_setups = spreads_df[spreads_df["ROI_%"] >= min_roi_target]
                    
                    if not qualified_setups.empty:
                        st.success(f"Scan Complete! Found {len(qualified_setups)} setups clearing the {min_roi_target}% yield threshold.")
                        
                        st.dataframe(
                            qualified_setups.style.background_gradient(
                                subset=['ROI_%', 'Safety_Buffer_%'], cmap='RdYlGn'
                            ).format({
                                'Safety_Buffer_%': '{:.2f}%',
                                'ROI_%': '{:.2f}%',
                                'Net_Premium': '₹{:.2f}',
                                'Gross_Credit': '₹{:.2f}'
                            }),
                            use_container_width=True,
                            hide_index=True
                        )
                    else:
                        st.warning(f"No setups met the minimum {min_roi_target}% ROI target. Adjust parameters to widen the search.")
                else:
                    st.error("Engine failed to pair any valid credit spreads. Check data integrity.")
                    
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                    
            except Exception as e:
                st.error(f"Pipeline Error: {str(e)}")
                if 'temp_path' in locals() and os.path.exists(temp_path):
                    os.remove(temp_path)
                if 'temp_part_path' in locals() and os.path.exists(temp_part_path):
                    os.remove(temp_part_path)
