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
        return df_tickers.sort_values('quoteVolume', ascending=False).head(100).index.tolist()
    except: return []

def analyze_triple_divergence(symbol, df):
    """
    逻辑：连续两次面积萎缩 + DIF连续两次抬高 (三波红簇对比)
    """
    if df is None or len(df) < 130: return None

    def get_ema(series, length):
        return series.ewm(span=length, adjust=False).mean()

    close = df['close']
    lows = df['low']
    ema12 = get_ema(close, 12)
    ema26 = get_ema(close, 26)
    dif = ema12 - ema26
    hist = dif - get_ema(dif, 9)

    # --- 提取所有红色能量簇信息 ---
    clusters = []
    curr_area = 0
    curr_min_dif = 999999
    in_red = False
    
    h_subset = hist.tail(180) # 增加回溯长度
    d_subset = dif.tail(180)

    for i in range(len(h_subset)):
        h_val = h_subset.iloc[i]
        d_val = d_subset.iloc[i]
        
        if h_val < 0:
            in_red = True
            curr_area += abs(h_val)
            curr_min_dif = min(curr_min_dif, d_val)
        else:
            if in_red:
                clusters.append({"area": curr_area, "min_dif": curr_min_dif})
                curr_area, curr_min_dif = 0, 999999
                in_red = False
    if in_red:
        clusters.append({"area": curr_area, "min_dif": curr_min_dif})

    # --- 判定连续两次萎缩和抬高 ---
    if len(clusters) < 3: return None
    
    c1 = clusters[-3] # 最早一波
    c2 = clusters[-2] # 中间一波
    c3 = clusters[-1] # 当前这波 (正在形成或刚结束)

    # 条件1：面积连续两次大幅度萎缩 (动能衰竭链)
    # 当前波面积 < 第二波的 60%，且第二波面积 < 第一波的 70%
    area_shrinking = (c3['area'] < c2['area'] * 0.6) and (c2['area'] < c1['area'] * 0.7)
    
    # 条件2：DIF 波谷连续两次抬高 (趋势反转链)
    dif_rising = (c3['min_dif'] > c2['min_dif']) and (c2['min_dif'] > c1['min_dif'])

    if area_shrinking and dif_rising:
        return {
            "price": close.iloc[-1],
            "desc": f"面积: {round(c1['area'],1)} > {round(c2['area'],1)} > {round(c3['area'],1)}",
            "dif_desc": f"DIF波谷稳步抬高 ✅"
        }
    return None

def main():
    exchange = ccxt.okx()
    symbols = get_okx_symbols(exchange)
    
    print(f"执行周线“三点连线”严苛扫描...")
    found_signals = []
    
    for s in symbols:
        try:
            ohlcv = exchange.fetch_ohlcv(s, timeframe="1w", limit=200)
            df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
            res = analyze_triple_divergence(s, df)
            if res:
                found_signals.append(f"🔥 `{s}`: 现价 `{res['price']}`\n   └ {res['desc']}\n   └ {res['dif_desc']}")
            time.sleep(0.1)
        except: continue

    if found_signals:
        report = "🚨 *OKX 周线级别：连续两次能量萎缩预警* 🚨\n\n"
        report += "\n".join(found_signals)
        report += "\n\n⚠️ *形态：三段式探底，动能连续大幅衰竭 + DIF线底位连续抬高*"
        send_telegram_msg(report)
    else:
        print("未发现连续萎缩信号。")

if __name__ == "__main__":
    main()
