import pandas as pd
import requests

class SpreadBuilderEngine:
    """Pairs OTM option strikes into vertical credit spreads, computes R:R, and calculates real-world INR risk."""
    
    _lot_sizes = {}

    @classmethod
    def fetch_lot_sizes(cls):
        """Silently scrapes live lot sizes from Zerodha's open API instrument dump."""
        if cls._lot_sizes: 
            return cls._lot_sizes
        
        try:
            # Tap into Zerodha's open unauthenticated daily dump
            url = "https://api.kite.trade/instruments"
            df = pd.read_csv(url)
            
            # Filter strictly for the NSE F&O segment
            nfo_df = df[df['exchange'] == 'NFO'].copy()
            
            # Extract unique symbols and their lot sizes
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
        
        for sym, group in df.groupby("Symbol"):
            spot_price = group['Spot_Price'].iloc[0]
            lot_size = lot_dict.get(sym, 1) 
                
            # ----------------------------------------------------
            # BEAR CALL SPREAD (Hunting Ceilings)
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
                        safety_ce = round(((short_strike_ce - spot_price) / spot_price) * 100, 2)
                        oi_chg_ce = ce_wall.get('OI_Change', 0)
                        
                        spreads.append({
                            "Symbol": sym,
                            "Spot_Price": spot_price,
                            "Strategy": "Bear Call Spread",
                            "Setup": f"Sell {short_strike_ce} CE / Buy {long_strike_ce} CE",
                            "Risk_Reward": f"{rr_ratio_ce}:1",
                            "RR_Ratio": rr_ratio_ce,
                            "Safety_Buffer_%": safety_ce,
                            "Lot_Size": lot_size,
                            "Net_Premium": net_prem_ce,
                            "Max_Profit_₹": round(net_prem_ce * lot_size, 2),
                            "Max_Risk_₹": round(max_risk_ce * lot_size, 2),
                            "Wall_Strength": "🟢 Reinforced" if oi_chg_ce > 0 else ("🔴 Crumbling" if oi_chg_ce < 0 else "⚪ Neutral"),
                            "Wall_OI": int(ce_wall['OI']),
                        })

            # ----------------------------------------------------
            # BULL PUT SPREAD (Hunting Floors)
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
                        safety_pe = round(((spot_price - short_strike_pe) / spot_price) * 100, 2)
                        oi_chg_pe = pe_wall.get('OI_Change', 0)
                        
                        spreads.append({
                            "Symbol": sym,
                            "Spot_Price": spot_price,
                            "Strategy": "Bull Put Spread",
                            "Setup": f"Sell {short_strike_pe} PE / Buy {long_strike_pe} PE",
                            "Risk_Reward": f"{rr_ratio_pe}:1",
                            "RR_Ratio": rr_ratio_pe,
                            "Safety_Buffer_%": safety_pe,
                            "Lot_Size": lot_size,
                            "Net_Premium": net_prem_pe,
                            "Max_Profit_₹": round(net_prem_pe * lot_size, 2),
                            "Max_Risk_₹": round(max_risk_pe * lot_size, 2),
                            "Wall_Strength": "🟢 Reinforced" if oi_chg_pe > 0 else ("🔴 Crumbling" if oi_chg_pe < 0 else "⚪ Neutral"),
                            "Wall_OI": int(pe_wall['OI']),
                        })

        final_df = pd.DataFrame(spreads)
        if not final_df.empty:
            final_df = final_df.sort_values(by="RR_Ratio", ascending=True).reset_index(drop=True)
            
        return final_df
