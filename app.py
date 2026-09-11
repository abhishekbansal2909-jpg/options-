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

st.sidebar.header("2. Base Strategy Filter")
strategy_filter = st.sidebar.multiselect(
    "Select Strategies to Process", 
    options=["Bear Call Spread", "Bull Put Spread", "Iron Condor"],
    default=["Bear Call Spread", "Bull Put Spread", "Iron Condor"]
)

# ==========================================
# Core Execution Engine
# ==========================================
if st.sidebar.button("Run Quantitative Scan", type="primary"):
    if bhavcopy_file is None:
        st.sidebar.error("⚠️ Please upload a Bhavcopy file to proceed.")
    else:
        with st.spinner("Initializing Quantitative Engine & Calculating Greeks..."):
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

                # 2. Bhavcopy Extraction & Normalization
                temp_path = f"temp_{bhavcopy_file.name}"
                with open(temp_path, "wb") as f:
                    f.write(bhavcopy_file.getbuffer())
                    
                ingestion = OptionsDataIngestion(file_path=temp_path)
                raw_df = ingestion.load_bhavcopy()
                
                # 3. Market Context & Syncing
                active_df = SpotAndExpiryEngine.filter_front_month_expiry(raw_df)
                synced_df = SpotAndExpiryEngine.sync_market_context(active_df)
                
                # Isolate the context for the scoring engine
                market_context_df = synced_df[['Symbol', 'Spot_Price', 'ATR_14', 'Beta', 'Days_To_Event']].drop_duplicates()
                
                # 4. Glass-Box Scoring Engine (Delta, ATR Moats, Institutional Walls)
                current_date_str = datetime.now().strftime("%Y-%m-%d")
                quant_engine = QuantitativeScoringEngine(active_df, market_context_df)
                scored_df = quant_engine.run_glass_box_pipeline(current_date_str)
                
                # 5. Build Spreads
                st.session_state['spreads_df'] = SpreadBuilderEngine.build_spreads(scored_df)
                st.success("✅ Engine computation complete. Displaying algorithmic rankings.")
                    
            except Exception as e:
                st.error(f"Pipeline Error: {str(e)}")
            finally:
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)
                if temp_part_path and os.path.exists(temp_part_path):
                    os.remove(temp_part_path)

# ==========================================
# Dynamic Grid Filters (Glass-Box UI)
# ==========================================
if 'spreads_df' in st.session_state and not st.session_state['spreads_df'].empty:
    st.divider()
    st.subheader("🔍 Quantitative Filters")
    
    col1, col2, col3, col4, col5 = st.columns(5)
    
    with col1:
        search_symbol = st.text_input("Search Symbol", placeholder="e.g., RELIANCE")
    with col2:
        min_score = st.number_input("Min Composite Score", min_value=0, max_value=100, value=50, step=10)
    with col3:
        max_delta = st.number_input("Max Short Delta", min_value=0.01, max_value=1.00, value=0.15, step=0.01)
    with col4:
        max_rr = st.number_input("Max Risk:Reward Ratio", min_value=0.1, max_value=50.0, value=25.0, step=0.5)
    with col5:
        wall_filter = st.multiselect(
            "Wall Strength", 
            options=["🟢 Reinforced", "🟢 Dual Reinforced", "🔴 Crumbling", "🔴 Both Crumbling", "⚪ Neutral", "⚪ Mixed Strength"],
            default=["🟢 Reinforced", "🟢 Dual Reinforced", "⚪ Neutral", "⚪ Mixed Strength"]
        )

    # Apply filters dynamically to the session state dataframe
    display_df = st.session_state['spreads_df'].copy()
    display_df = display_df[display_df["Strategy"].isin(strategy_filter)]
    
    if search_symbol:
        display_df = display_df[display_df["Symbol"].str.contains(search_symbol.upper())]
        
    display_df = display_df[display_df["Score"] >= min_score]
    display_df = display_df[display_df["Short_Delta"].abs() <= max_delta]
    display_df = display_df[display_df["RR_Ratio"] <= max_rr]
    display_df = display_df[display_df["Wall_Strength"].isin(wall_filter)]
    
    st.caption(f"Showing **{len(display_df)}** statistically filtered setups.")
    
    # Render the advanced quantitative grid
    cols_to_show = [
        'Symbol', 'Score', 'Strategy', 'Setup', 'Spot_Price', 
        'Short_Delta', 'ATR_Moat', 'Risk_Reward', 'Net_Premium',
        'Max_Profit_₹', 'Max_Risk_₹', 'Wall_Strength'
    ]
    
    st.dataframe(
        display_df[cols_to_show].style.background_gradient(
            subset=['Score', 'ATR_Moat'], cmap='RdYlGn'
        ).background_gradient(
            subset=['Short_Delta'], cmap='RdYlGn_r'  # Reversed so lower Delta is green
        ).format({
            'Spot_Price': '₹{:.2f}',
            'Short_Delta': '{:.3f}',
            'ATR_Moat': '{:.2f}x',
            'Net_Premium': '₹{:.2f}',
            'Max_Profit_₹': '₹{:,.2f}',
            'Max_Risk_₹': '₹{:,.2f}',
        }),
        use_container_width=True,
        hide_index=True
    )
