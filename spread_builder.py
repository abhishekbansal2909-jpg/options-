import pandas as pd
import requests

class SpreadBuilderEngine:
    """Pairs OTM option strikes into vertical credit spreads and Iron Condors, computes R:R, and extracts quantitative engine flags."""
    
    _lot_sizes = {}

    @classmethod
    def fetch_lot_sizes(cls):
        if cls._lot_sizes: 
            return cls._lot_sizes
        
        try:
            url = "https://api.kite.trade/instruments"
            df = pd.read_csv(url)
            nfo_df = df[df['exchange'] == 'NFO'].copy()
            lot_map = nfo_df.drop_duplicates(subset=['name'])[['name', 'lot_size']]
            
            cls._lot_sizes = pd.Series(
                lot_map['lot_size'].values, 
                index=lot_map['name'].str.strip().str.upper()
            ).to_dict()
        except Exception as e:
            print(f"Kite lot size fetch failed: {e}")
            
        return cls._lot_sizes

    @staticmethod
    def build_spreads(df: pd.DataFrame) -> pd.DataFrame:
        lot_dict = SpreadBuilderEngine.fetch_lot_sizes()
        spreads = []
        
        if 'Expiry_Date' not in df.columns:
            print("⚠️ Expiry_Date missing from data. Defaulting to single-cycle grouping.")
            df['Expiry_Date'] = 'Unknown'
            df['Expiry_Cycle'] = 'Near'
            df['DTE'] = 0

        for (sym, expiry_date), group in df.groupby(["Symbol", "Expiry_Date"]):
            spot_price = group['Spot_Price'].iloc[0]
            lot_size = lot_dict.get(sym, 1) 
            
            # Extract time metadata
            expiry_str = expiry_date.strftime('%Y-%m-%d') if isinstance(expiry_date, pd.Timestamp) else str(expiry_date)
            cycle = group.get('Expiry_Cycle', pd.Series(['Unknown'])).iloc[0]
            dte = group.get('DTE', pd.Series([0])).iloc[0]
            
            # Extract Glass Engine Trend Metrics
            ema_20 = group.get('EMA_20', pd.Series([spot_price])).iloc[0]
            ema_dist_pct = group.get('EMA_DIST_PCT', pd.Series([0.0])).iloc[0]
            
            ce_candidate = None
            pe_candidate = None
                
            # ----------------------------------------------------
            # BEAR CALL SPREAD 
            # ----------------------------------------------------
            ce_data = group[(group['Option_Type'] == 'CE') & (group['Strike'] > spot_price)].copy()
            ce_data = ce_data.sort_values(by='Strike', ascending=True)
            
            if len(ce_data) >= 2:
                ce_wall = ce_data.loc[ce_data['OI'].idxmax()]
                short_strike_ce = ce_wall['Strike']
                
                ce_hedge_data = ce_data[ce_data['Strike'] > short_strike_ce]
                if not ce_hedge_data.empty:
                    ce_hedge = ce_hedge_data.iloc[0]
                    long_strike_ce = ce_hedge['Strike']
                    
                    net_prem_ce = round(ce_wall['LTP'] - ce_hedge['LTP'], 2)
                    spread_width_ce = round(long_strike_ce - short_strike_ce, 2)
                    max_risk_ce = round(spread_width_ce - net_prem_ce, 2)
                    
                    if net_prem_ce > 0 and max_risk_ce > 0:
                        rr_ratio_ce = round(max_risk_ce / net_prem_ce, 2)
                        delta_ce = round(ce_wall.get('Delta', 0.0), 3)
                        moat_atr_ce = round(ce_wall.get('Moat_ATR', 0.0), 2)
                        score_ce = ce_wall.get('Composite_Score', 0)
                        
                        oi_chg_ce = ce_wall.get('OI_Change', 0)
                        wall_str_ce = "🟢 Reinforced" if oi_chg_ce > 0 else ("🔴 Crumbling" if oi_chg_ce < 0 else "⚪ Neutral")
                        
                        # Regime Evaluation & Delta Calibration
                        regime_ce = "🟢 Trend Aligned" if spot_price < ema_20 else "🔴 Counter-Trend"
                        pass_delta_ce = bool(0.10 <= abs(delta_ce) <= 0.20) # Widened band for Bear Calls
                        
                        ce_candidate = {
                            "short_strike": short_strike_ce, "long_strike": long_strike_ce,
                            "net_prem": net_prem_ce, "spread_width": spread_width_ce,
                            "safety": round(((short_strike_ce - spot_price) / spot_price) * 100, 2),
                            "oi_chg": oi_chg_ce, "wall_oi": int(ce_wall['OI']),
                            "score": score_ce, "delta": delta_ce, "moat_atr": moat_atr_ce
                        }
                        
                        spreads.append({
                            "Symbol": sym, "Strategy": "Bear Call Spread", "Score": score_ce,
                            "Regime": regime_ce, "EMA_20": ema_20, "EMA_Dist_%": ema_dist_pct,
                            "Expiry_Date": expiry_str, "Expiry_Cycle": cycle, "DTE": int(dte),
                            "Setup": f"Sell {short_strike_ce} CE / Buy {long_strike_ce} CE",
                            "Spot_Price": spot_price,
                            "Risk_Reward": f"{rr_ratio_ce}:1",
                            "RR_Ratio": rr_ratio_ce, "Net_Premium": net_prem_ce,
                            "Short_Delta": delta_ce, "ATR_Moat": moat_atr_ce,
                            "Pass_Delta": pass_delta_ce, 
                            "Pass_Moat": ce_wall.get('Pass_Moat', False),
                            "Max_Profit_₹": round(net_prem_ce * lot_size, 2),
                            "Max_Risk_₹": round(max_risk_ce * lot_size, 2),
                            "Lot_Size": lot_size,
                            "Wall_Strength": wall_str_ce,
                            "Wall_OI": int(ce_wall['OI']),
                            "L2_Execution_Risk": "Awaiting L2 Check"
                        })

            # ----------------------------------------------------
            # BULL PUT SPREAD 
            # ----------------------------------------------------
            pe_data = group[(group['Option_Type'] == 'PE') & (group['Strike'] < spot_price)].copy()
            pe_data = pe_data.sort_values(by='Strike', ascending=False)
            
            if len(pe_data) >= 2:
                pe_wall = pe_data.loc[pe_data['OI'].idxmax()]
                short_strike_pe = pe_wall['Strike']
                
                pe_hedge_data = pe_data[pe_data['Strike'] < short_strike_pe]
                if not pe_hedge_data.empty:
                    pe_hedge = pe_hedge_data.iloc[0]
                    long_strike_pe = pe_hedge['Strike']
                    
                    net_prem_pe = round(pe_wall['LTP'] - pe_hedge['LTP'], 2)
                    spread_width_pe = round(short_strike_pe - long_strike_pe, 2)
                    max_risk_pe = round(spread_width_pe - net_prem_pe, 2)
                    
                    if net_prem_pe > 0 and max_risk_pe > 0:
                        rr_ratio_pe = round(max_risk_pe / net_prem_pe, 2)
                        delta_pe = round(pe_wall.get('Delta', 0.0), 3)
                        moat_atr_pe = round(pe_wall.get('Moat_ATR', 0.0), 2)
                        score_pe = pe_wall.get('Composite_Score', 0)
                        
                        oi_chg_pe = pe_wall.get('OI_Change', 0)
                        wall_str_pe = "🟢 Reinforced" if oi_chg_pe > 0 else ("🔴 Crumbling" if oi_chg_pe < 0 else "⚪ Neutral")
                        
                        # Regime Evaluation & Delta Calibration
                        regime_pe = "🟢 Trend Aligned" if spot_price > ema_20 else "🔴 Counter-Trend"
                        pass_delta_pe = bool(0.10 <= abs(delta_pe) <= 0.13) # Strict band for Bull Puts
                        
                        pe_candidate = {
                            "short_strike": short_strike_pe, "long_strike": long_strike_pe,
                            "net_prem": net_prem_pe, "spread_width": spread_width_pe,
                            "safety": round(((spot_price - short_strike_pe) / spot_price) * 100, 2),
                            "oi_chg": oi_chg_pe, "wall_oi": int(pe_wall['OI']),
                            "score": score_pe, "delta": delta_pe, "moat_atr": moat_atr_pe
                        }
                        
                        spreads.append({
                            "Symbol": sym, "Strategy": "Bull Put Spread", "Score": score_pe,
                            "Regime": regime_pe, "EMA_20": ema_20, "EMA_Dist_%": ema_dist_pct,
                            "Expiry_Date": expiry_str, "Expiry_Cycle": cycle, "DTE": int(dte),
                            "Setup": f"Sell {short_strike_pe} PE / Buy {long_strike_pe} PE",
                            "Spot_Price": spot_price,
                            "Risk_Reward": f"{rr_ratio_pe}:1",
                            "RR_Ratio": rr_ratio_pe, "Net_Premium": net_prem_pe,
                            "Short_Delta": delta_pe, "ATR_Moat": moat_atr_pe,
                            "Pass_Delta": pass_delta_pe, 
                            "Pass_Moat": pe_wall.get('Pass_Moat', False),
                            "Max_Profit_₹": round(net_prem_pe * lot_size, 2),
                            "Max_Risk_₹": round(max_risk_pe * lot_size, 2),
                            "Lot_Size": lot_size,
                            "Wall_Strength": wall_str_pe,
                            "Wall_OI": int(pe_wall['OI']),
                            "L2_Execution_Risk": "Awaiting L2 Check"
                        })

            # ----------------------------------------------------
            # IRON CONDOR 
            # ----------------------------------------------------
            if ce_candidate and pe_candidate:
                total_credit_ic = round(ce_candidate['net_prem'] + pe_candidate['net_prem'], 2)
                max_width_ic = max(ce_candidate['spread_width'], pe_candidate['spread_width'])
                max_risk_ic = round(max_width_ic - total_credit_ic, 2)
                
                if total_credit_ic > 0 and max_risk_ic > 0:
                    rr_ratio_ic = round(max_risk_ic / total_credit_ic, 2)
                    
                    score_ic = min(ce_candidate['score'], pe_candidate['score'])
                    worst_moat_ic = min(ce_candidate['moat_atr'], pe_candidate['moat_atr'])
                    max_abs_delta_ic = max(abs(ce_candidate['delta']), abs(pe_candidate['delta']))
                    
                    if ce_candidate['oi_chg'] > 0 and pe_candidate['oi_chg'] > 0:
                        wall_str_ic = "🟢 Dual Reinforced"
                    elif ce_candidate['oi_chg'] < 0 and pe_candidate['oi_chg'] < 0:
                        wall_str_ic = "🔴 Both Crumbling"
                    else:
                        wall_str_ic = "⚪ Mixed Strength"
                        
                    # Iron Condor Regime (Requires tight consolidation)
                    regime_ic = "🟢 Range Bound" if abs(ema_dist_pct) <= 1.5 else "🔴 Expanding"
                    
                    spreads.append({
                        "Symbol": sym, "Strategy": "Iron Condor", "Score": score_ic,
                        "Regime": regime_ic, "EMA_20": ema_20, "EMA_Dist_%": ema_dist_pct,
                        "Expiry_Date": expiry_str, "Expiry_Cycle": cycle, "DTE": int(dte),
                        "Setup": f"Sell {pe_candidate['short_strike']} PE & {ce_candidate['short_strike']} CE",
                        "Spot_Price": spot_price,
                        "Risk_Reward": f"{rr_ratio_ic}:1",
                        "RR_Ratio": rr_ratio_ic, "Net_Premium": total_credit_ic,
                        "Short_Delta": max_abs_delta_ic, "ATR_Moat": worst_moat_ic,
                        "Pass_Delta": bool(max_abs_delta_ic <= 0.20),
                        "Pass_Moat": bool(ce_candidate['moat_atr'] >= 1.5 and pe_candidate['moat_atr'] >= 1.5),
                        "Max_Profit_₹": round(total_credit_ic * lot_size, 2),
                        "Max_Risk_₹": round(max_risk_ic * lot_size, 2),
                        "Lot_Size": lot_size,
                        "Wall_Strength": wall_str_ic,
                        "Wall_OI": ce_candidate['wall_oi'] + pe_candidate['wall_oi'],
                        "L2_Execution_Risk": "Awaiting L2 Check"
                    })

        final_df = pd.DataFrame(spreads)
        if not final_df.empty:
            # Sort by Expiry Date first, then Score and Risk/Reward
            final_df = final_df.sort_values(
                by=["Expiry_Date", "Score", "RR_Ratio"], 
                ascending=[True, False, True]
            ).reset_index(drop=True)
            
        return final_df
