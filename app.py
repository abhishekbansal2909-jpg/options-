import streamlit as st
import pandas as pd
import os
from datetime import datetime
from engine import OptionsDataIngestion, QuantitativeScoringEngine
from spotexpiry import SpotAndExpiryEngine
from spread_builder import SpreadBuilderEngine

st.set_page_config(page_title="Quantitative Spread Engine", layout="wide", initial_sidebar_state="expanded")
st.title("🦅 Quantitative Credit Spread Dashboard")
st.markdown("Algorithmic Underwriting Engine | Ranked by Composite Safety Score & Institutional Boundaries")

st.sidebar.header("1. Data Ingestion")
bhavcopy_file = st.sidebar.file_uploader("Upload NSE Bhavcopy (ZIP/CSV)", type=['csv', 'zip'])

st.sidebar.header("2. Base Filters")
expiry_filter = st.sidebar.radio("Select Expiry Cycle", options=["Both", "Near", "Next"], index=0)
strategy_filter = st.sidebar.multiselect(
    "Select Strategies to Process", 
    options=["Bear Call Spread", "Bull Put Spread", "Iron Condor"],
    default=["Bear Call Spread", "Bull Put Spread", "Iron Condor"]
)

@st.cache_data(show_spinner=False)
def run_quant_pipeline(_bhavcopy_bytes, bhavcopy_name):
    temp_path = f"temp_{bhavcopy_name}"
    
    try:
        with open(temp_path, "wb") as f:
            f.write(_bhavcopy_bytes)
                
        ingestion = OptionsDataIngestion(file_path=temp_path)
        raw_df = ingestion.load_bhavcopy()
        
        active_df = SpotAndExpiryEngine.filter_active_expiries(raw_df, include_next_month=True)
        synced_df = SpotAndExpiryEngine.sync_market_context(active_df)
        
        market_context_cols = ['Symbol', 'Spot_Price', 'ATR_14', 'Beta', 'Days_To_Event', 'EMA_20', 'EMA_DIST_PCT']
        available_context = [c for c in market_context_cols if c in synced_df.columns]
        market_context_df = synced_df[available_context].drop_duplicates()
        
        current_date_str = datetime.now().strftime("%Y-%m-%d")
        quant_engine = QuantitativeScoringEngine(active_df, market_context_df)
        scored_df = quant_engine.run_glass_box_pipeline(current_date_str)
        
        spreads_df = SpreadBuilderEngine.build_spreads(scored_df)
        return spreads_df
        
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)

if st.sidebar.button("Run Quantitative Scan", type="primary"):
    if bhavcopy_file is None:
        st.sidebar.error("⚠️ Please upload a Bhavcopy file to proceed.")
    else:
        with st.spinner("Initializing Quantitative Engine & Merging Database Context..."):
            try:
                bhav_bytes = bhavcopy_file.getvalue()
                bhav_name = bhavcopy_file.name
                
                spreads_df = run_quant_pipeline(bhav_bytes, bhav_name)
                st.session_state['spreads_df'] = spreads_df
                st.success("✅ Engine computation complete.")
            except Exception as e:
                st.error(f"Pipeline Error: {str(e)}")

if 'spreads_df' in st.session_state and not st.session_state['spreads_df'].empty:
    st.divider()
    st.subheader("🔍 Quantitative Filters")
    
    col1, col2, col3, col4, col5 = st.columns(5)
    
    with col1:
        search_symbol = st.text_input("Search Symbol", placeholder="e.g., RELIANCE")
    with col2:
        regime_filter = st.multiselect(
            "Regime Alignment", 
            options=["🟢 Trend Aligned", "🔴 Counter-Trend", "🟢 Range Bound", "🔴 Expanding"],
            default=["🟢 Trend Aligned", "🟢 Range Bound"]
        )
    with col3:
        min_score = st.number_input("Min Composite Score", min_value=-50, max_value=100, value=50, step=10)
    with col4:
        max_delta = st.number_input("Max Short Delta", min_value=0.01, max_value=1.00, value=0.20, step=0.01)
    with col5:
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
    
    if 'Regime' in display_df.columns:
        display_df = display_df[display_df["Regime"].isin(regime_filter)]
        
    if 'Wall_Strength' in display_df.columns:
        display_df = display_df[display_df["Wall_Strength"].isin(wall_filter)]
        
    # Sort logically
    if 'Net_Premium' in display_df.columns and 'Score' in display_df.columns:
        display_df = display_df.sort_values(by=['Score', 'Net_Premium'], ascending=[False, False])
    elif 'Score' in display_df.columns:
        display_df = display_df.sort_values(by=['Score'], ascending=False)
    
    st.caption(f"Showing **{len(display_df)}** statistically filtered setups.")
    
    available_cols = display_df.columns.tolist()
    
    cols_to_show = [
        'Symbol', 'Regime', 'Score', 'Strategy', 'Setup', 'Spot_Price', 'EMA_20', 'EMA_Dist_%',
        'Expiry_Date', 'DTE', 'Short_Delta', 'ATR_Moat', 'Risk_Reward', 'Net_Premium',
        'Max_Profit_₹', 'Max_Risk_₹', 'Wall_Strength', 'Wall_OI', 'L2_Execution_Risk'
    ]
    
    cols_to_show = [c for c in cols_to_show if c in available_cols]
    
    format_dict = {
        'Spot_Price': '₹{:.2f}', 'EMA_20': '₹{:.2f}', 'EMA_Dist_%': '{:.2f}%',
        'Short_Delta': '{:.3f}', 'ATR_Moat': '{:.2f}x', 'Net_Premium': '₹{:.2f}',
        'Max_Profit_₹': '₹{:,.2f}', 'Max_Risk_₹': '₹{:,.2f}', 'Wall_OI': '{:,}' 
    }
    
    format_dict = {k: v for k, v in format_dict.items() if k in cols_to_show}
    
    st.dataframe(
        display_df[cols_to_show].style.background_gradient(
            subset=['Score'] if 'Score' in cols_to_show else [], cmap='RdYlGn', vmin=-40, vmax=100
        ).background_gradient(
            subset=['ATR_Moat'] if 'ATR_Moat' in cols_to_show else [], cmap='RdYlGn'
        ).background_gradient(
            subset=['Short_Delta'] if 'Short_Delta' in cols_to_show else [], cmap='RdYlGn_r'  
        ).format(format_dict),
        use_container_width=True, hide_index=True
    )
