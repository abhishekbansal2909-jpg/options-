import streamlit as st
import pandas as pd
import os
from engine import OptionsDataIngestion
from spotexpiry import SpotAndExpiryEngine
from spread_builder import SpreadBuilderEngine

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
# Added File Uploader
uploaded_file = st.sidebar.file_uploader("Upload NSE Bhavcopy (ZIP/CSV)", type=['csv', 'zip'])

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
    if uploaded_file is None:
        st.sidebar.error("⚠️ Please upload a Bhavcopy file first.")
    else:
        with st.spinner("Ingesting F&O Data & Syncing Spot Prices..."):
            try:
                # Save the uploaded file temporarily so the backend can read it
                temp_path = f"temp_{uploaded_file.name}"
                with open(temp_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                    
                # Step 1: Ingest NSE CSV Data
                ingestion = OptionsDataIngestion(file_path=temp_path)
                raw_df = ingestion.load_bhavcopy()
                
                # Step 2: Lock Active Expiry & Fetch Live Cash Prices
                active_df = SpotAndExpiryEngine.filter_front_month_expiry(raw_df)
                synced_df = SpotAndExpiryEngine.sync_spot_prices(active_df)
                
                # Step 3: Build Spreads & Calculate Yields
                spreads_df = SpreadBuilderEngine.build_spreads(synced_df, margin_per_lot=margin_per_lot)
                
                if not spreads_df.empty:
                    # Step 4: Filter by User's Target ROI
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
                    
                # Clean up the temporary file
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                    
            except Exception as e:
                st.error(f"Pipeline Error: {str(e)}")
                # Clean up in case of error
                if os.path.exists(temp_path):
                    os.remove(temp_path)
