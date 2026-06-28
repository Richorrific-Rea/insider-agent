"""
Price spike detection via Yahoo Finance.

For each ticker we fetch:
  - Current price and % change vs previous close
  - % change vs today's open (intraday velocity)
  - Current volume vs 10-day average (volume confirmation)

A spike is only meaningful when volume confirms it.
Free, no API key needed. ~15 min delay (sufficient for this use case).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

YAHOO_QUOTE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
MIN_DELAY = 0.3
_last_req: float = 0.0


def _get(url: str, **kw) -> requests.Response:
    global _last_req
    elapsed = time.monotonic() - _last_req
    if elapsed < MIN_DELAY:
        time.sleep(MIN_DELAY - elapsed)
    r = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; insider-agent/1.0)"},
        timeout=15,
        **kw,
    )
    _last_req = time.monotonic()
    r.raise_for_status()
    return r


@dataclass
class PriceSnapshot:
    ticker: str
    current_price: float
    prev_close: float
    open_price: float
    day_high: float
    day_low: float
    volume: int
    avg_volume_10d: int

    pct_change_vs_close: float   # % change from previous close
    pct_change_vs_open: float    # % change from today's open (intraday velocity)
    volume_ratio: float          # current volume / 10-day average

    @property
    def is_spiking(self) -> bool:
        """True if price AND volume both confirm an unusual move (main flow)."""
        return (
            self.pct_change_vs_close >= 5.0
            and self.volume_ratio >= 1.5
        )

    def is_moving(self, threshold_pct: float = 7.0) -> bool:
        """Watchlist check — only needs % change, no volume requirement."""
        return self.pct_change_vs_close >= threshold_pct

    @property
    def spike_strength(self) -> str:
        """NOTABLE / FUERTE / EXTREMO based on magnitude."""
        pct = self.pct_change_vs_close
        if pct >= 18: return "EXTREMO"
        if pct >= 12: return "FUERTE"
        if pct >= 7:  return "NOTABLE"
        return "NORMAL"

    @property
    def summary(self) -> str:
        return (
            f"+{self.pct_change_vs_close:.1f}% vs cierre | "
            f"Vol: {self.volume_ratio:.1f}x promedio | "
            f"${self.current_price:.2f}"
        )


def fetch_price(ticker: str) -> Optional[PriceSnapshot]:
    """Fetch current price snapshot for a single ticker. Returns None on failure."""
    url = YAHOO_QUOTE_URL.format(ticker=ticker.upper())
    try:
        resp = _get(url, params={"interval": "1d", "range": "5d"})
        data = resp.json()
    except Exception as exc:
        logger.debug("Price fetch failed for %s: %s", ticker, exc)
        return None

    try:
        result = data["chart"]["result"][0]
        meta   = result["meta"]

        current  = float(meta.get("regularMarketPrice", 0) or 0)
        prev_cls = float(meta.get("previousClose", 0) or meta.get("chartPreviousClose", 0) or 0)
        open_p   = float(meta.get("regularMarketOpen", 0) or 0)
        high     = float(meta.get("regularMarketDayHigh", 0) or 0)
        low      = float(meta.get("regularMarketDayLow", 0) or 0)
        volume   = int(meta.get("regularMarketVolume", 0) or 0)
        avg_vol  = int(meta.get("averageDailyVolume10Day", 0) or 0)

        if current <= 0 or prev_cls <= 0:
            return None

        pct_close = ((current - prev_cls) / prev_cls) * 100
        pct_open  = ((current - open_p)   / open_p)   * 100 if open_p > 0 else 0.0
        vol_ratio = (volume / avg_vol) if avg_vol > 0 else 1.0

        return PriceSnapshot(
            ticker=ticker.upper(),
            current_price=current,
            prev_close=prev_cls,
            open_price=open_p,
            day_high=high,
            day_low=low,
            volume=volume,
            avg_volume_10d=avg_vol,
            pct_change_vs_close=round(pct_close, 2),
            pct_change_vs_open=round(pct_open, 2),
            volume_ratio=round(vol_ratio, 2),
        )

    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.debug("Price parse error for %s: %s", ticker, exc)
        return None


def fetch_prices(tickers: List[str]) -> Dict[str, Optional[PriceSnapshot]]:
    """Fetch price snapshots for multiple tickers."""
    return {t: fetch_price(t) for t in tickers}
