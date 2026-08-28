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
    try:
        df = pd.read_csv(filepath)
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

st.sidebar.header("2. Base Strategy Filter")
strategy_filter = st.sidebar.multiselect(
    "Select Strategies to Process", 
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

                # 2. Bhavcopy Options Pipeline
                temp_path = f"temp_{bhavcopy_file.name}"
                with open(temp_path, "wb") as f:
                    f.write(bhavcopy_file.getbuffer())
                    
                ingestion = OptionsDataIngestion(file_path=temp_path)
                raw_df = ingestion.load_bhavcopy()
                
                active_df = SpotAndExpiryEngine.filter_front_month_expiry(raw_df)
                synced_df = SpotAndExpiryEngine.sync_spot_prices(active_df)
                
                # Cache the results in session state so filters can be used without re-running
                st.session_state['spreads_df'] = SpreadBuilderEngine.build_spreads(synced_df)
                st.success(f"Scan complete. Data loaded successfully.")
                    
            except Exception as e:
                st.error(f"Pipeline Error: {str(e)}")
            finally:
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)
                if temp_part_path and os.path.exists(temp_part_path):
                    os.remove(temp_part_path)

# ==========================================
# Dynamic Grid Filters (Only show if data exists)
# ==========================================
if 'spreads_df' in st.session_state and not st.session_state['spreads_df'].empty:
    st.divider()
    st.subheader("🔍 Dynamic Grid Filters")
    
    # 4-Column Layout for sleek UI
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        search_symbol = st.text_input("Search Symbol", placeholder="e.g., RELIANCE")
    with col2:
        min_safety = st.number_input("Minimum Safety Buffer (%)", min_value=0.0, max_value=50.0, value=0.0, step=0.5)
    with col3:
        max_rr = st.number_input("Max Risk:Reward Ratio (e.g., 5.0)", min_value=0.1, max_value=50.0, value=20.0, step=0.5)
    with col4:
        wall_filter = st.multiselect(
            "Wall Strength", 
            options=["🟢 Reinforced", "🔴 Crumbling", "⚪ Neutral"],
            default=["🟢 Reinforced", "⚪ Neutral"] # Defaults to hiding crumbling walls
        )

    # Apply the filters to the cached dataframe
    display_df = st.session_state['spreads_df'].copy()
    display_df = display_df[display_df["Strategy"].isin(strategy_filter)]
    
    if search_symbol:
        display_df = display_df[display_df["Symbol"].str.contains(search_symbol.upper())]
        
    display_df = display_df[display_df["Safety_Buffer_%"] >= min_safety]
    display_df = display_df[display_df["RR_Ratio"] <= max_rr]
    display_df = display_df[display_df["Wall_Strength"].isin(wall_filter)]
    
    # Render the final filtered grid
    st.caption(f"Showing **{len(display_df)}** setups matching your criteria.")
    
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
