import pandas as pd
import numpy as np

class SpreadBuilderEngine:
    @staticmethod
    def build_spreads(df):
        spreads = []
        
        # Loop through each symbol to construct spreads
        for symbol, group in df.groupby('Symbol'):
            # Fetch context variables
            spot = group['Spot_Price'].iloc[0]
            ema = group['EMA_20'].iloc[0] if 'EMA_20' in group.columns else spot
            ema_dist = group['EMA_DIST_PCT'].iloc[0] if 'EMA_DIST_PCT' in group.columns else 0
            regime = "🟢 Trend Aligned" if group['Regime_Aligned'].iloc[0] else "🔴 Counter-Trend"
            
            # Bear Call Spreads
            calls = group[group['Option_Type'] == 'CE'].sort_values('Strike')
            if not calls.empty:
                # Find short leg (nearest strike that passed filters)
                valid_shorts = calls[calls['Pass_Moat'] & calls['Pass_Delta']]
                if not valid_shorts.empty:
                    short_call = valid_shorts.iloc[0]
                    # Find long leg (next strike up for protection)
                    long_calls = calls[calls['Strike'] > short_call['Strike']]
                    if not long_calls.empty:
                        long_call = long_calls.iloc[0]
                        
                        net_premium = short_call['LTP'] - long_call['LTP']
                        if net_premium > 0: # Ensure credit
                            risk = (long_call['Strike'] - short_call['Strike']) - net_premium
                            rr_ratio = f"1:{round(risk / net_premium, 2)}" if risk > 0 and net_premium > 0 else "N/A"
                            
                            spreads.append({
                                'Symbol': symbol,
                                'Regime': regime,
                                'Score': short_call['Composite_Score'],
                                'Strategy': 'Bear Call Spread',
                                'Setup': f"SELL {short_call['Strike']} CE / BUY {long_call['Strike']} CE",
                                'Spot_Price': spot,
                                'EMA_20': ema,
                                'EMA_Dist_%': ema_dist,
                                'Expiry_Date': short_call['Expiry_Date'].strftime("%Y-%m-%d"),
                                'DTE': int(short_call.get('Days_To_Event', 30)),
                                'Short_Delta': short_call['Delta'],
                                'ATR_Moat': short_call['Moat_ATR'],
                                'Risk_Reward': rr_ratio,
                                'Net_Premium': round(net_premium, 2),
                                'Max_Profit_₹': net_premium, # Needs Lot Size for absolute currency
                                'Max_Risk_₹': risk,
                                'Wall_Strength': "🟢 Reinforced" if short_call['Outside_Inst_Wall'] else "⚪ Neutral",
                                'Wall_OI': short_call['OI'],
                                'L2_Execution_Risk': "Low",
                                'Expiry_Cycle': short_call.get('Expiry_Cycle', 'Near')
                            })

            # Bull Put Spreads
            puts = group[group['Option_Type'] == 'PE'].sort_values('Strike', ascending=False)
            if not puts.empty:
                valid_shorts = puts[puts['Pass_Moat'] & puts['Pass_Delta']]
                if not valid_shorts.empty:
                    short_put = valid_shorts.iloc[0]
                    long_puts = puts[puts['Strike'] < short_put['Strike']]
                    if not long_puts.empty:
                        long_put = long_puts.iloc[0]
                        
                        net_premium = short_put['LTP'] - long_put['LTP']
                        if net_premium > 0:
                            risk = (short_put['Strike'] - long_put['Strike']) - net_premium
                            rr_ratio = f"1:{round(risk / net_premium, 2)}" if risk > 0 and net_premium > 0 else "N/A"
                            
                            spreads.append({
                                'Symbol': symbol,
                                'Regime': regime,
                                'Score': short_put['Composite_Score'],
                                'Strategy': 'Bull Put Spread',
                                'Setup': f"SELL {short_put['Strike']} PE / BUY {long_put['Strike']} PE",
                                'Spot_Price': spot,
                                'EMA_20': ema,
                                'EMA_Dist_%': ema_dist,
                                'Expiry_Date': short_put['Expiry_Date'].strftime("%Y-%m-%d"),
                                'DTE': int(short_put.get('Days_To_Event', 30)),
                                'Short_Delta': short_put['Delta'],
                                'ATR_Moat': short_put['Moat_ATR'],
                                'Risk_Reward': rr_ratio,
                                'Net_Premium': round(net_premium, 2),
                                'Max_Profit_₹': net_premium,
                                'Max_Risk_₹': risk,
                                'Wall_Strength': "🟢 Reinforced" if short_put['Outside_Inst_Wall'] else "⚪ Neutral",
                                'Wall_OI': short_put['OI'],
                                'L2_Execution_Risk': "Low",
                                'Expiry_Cycle': short_put.get('Expiry_Cycle', 'Near')
                            })

        # Compile into DataFrame
        result_df = pd.DataFrame(spreads)
        if not result_df.empty:
            result_df = result_df.sort_values(by=['Score', 'Net_Premium'], ascending=[False, False])
            
        return result_df
