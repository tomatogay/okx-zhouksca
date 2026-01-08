import ccxt
import pandas as pd
import numpy as np
import time
import os
import requests

# === 配置区 ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_msg(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"发送失败: {e}")

def get_okx_symbols(exchange):
    try:
        markets = exchange.fetch_tickers()
        df_tickers = pd.DataFrame.from_dict(markets, orient='index')
        df_tickers = df_tickers[df_tickers['symbol'].str.endswith('/USDT')]
        # 筛选交易额前150的币种，确保逻辑在主流币上更准
        top_symbols = df_tickers.sort_values('quoteVolume', ascending=False).head(150).index.tolist()
        return top_symbols
    except Exception as e:
        print(f"获取币种失败: {e}")
        return []

def analyze_divergence_strategy(symbol, df):
    if df is None or len(df) < 120: return None

    # 原生 EMA 计算
    def get_ema(series, length):
        return series.ewm(span=length, adjust=False).mean()

    close = df['close']
    lows = df['low']
    
    # 指标计算
    ema12 = get_ema(close, 12)
    ema26 = get_ema(close, 26)
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = dif - dea
    
    ema55 = get_ema(close, 55)
    ma99 = close.rolling(window=99).mean()

    # --- 1. 识别能量簇及其对应的价格极值 ---
    red_clusters = [] # 存储: {"area": 面积, "min_price": 区域最低价}
    current_area = 0
    current_min_price = 999999999
    in_red = False
    
    # 扫描最近150根K线，确保覆盖两个大波段
    recent_hist = hist.tail(150)
    recent_lows = lows.tail(150)

    for i in range(len(recent_hist)):
        val = recent_hist.iloc[i]
        price = recent_lows.iloc[i]
        
        if val < 0:
            in_red = True
            current_area += abs(val)
            current_min_price = min(current_min_price, price)
        else:
            if in_red:
                red_clusters.append({"area": current_area, "min_price": current_min_price})
                current_area = 0
                current_min_price = 999999999
                in_red = False
    
    if in_red:
        red_clusters.append({"area": current_area, "min_price": current_min_price})

    # --- 2. 双重底背离逻辑判断 ---
    is_divergence = False
    ratio_str = ""
    if len(red_clusters) >= 2:
        prev_c = red_clusters[-2]
        curr_c = red_clusters[-1]
        
        # 判定标准：当前红簇面积小于上一个红簇的45% (能量大幅衰竭)
        # 且价格不高于前一个底部的10% (处于底部区间或创新低)
        if curr_c['area'] < (prev_c['area'] * 0.45) and curr_c['area'] > 0:
            if curr_c['min_price'] <= prev_c['min_price'] * 1.10:
                is_divergence = True
                ratio_str = f"{round((curr_c['area']/prev_c['area'])*100, 1)}%"

    # --- 3. 趋势确认：背离形成 + 站上EMA55和MA99 ---
    last_close = close.iloc[-1]
    if is_divergence and last_close > ema55.iloc[-1] and last_close > ma99.iloc[-1]:
        return {
            "price": last_close,
            "ema55": round(ema55.iloc[-1], 6),
            "ratio": ratio_str
        }
    return None

def main():
    exchange = ccxt.okx()
    symbols = get_okx_symbols(exchange)
    timeframes = {"周线": "1w", "日线": "1d"}
    
    final_report = "🚨 *OKX 双重底背离预警* 🚨\n"
    found_any = False

    for label, tf in timeframes.items():
        found_in_tf = []
        for s in symbols:
            try:
                ohlcv = exchange.fetch_ohlcv(s, timeframe=tf, limit=200)
                df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
                res = analyze_divergence_strategy(s, df)
                if res:
                    icon = "⭐" if label == "周线" else "🔹"
                    found_in_tf.append(f"{icon} `{s}`: 现价`{res['price']}` (收敛比 `{res['ratio']}`)")
                    found_any = True
                time.sleep(0.1)
            except: continue
        
        if found_in_tf:
            final_report += f"\n🔥 *{label}买入点确认：*\n" + "\n".join(found_in_tf) + "\n"

    if found_any:
        final_report += "\n⚠️ *策略依据：能量簇二段收敛 + 突破EMA55/MA99*"
        send_telegram_msg(final_report)
    else:
        print("未发现符合双重底背离的币种。")

if __name__ == "__main__":
    main()
