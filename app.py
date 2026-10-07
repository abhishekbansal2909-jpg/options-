import streamlit as st
import pandas as pd
import os
from datetime import datetime
from engine import OptionsDataIngestion, QuantitativeScoringEngine
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
st.set_page_config(page_title="Quantitative Spread Engine", layout="wide", initial_sidebar_state="expanded")
st.title("🦅 Quantitative Credit Spread Dashboard")
st.markdown("Algorithmic Underwriting Engine | Ranked by Composite Safety Score & Institutional Boundaries")

# ==========================================
# Sidebar Controls
# ==========================================
st.sidebar.header("1. Data Ingestion")
bhavcopy_file = st.sidebar.file_uploader("Upload NSE Bhavcopy (ZIP/CSV)", type=['csv', 'zip'])
participant_file = st.sidebar.file_uploader("Upload Participant OI (CSV)", type=['csv'])

st.sidebar.header("2. Base Filters")
universe_filter = st.sidebar.radio(
    "Liquidity Universe (Speed)",
    options=["Top 50 Liquid (Fast)", "Top 100 Liquid (Balanced)", "All F&O (Slow)"],
    index=0
)

expiry_filter = st.sidebar.radio(
    "Select Expiry Cycle",
    options=["Both", "Near", "Next"],
    index=0
)

strategy_filter = st.sidebar.multiselect(
    "Select Strategies to Process", 
    options=["Bear Call Spread", "Bull Put Spread", "Iron Condor"],
    default=["Bear Call Spread", "Bull Put Spread", "Iron Condor"]
)

# ==========================================
# Core Execution Engine (Cached)
# ==========================================
@st.cache_data(show_spinner=False)
def run_quant_pipeline(_bhavcopy_bytes, bhavcopy_name, _participant_bytes, _universe_filter):
    temp_path = f"temp_{bhavcopy_name}"
    temp_part_path = "temp_participant.csv" if _participant_bytes else None
    
    try:
        with open(temp_path, "wb") as f:
            f.write(_bhavcopy_bytes)
        if temp_part_path:
            with open(temp_part_path, "wb") as f:
                f.write(_participant_bytes)
                
        # 1. Macro Tide
        tide_info = None
        if temp_part_path:
            tide_status, tide_ratio = get_fii_tide(temp_part_path)
            tide_info = f"**MACRO TIDE:** {tide_status} | **FII Long Ratio:** {tide_ratio}%"

        # 2. Bhavcopy Extraction & Liquidity Filtering
        max_symbols = 50 if "50" in _universe_filter else (100 if "100" in _universe_filter else None)
        ingestion = OptionsDataIngestion(file_path=temp_path, max_symbols=max_symbols)
        raw_df = ingestion.load_bhavcopy()
        
        # 3. Market Context & Syncing 
        active_df = SpotAndExpiryEngine.filter_active_expiries(raw_df, include_next_month=True)
        synced_df = SpotAndExpiryEngine.sync_market_context(active_df)
        
        # Removed EMA columns from expected context
        market_context_cols = ['Symbol', 'Spot_Price', 'ATR_14', 'Beta', 'Days_To_Event']
        available_context = [c for c in market_context_cols if c in synced_df.columns]
        market_context_df = synced_df[available_context].drop_duplicates()
        
        # 4. Glass-Box Scoring Engine (No EMA Dependency)
        current_date_str = datetime.now().strftime("%Y-%m-%d")
        quant_engine = QuantitativeScoringEngine(active_df, market_context_df)
        scored_df = quant_engine.run_glass_box_pipeline(current_date_str)
        
        # 5. Build Spreads
        spreads_df = SpreadBuilderEngine.build_spreads(scored_df)
        return spreads_df, tide_info
        
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        if temp_part_path and os.path.exists(temp_part_path):
            os.remove(temp_part_path)

