import pandas as pd

# Standard NSE F&O Lot Sizes (You can expand this list)
LOT_SIZES = {
    "RELIANCE": 250, "TCS": 175, "INFY": 400, "HDFCBANK": 550, "ICICIBANK": 700,
    "SBIN": 750, "BHARTIARTL": 475, "ITC": 1600, "LT": 150, "BAJFINANCE": 125,
    "KOTAKBANK": 400, "LUPIN": 850, "PRESTIGE": 275
}

class SpreadBuilderEngine:
    """Pairs OTM option strikes into vertical credit spreads and calculates ROI."""
    
    @staticmethod
    def build_spreads(df: pd.DataFrame, margin_per_lot: float = 40000.0) -> pd.DataFrame:
        """
        Builds spreads. 
        'margin_per_lot' is dynamic and can be passed from the Streamlit UI.
        """
        spreads = []
        
        # Group by underlying stock
        for sym, group in df.groupby("Symbol"):
            spot_price = group['Spot_Price'].iloc[0]
            lot_size = LOT_SIZES.get(sym, 500) # Default to 500 if missing
            
            # ----------------------------------------------------
            # 1. BEAR CALL SPREAD (Hunting the Ceiling)
            # ----------------------------------------------------
            ce_data = group[(group['Option_Type'] == 'CE') & (group['Strike'] > spot_price)].copy()
            ce_data = ce_data.sort_values(by='Strike', ascending=True) # Closest to spot first
            
            if len(ce_data) >= 2:
                # Find the Wall (Highest OI in OTM territory)
                ce_wall = ce_data.loc[ce_data['OI'].idxmax()]
                short_strike_ce = ce_wall['Strike']
                
                # The Hedge (Next available strike ABOVE the wall)
                ce_hedge_data = ce_data[ce_data['Strike'] > short_strike_ce]
                if not ce_hedge_data.empty:
                    ce_hedge = ce_hedge_data.iloc[0]
                    long_strike_ce = ce_hedge['Strike']
                    
                    net_premium_ce = round(ce_wall['LTP'] - ce_hedge['LTP'], 2)
                    gross_credit_ce = net_premium_ce * lot_size
                    roi_ce = round((gross_credit_ce / margin_per_lot) * 100, 2)
                    safety_buffer_ce = round(((short_strike_ce - spot_price) / spot_price) * 100, 2)
                    
                    if net_premium_ce > 0:
                        spreads.append({
                            "Symbol": sym,
                            "Spot_Price": spot_price,
                            "Strategy": "Bear Call Spread",
                            "Setup": f"Sell {short_strike_ce} CE / Buy {long_strike_ce} CE",
                            "Safety_Buffer_%": safety_buffer_ce,
                            "Net_Premium": net_premium_ce,
                            "Gross_Credit": gross_credit_ce,
                            "ROI_%": roi_ce,
                            "Wall_OI": ce_wall['OI']
                        })

            # ----------------------------------------------------
            # 2. BULL PUT SPREAD (Hunting the Floor)
            # ----------------------------------------------------
            pe_data = group[(group['Option_Type'] == 'PE') & (group['Strike'] < spot_price)].copy()
            pe_data = pe_data.sort_values(by='Strike', ascending=False) # Closest to spot first
            
            if len(pe_data) >= 2:
                # Find the Wall (Highest OI in OTM territory)
                pe_wall = pe_data.loc[pe_data['OI'].idxmax()]
                short_strike_pe = pe_wall['Strike']
                
                # The Hedge (Next available strike BELOW the wall)
                pe_hedge_data = pe_data[pe_data['Strike'] < short_strike_pe]
                if not pe_hedge_data.empty:
                    pe_hedge = pe_hedge_data.iloc[0]
                    long_strike_pe = pe_hedge['Strike']
                    
                    net_premium_pe = round(pe_wall['LTP'] - pe_hedge['LTP'], 2)
                    gross_credit_pe = net_premium_pe * lot_size
                    roi_pe = round((gross_credit_pe / margin_per_lot) * 100, 2)
                    safety_buffer_pe = round(((spot_price - short_strike_pe) / spot_price) * 100, 2)
                    
                    if net_premium_pe > 0:
                        spreads.append({
                            "Symbol": sym,
                            "Spot_Price": spot_price,
                            "Strategy": "Bull Put Spread",
                            "Setup": f"Sell {short_strike_pe} PE / Buy {long_strike_pe} PE",
                            "Safety_Buffer_%": safety_buffer_pe,
                            "Net_Premium": net_premium_pe,
                            "Gross_Credit": gross_credit_pe,
                            "ROI_%": roi_pe,
                            "Wall_OI": pe_wall['OI']
                        })

        # Return as a DataFrame sorted by best ROI
        final_df = pd.DataFrame(spreads)
        if not final_df.empty:
            final_df = final_df.sort_values(by="ROI_%", ascending=False)
            
        return final_df
