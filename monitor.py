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
        # 扫描成交额前150的币种
        return df_tickers.sort_values('quoteVolume', ascending=False).head(150).index.tolist()
    except Exception as e:
        print(f"获取币种失败: {e}")
        return []

def analyze_strict_divergence(symbol, df):
    """
    更新逻辑：
    1. 仅限周线
    2. 取消 EMA/MA 均线限制 (纯动能背离判断)
    3. MACD能量簇面积底背离 (面积萎缩)
    4. DIF线波谷抬高 (DIF底背离)
    """
    if df is None or len(df) < 100: return None

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

    # --- 提取红色能量簇信息 ---
    clusters = []
    curr_area = 0
    curr_min_dif = 999999
    curr_min_price = 999999
    in_red = False
    
    # 回溯周线数据
    h_subset = hist.tail(150)
    d_subset = dif.tail(150)
    l_subset = lows.tail(150)

    for i in range(len(h_subset)):
        h_val = h_subset.iloc[i]
        d_val = d_subset.iloc[i]
        p_val = l_subset.iloc[i]
        
        if h_val < 0:
            in_red = True
            curr_area += abs(h_val)
            curr_min_dif = min(curr_min_dif, d_val)
            curr_min_price = min(curr_min_price, p_val)
        else:
            if in_red:
                clusters.append({
                    "area": curr_area, 
                    "min_dif": curr_min_dif, 
                    "min_price": curr_min_price
                })
                curr_area, curr_min_dif, curr_min_price = 0, 999999, 999999
                in_red = False
    
    if in_red:
        clusters.append({"area": curr_area, "min_dif": curr_min_dif, "min_price": curr_min_price})

    # --- 同时底背离判定 ---
    if len(clusters) < 2: return None
    
    prev, curr = clusters[-2], clusters[-1]
    
    # 1. 价格条件：当前波段价格低点未大幅反弹（处于低位区间或创新低）
    price_check = curr['min_price'] <= prev['min_price'] * 1.08
    
    # 2. MACD面积背离：当前能量簇面积显著小于前一个 (能量衰竭)
    area_div = curr['area'] < (prev['area'] * 0.5)
    
    # 3. DIF线背离：当前DIF最低点高于前一波 (趋势线抬高)
    dif_div = curr['min_dif'] > prev['min_dif']
    
    last_close = close.iloc[-1]

    if price_check and area_div and dif_div:
        return {
            "price": last_close,
            "area_ratio": f"{round((curr['area']/prev['area'])*100, 1)}%",
            "dif_val": round(curr['min_dif'], 6)
        }
    return None

def main():
    exchange = ccxt.okx()
    symbols = get_okx_symbols(exchange)
    
    label, tf = "周线", "1w"
    found_signals = []
    
    print(f"开始执行{label}纯背离扫描（已取消均线限制）...")
    
    for s in symbols:
        try:
            # 获取足够长的K线以计算指标
            ohlcv = exchange.fetch_ohlcv(s, timeframe=tf, limit=200)
            df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
            res = analyze_strict_divergence(s, df)
            if res:
                found_signals.append(f"⭐ `{s}`: 现价 `{res['price']}`\n   └ 面积萎缩 `{res['area_ratio']}` | DIF抬高(当前:{res['dif_val']}) ✅")
            time.sleep(0.1)
        except: continue

    if found_signals:
        report = "🚨 *OKX 周线双重底背离预警 (左侧版)* 🚨\n\n"
        report += "\n".join(found_signals)
        report += "\n\n⚠️ *逻辑：价格持平/新低 + MACD红簇面积萎缩 + DIF线底抬高*"
        send_telegram_msg(report)
    else:
        print("未发现匹配信号。")

if __name__ == "__main__":
    main()
