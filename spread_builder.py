
import pandas as pd

class SpreadBuilderEngine:
    @staticmethod
    def build_spreads(df):
        if df.empty: return pd.DataFrame()
        spreads = []
        for symbol, group in df.groupby('Symbol'):
            spot = group['Spot_Price'].iloc[0]
            ema = group['EMA_20'].iloc[0] if 'EMA_20' in group.columns else spot
            is_aligned = group['Regime_Aligned'].iloc[0] if 'Regime_Aligned' in group.columns else True
            regime = "🟢 Trend Aligned" if is_aligned else "🔴 Counter-Trend"
            
            calls = group[group['Option_Type'] == 'CE'].sort_values('Strike')
            if not calls.empty:
                valid_shorts = calls[calls['Pass_Moat'] & calls['Pass_Delta']]
                if not valid_shorts.empty:
                    short_call = valid_shorts.iloc[0]
                    long_calls = calls[calls['Strike'] > short_call['Strike']]
                    if not long_calls.empty:
                        long_call = long_calls.iloc[0]
                        net_premium = short_call['LTP'] - long_call['LTP']
                        if net_premium > 0:
                            spreads.append({
                                'Symbol': symbol, 'Regime': regime, 'Score': short_call['Composite_Score'],
                                'Strategy': 'Bear Call Spread', 'Setup': f"SELL {short_call['Strike']} CE / BUY {long_call['Strike']} CE",
                                'Spot_Price': spot, 'EMA_20': ema, 'Short_Delta': short_call['Delta'],
                                'ATR_Moat': short_call['Moat_ATR'], 'Net_Premium': round(net_premium, 2),
                                'Expiry_Cycle': short_call.get('Expiry_Cycle', 'Near')
                            })

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
                            spreads.append({
                                'Symbol': symbol, 'Regime': regime, 'Score': short_put['Composite_Score'],
                                'Strategy': 'Bull Put Spread', 'Setup': f"SELL {short_put['Strike']} PE / BUY {long_put['Strike']} PE",
                                'Spot_Price': spot, 'EMA_20': ema, 'Short_Delta': short_put['Delta'],
                                'ATR_Moat': short_put['Moat_ATR'], 'Net_Premium': round(net_premium, 2),
                                'Expiry_Cycle': short_put.get('Expiry_Cycle', 'Near')
                            })

        df_out = pd.DataFrame(spreads)
        if not df_out.empty:
            df_out.sort_values(by=['Score', 'Net_Premium'], ascending=[False, False], inplace=True)
        return df_out