if st.sidebar.button("Run Quantitative Scan", type="primary"):
    if bhavcopy_file is None:
        st.sidebar.error("⚠️ Please upload a Bhavcopy file to proceed.")
    else:
        with st.spinner("Initializing Volatility Engine & Calculating Greeks..."):
            try:
                bhav_bytes = bhavcopy_file.getvalue()
                bhav_name = bhavcopy_file.name
                part_bytes = participant_file.getvalue() if participant_file else None
                
                spreads_df, tide_info = run_quant_pipeline(bhav_bytes, bhav_name, part_bytes, universe_filter)
                
                if tide_info:
                    st.info(tide_info)
                    
                st.session_state['spreads_df'] = spreads_df
                st.success("✅ Engine computation complete. Execute manual trend verification before entry.")
            except Exception as e:
                st.error(f"Pipeline Error: {str(e)}")

# ==========================================
# Dynamic Grid Filters (Glass-Box UI)
# ==========================================
if 'spreads_df' in st.session_state and not st.session_state['spreads_df'].empty:
    st.divider()
    st.subheader("🔍 Quantitative Filters")
    
    # Reduced to 4 columns (Removed Regime Alignment)
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        search_symbol = st.text_input("Search Symbol", placeholder="e.g., RELIANCE")
    with col2:
        min_score = st.number_input("Min Composite Score", min_value=0, max_value=100, value=50, step=10)
    with col3:
        max_delta = st.number_input("Max Short Delta", min_value=0.01, max_value=1.00, value=0.20, step=0.01)
    with col4:
        wall_filter = st.multiselect(
            "Wall Strength", 
            options=["🟢 Reinforced", "🟢 Dual Reinforced", "🔴 Crumbling", "🔴 Both Crumbling", "⚪ Neutral", "⚪ Mixed Strength"],
            default=["🟢 Reinforced", "🟢 Dual Reinforced", "⚪ Neutral", "⚪ Mixed Strength"]
        )

    display_df = st.session_state['spreads_df'].copy()
    
    if expiry_filter != "Both" and 'Expiry_Cycle' in display_df.columns:
        display_df = display_df[display_df["Expiry_Cycle"] == expiry_filter]
            
    display_df = display_df[display_df["Strategy"].isin(strategy_filter)]
    
    if search_symbol:
        display_df = display_df[display_df["Symbol"].str.contains(search_symbol.upper())]
        
    display_df = display_df[display_df["Score"] >= min_score]
    display_df = display_df[display_df["Short_Delta"].abs() <= max_delta]
        
    if 'Wall_Strength' in display_df.columns:
        display_df = display_df[display_df["Wall_Strength"].isin(wall_filter)]
    
    st.caption(f"Showing **{len(display_df)}** statistically filtered setups.")
    
    available_cols = display_df.columns.tolist()
    
    # Removed Regime and EMA metrics from display
    cols_to_show = [
        'Symbol', 'Score', 'Strategy', 'Setup', 'Spot_Price',
        'Expiry_Date', 'DTE', 'Short_Delta', 'ATR_Moat', 'Risk_Reward', 'Net_Premium',
        'Max_Profit_₹', 'Max_Risk_₹', 'Wall_Strength', 'Wall_OI', 'L2_Execution_Risk'
    ]
    cols_to_show = [c for c in cols_to_show if c in available_cols]
    
    format_dict = {
        'Spot_Price': '₹{:.2f}',
        'Short_Delta': '{:.3f}',
        'ATR_Moat': '{:.2f}x',
        'Net_Premium': '₹{:.2f}',
        'Max_Profit_₹': '₹{:,.2f}',
        'Max_Risk_₹': '₹{:,.2f}',
        'Wall_OI': '{:,}' 
    }
    format_dict = {k: v for k, v in format_dict.items() if k in cols_to_show}
    
    st.dataframe(
        display_df[cols_to_show].style.background_gradient(
            subset=['Score'] if 'Score' in cols_to_show else [], cmap='RdYlGn', vmin=0, vmax=100
        ).background_gradient(
            subset=['ATR_Moat'] if 'ATR_Moat' in cols_to_show else [], cmap='RdYlGn'
        ).background_gradient(
            subset=['Short_Delta'] if 'Short_Delta' in cols_to_show else [], cmap='RdYlGn_r'  
        ).format(format_dict),
        use_container_width=True,
        hide_index=True
    )

