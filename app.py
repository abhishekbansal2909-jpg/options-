
import streamlit as st
import pandas as pd
import os
from datetime import datetime
from engine import OptionsDataIngestion, QuantitativeScoringEngine
from spotexpiry import SpotAndExpiryEngine
from spread_builder import SpreadBuilderEngine

st.set_page_config(page_title="Quantitative Spread Engine", layout="wide")
st.title("🦅 Quantitative Credit Spread Dashboard")

st.sidebar.header("1. Data Ingestion")
bhavcopy_file = st.sidebar.file_uploader("Upload NSE Bhavcopy (CSV/ZIP)", type=['csv', 'zip'])

st.sidebar.header("2. Base Filters")
expiry_filter = st.sidebar.radio("Select Expiry Cycle", options=["Both", "Near", "Next"], index=0)
strategy_filter = st.sidebar.multiselect(
    "Select Strategies to Process", 
    options=["Bear Call Spread", "Bull Put Spread", "Iron Condor"],
    default=["Bear Call Spread", "Bull Put Spread", "Iron Condor"]
)

if st.sidebar.button("Run Quantitative Scan", type="primary"):
    if bhavcopy_file is None:
        st.sidebar.error("⚠️ Please upload a Bhavcopy file.")
    else:
        with st.spinner("Processing local database and quantitative metrics..."):
            temp_path = bhavcopy_file.name
            with open(temp_path, "wb") as f:
                f.write(bhavcopy_file.getvalue())
                
            try:
                ingestion = OptionsDataIngestion(file_path=temp_path)
                raw_df = ingestion.load_bhavcopy()
                
                active_df = SpotAndExpiryEngine.filter_active_expiries(raw_df, include_next_month=True)
                synced_df = SpotAndExpiryEngine.sync_market_context(active_df)
                
                market_cols = ['Symbol', 'Spot_Price', 'ATR_14', 'Beta', 'Days_To_Event', 'EMA_20']
                available_cols = [c for c in market_cols if c in synced_df.columns]
                market_context = synced_df[available_cols].drop_duplicates()
                
                quant_engine = QuantitativeScoringEngine(active_df, market_context)
                scored_df = quant_engine.run_glass_box_pipeline(datetime.now().strftime("%Y-%m-%d"))
                
                spreads_df = SpreadBuilderEngine.build_spreads(scored_df)
                st.session_state['spreads_df'] = spreads_df
                st.success("✅ Engine computation complete.")
            except Exception as e:
                st.error(f"Pipeline Error: {e}")
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

if 'spreads_df' in st.session_state and not st.session_state['spreads_df'].empty:
    st.divider()
    display_df = st.session_state['spreads_df'].copy()
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        search_sym = st.text_input("Search Symbol").upper()
    with col2:
        regime_filter = st.multiselect("Regime Alignment", ["🟢 Trend Aligned", "🔴 Counter-Trend"], default=["🟢 Trend Aligned"])
    with col3:
        min_score = st.number_input("Min Score", value=50, step=10)
    with col4:
        max_delta = st.number_input("Max Short Delta", value=0.20, step=0.01)

    if expiry_filter != "Both" and 'Expiry_Cycle' in display_df.columns:
        display_df = display_df[display_df["Expiry_Cycle"] == expiry_filter]
        
    display_df = display_df[display_df["Strategy"].isin(strategy_filter)]
    if search_sym:
        display_df = display_df[display_df["Symbol"].str.contains(search_sym)]
        
    display_df = display_df[display_df["Score"] >= min_score]
    display_df = display_df[display_df["Short_Delta"].abs() <= max_delta]
    
    if 'Regime' in display_df.columns:
        display_df = display_df[display_df["Regime"].isin(regime_filter)]
        
    st.dataframe(display_df, use_container_width=True, hide_index=True)
