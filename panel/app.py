from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import math

import numpy as np
import pandas as pd
import yfinance as yf
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Hisse Analiz Paneli")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)
HTML = Path(__file__).with_name("index.html")


@app.get("/")
def home():
    return FileResponse(HTML)


def clean_num(value):
    try:
        x = float(value)
        return round(x, 2) if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def indicators(df):
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Close"])
    if len(df) < 30:
        raise ValueError("Yeterli fiyat geçmişi alınamadı.")
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    volume = df["Volume"].astype(float) if "Volume" in df else pd.Series(0, index=df.index)

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    vol_avg = volume.rolling(20).mean()
    tr = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()

    last = close.iloc[-1]
    prev = close.iloc[-2]
    last_rsi = clean_num(rsi.iloc[-1])
    last_macd = clean_num(macd.iloc[-1])
    last_signal = clean_num(signal.iloc[-1])
    last_sma20 = clean_num(sma20.iloc[-1])
    last_sma50 = clean_num(sma50.iloc[-1])
    last_atr = clean_num(atr.iloc[-1]) or last * 0.02
    last_volume = clean_num(volume.iloc[-1])
    avg_volume = clean_num(vol_avg.iloc[-1])
    pct = ((last / prev) - 1) * 100 if prev else 0

    bullish = sum([
        last_rsi is not None and 45 <= last_rsi <= 68,
        last_macd is not None and last_signal is not None and last_macd > last_signal,
        last_sma20 is not None and last > last_sma20,
        last_sma50 is not None and last > last_sma50,
        avg_volume is not None and last_volume is not None and avg_volume > 0 and last_volume > avg_volume,
    ])
    bearish = sum([
        last_rsi is not None and last_rsi < 45,
        last_macd is not None and last_signal is not None and last_macd < last_signal,
        last_sma20 is not None and last < last_sma20,
        last_sma50 is not None and last < last_sma50,
        avg_volume is not None and last_volume is not None and avg_volume > 0 and last_volume > avg_volume and pct < 0,
    ])
    supports = [x for x in [clean_num(low.tail(20).min()), last_sma20] if x and x < last]
    resistances = [x for x in [clean_num(high.tail(20).max()), last_sma20] if x and x > last]
    support = max(supports) if supports else last - 1.5 * last_atr
    resistance = min(resistances) if resistances else last + 1.5 * last_atr
    return {
        "price": clean_num(last), "change_pct": clean_num(pct), "rsi": last_rsi,
        "macd": last_macd, "macd_signal": last_signal, "sma20": last_sma20,
        "sma50": last_sma50, "volume": last_volume, "avg_volume20": avg_volume,
        "support": clean_num(support), "resistance": clean_num(resistance),
        "atr": clean_num(last_atr), "bullish_score": int(bullish), "bearish_score": int(bearish),
        "timestamp": str(df.index[-1]),
    }


@app.get("/api/analyze")
def analyze(symbol: str = Query(..., min_length=1, max_length=15), side: str = Query("al")):
    symbol = symbol.strip().upper().replace(".IS", "")
    if not symbol.replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Geçerli bir BIST hisse kodu girin.")
    if side not in ("al", "sat"):
        raise HTTPException(status_code=400, detail="İşlem yönü al veya sat olmalı.")
    ticker = symbol + ".IS"
    used = None
    data = None
    errors = []
    for interval, period in [("5m", "5d"), ("1d", "1y")]:
        try:
            candidate = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True, timeout=12)
            if candidate is not None and len(candidate) >= 30:
                data, used = candidate, interval
                break
            errors.append(f"{interval}: yetersiz veri")
        except Exception as exc:
            errors.append(f"{interval}: {type(exc).__name__}")
    if data is None:
        raise HTTPException(status_code=502, detail="Veri sağlayıcısından bu hisse için yeterli veri alınamadı. Biraz sonra tekrar deneyin. " + "; ".join(errors))
    try:
        result = indicators(data)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    price = result["price"]
    atr = result["atr"] or price * 0.02
    if side == "al":
        result["verdict"] = "ALIM İÇİN UYGUN" if result["bullish_score"] >= 3 and (result["rsi"] or 0) < 70 else ("BEKLE" if result["bullish_score"] == result["bearish_score"] else "ALIMDA TEMKİNLİ")
        result["entry"] = price
        result["stop"] = clean_num(price - 1.5 * atr)
        result["target1"] = clean_num(price + 1.5 * atr)
        result["target2"] = clean_num(price + 2.5 * atr)
        result["comment"] = (
            "Göstergeler alım yönünden olumlu; yine de destek/direnç ve risk seviyesi kontrol edilmeli."
            if result["bullish_score"] >= 3 else
            "Alım için yeterli sayıda olumlu sinyal yok. Fiyatın destek ve trend teyidi vermesi beklenebilir."
        )
    else:
        result["verdict"] = "SATIŞ / AZALTMA DEĞERLENDİR" if result["bearish_score"] >= 3 else ("TUT / BEKLE" if result["bullish_score"] > result["bearish_score"] else "SATIŞ SİNYALİ ZAYIF")
        result["entry"] = None
        result["stop"] = None
        result["target1"] = clean_num(max(0.01, price - 1.5 * atr))
        result["target2"] = clean_num(max(0.01, price - 2.5 * atr))
        result["comment"] = (
            "Elde bulunan hisse için satış veya pozisyon azaltma sinyalleri güçlenmiş. Karar öncesi maliyetin ve işlem planının dikkate alınması gerekir."
            if result["bearish_score"] >= 3 else
            "Belirgin satış üstünlüğü oluşmamış. Bu sonuç açığa satış önerisi değildir; eldeki pozisyonun yönetimi içindir."
        )
    result.update({
        "symbol": symbol, "side": side, "interval": used,
        "data_mode": "GECİKMELİ / GÜN İÇİ" if used == "5m" else "GÜNLÜK SON VERİ",
        "source": "Yahoo Finance (yfinance)",
        "server_time": datetime.now(ZoneInfo("Europe/Istanbul")).isoformat(),
        "disclaimer": "Bu gösterge temelli otomatik değerlendirmedir; kesin getiri veya işlem sonucu garantisi değildir."
    })
    return result


@app.get("/health")
def health():
    return {"ok": True, "service": "hisse-analiz-paneli"}
