import os
import asyncio
import logging
from datetime import datetime, timezone

import aiohttp
import discord
import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "1539566165846921226"))
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "").strip()

SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "90"))
MIN_SIGNAL_SCORE = int(os.getenv("MIN_SIGNAL_SCORE", "74"))
ALERT_COOLDOWN_MINUTES = int(os.getenv("ALERT_COOLDOWN_MINUTES", "12"))

DEFAULT_SYMBOLS = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "AUD/USD",
    "USD/CAD",
    "EUR/JPY",
]

SYMBOLS = [
    s.strip().upper()
    for s in os.getenv("SYMBOLS", ",".join(DEFAULT_SYMBOLS)).split(",")
    if s.strip()
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("signal-bot")


class MarketDataError(Exception):
    pass


class TwelveDataClient:
    BASE = "https://api.twelvedata.com/time_series"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def candles(self, session: aiohttp.ClientSession, symbol: str, interval="1min", outputsize=120):
        params = {
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "apikey": self.api_key,
            "format": "JSON",
            "timezone": "UTC",
        }
        async with session.get(self.BASE, params=params, timeout=20) as resp:
            data = await resp.json(content_type=None)

        if isinstance(data, dict) and data.get("status") == "error":
            raise MarketDataError(data.get("message", "Twelve Data error"))

        values = data.get("values") if isinstance(data, dict) else None
        if not values:
            raise MarketDataError(f"No candle data returned for {symbol}")

        df = pd.DataFrame(values)
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        return df.sort_values("datetime").dropna().reset_index(drop=True)


def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df, period=14):
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()


def bollinger(series, period=20, std_mult=2):
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    return mid, mid + std_mult * std, mid - std_mult * std


def analyze(df: pd.DataFrame):
    if len(df) < 60:
        return None

    d = df.copy()
    d["ema9"] = ema(d["close"], 9)
    d["ema21"] = ema(d["close"], 21)
    d["ema50"] = ema(d["close"], 50)
    d["rsi"] = rsi(d["close"], 14)
    macd = ema(d["close"], 12) - ema(d["close"], 26)
    signal = ema(macd, 9)
    d["macd_hist"] = macd - signal
    d["atr"] = atr(d)
    bb_mid, bb_upper, bb_lower = bollinger(d["close"])
    d["bb_mid"], d["bb_upper"], d["bb_lower"] = bb_mid, bb_upper, bb_lower
    d["resistance"] = d["high"].shift(1).rolling(20).max()
    d["support"] = d["low"].shift(1).rolling(20).min()

    x = d.iloc[-1]
    prev = d.iloc[-2]
    if any(pd.isna(x[k]) for k in ["ema9","ema21","ema50","rsi","macd_hist","atr","bb_mid","bb_upper","bb_lower"]):
        return None

    bull = bear = 0
    reasons_bull, reasons_bear = [], []

    if x.ema9 > x.ema21 > x.ema50:
        bull += 28; reasons_bull.append("EMA 9/21/50 bullish alignment")
    elif x.ema9 < x.ema21 < x.ema50:
        bear += 28; reasons_bear.append("EMA 9/21/50 bearish alignment")
    else:
        if x.ema9 > x.ema21: bull += 12
        if x.ema9 < x.ema21: bear += 12

    if 52 <= x.rsi <= 68:
        bull += 16; reasons_bull.append(f"RSI bullish ({x.rsi:.1f})")
    elif 32 <= x.rsi <= 48:
        bear += 16; reasons_bear.append(f"RSI bearish ({x.rsi:.1f})")
    elif x.rsi > 72:
        bear += 5; reasons_bear.append("RSI overbought caution")
    elif x.rsi < 28:
        bull += 5; reasons_bull.append("RSI oversold rebound potential")

    if x.macd_hist > 0 and x.macd_hist > prev.macd_hist:
        bull += 18; reasons_bull.append("MACD momentum accelerating up")
    elif x.macd_hist < 0 and x.macd_hist < prev.macd_hist:
        bear += 18; reasons_bear.append("MACD momentum accelerating down")

    body = abs(x.close - x.open)
    candle_range = max(x.high - x.low, 1e-12)
    body_ratio = body / candle_range
    if x.close > x.open and body_ratio >= 0.55:
        bull += 12; reasons_bull.append("strong bullish candle")
    elif x.close < x.open and body_ratio >= 0.55:
        bear += 12; reasons_bear.append("strong bearish candle")

    if not pd.isna(x.resistance) and x.close > x.resistance:
        bull += 16; reasons_bull.append("20-candle resistance breakout")
    if not pd.isna(x.support) and x.close < x.support:
        bear += 16; reasons_bear.append("20-candle support breakdown")

    if x.close > x.bb_mid and x.close < x.bb_upper:
        bull += 8
    elif x.close < x.bb_mid and x.close > x.bb_lower:
        bear += 8

    atr_pct = (x.atr / x.close) * 100
    quality = "Normal"
    if atr_pct < 0.015:
        bull -= 8; bear -= 8; quality = "Low"
    elif atr_pct > 0.30:
        bull -= 5; bear -= 5; quality = "High / noisy"

    direction = "CALL" if bull > bear else "PUT"
    raw, opposition = max(bull, bear), min(bull, bear)
    score = int(max(0, min(95, raw - 0.35 * opposition)))
    expiry = "3–5 min" if score >= 86 else "2–3 min" if score >= 78 else "1–2 min"
    reasons = reasons_bull if direction == "CALL" else reasons_bear
    return {
        "direction": direction,
        "score": score,
        "expiry": expiry,
        "price": float(x.close),
        "rsi": float(x.rsi),
        "volatility": quality,
        "reasons": reasons[:4],
    }


class SignalBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.market = TwelveDataClient(TWELVE_DATA_API_KEY)
        self.last_alert = {}

    async def setup_hook(self):
        self.scan_task = asyncio.create_task(self.scanner_loop())

    async def on_ready(self):
        log.info("Logged in as %s", self.user)

    async def get_target_channel(self):
        ch = self.get_channel(DISCORD_CHANNEL_ID)
        if ch is None:
            try:
                ch = await self.fetch_channel(DISCORD_CHANNEL_ID)
            except Exception as exc:
                log.error("Could not access channel %s: %s", DISCORD_CHANNEL_ID, exc)
                return None
        return ch

    def cooldown_ok(self, symbol, direction):
        key = f"{symbol}:{direction}"
        last = self.last_alert.get(key)
        if not last:
            return True
        age = (datetime.now(timezone.utc) - last).total_seconds() / 60
        return age >= ALERT_COOLDOWN_MINUTES

    async def send_signal(self, channel, symbol, result, rank):
        arrow = "🟢" if result["direction"] == "CALL" else "🔴"
        reasons = "\n".join(f"• {r}" for r in result["reasons"]) or "• indicator agreement"
        embed = discord.Embed(
            title=f"{arrow} {symbol} — {result['direction']}",
            description=(
                f"**Setup score:** {result['score']}/100\n"
                f"**Suggested expiry window:** {result['expiry']}\n"
                f"**Rank this scan:** #{rank}"
            ),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Price", value=f"{result['price']:.6f}", inline=True)
        embed.add_field(name="RSI", value=f"{result['rsi']:.1f}", inline=True)
        embed.add_field(name="Volatility", value=result["volatility"], inline=True)
        embed.add_field(name="Why it ranked", value=reasons, inline=False)
        embed.set_footer(text="Signal score ≠ win probability. Analysis only; does not place trades.")
        await channel.send(embed=embed)

    async def scanner_loop(self):
        await self.wait_until_ready()
        channel = await self.get_target_channel()
        if channel is None:
            return

        await channel.send(
            "✅ **Pocket Option signal scanner is online.**\n"
            f"Scanning: {', '.join(SYMBOLS)}\n"
            f"Alert threshold: {MIN_SIGNAL_SCORE}/100\n"
            "Real-market pairs only; no automatic trade execution."
        )

        async with aiohttp.ClientSession() as session:
            while not self.is_closed():
                ranked = []
                for symbol in SYMBOLS:
                    try:
                        df = await self.market.candles(session, symbol)
                        result = analyze(df)
                        if result:
                            ranked.append((symbol, result))
                    except Exception as exc:
                        log.warning("%s scan failed: %s", symbol, exc)
                    await asyncio.sleep(1.2)

                ranked.sort(key=lambda item: item[1]["score"], reverse=True)
                sent = 0
                for rank, (symbol, result) in enumerate(ranked, 1):
                    if result["score"] < MIN_SIGNAL_SCORE:
                        continue
                    if not self.cooldown_ok(symbol, result["direction"]):
                        continue
                    await self.send_signal(channel, symbol, result, rank)
                    self.last_alert[f"{symbol}:{result['direction']}"] = datetime.now(timezone.utc)
                    sent += 1
                    if sent >= 2:
                        break
                await asyncio.sleep(SCAN_INTERVAL_SECONDS)


def validate():
    missing = []
    if not DISCORD_TOKEN:
        missing.append("DISCORD_TOKEN")
    if not TWELVE_DATA_API_KEY:
        missing.append("TWELVE_DATA_API_KEY")
    if missing:
        raise SystemExit("Missing environment variables: " + ", ".join(missing))


if __name__ == "__main__":
    validate()
    bot = SignalBot()
    bot.run(DISCORD_TOKEN)
