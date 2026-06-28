"""
Unusual options activity detector via Yahoo Finance options chain.

For each ticker we:
  1. Fetch the options chain (calls + puts)
  2. Flag contracts where volume / open_interest >= threshold
     AND volume is meaningfully above the 20-day average (approximated by OI)
  3. Return the top unusual contracts sorted by volume/OI ratio

Yahoo Finance options API is undocumented but stable.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

YAHOO_OPTIONS_URL = "https://query1.finance.yahoo.com/v7/finance/options/{ticker}"
MIN_DELAY = 0.3
_last_req: float = 0.0

# Minimum thresholds to flag as "unusual"
MIN_VOLUME = 500           # at least 500 contracts
MIN_VOL_OI_RATIO = 3.0    # volume at least 3x open interest


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
class UnusualOption:
    ticker: str
    option_type: str        # "CALL" or "PUT"
    strike: float
    expiration: str         # YYYY-MM-DD
    volume: int
    open_interest: int
    volume_oi_ratio: float
    premium: float          # last price × 100 (per contract)
    implied_volatility: float
    timestamp: str          # ISO datetime of last trade


def fetch_unusual_options(
    ticker: str,
    min_vol_oi_ratio: float = MIN_VOL_OI_RATIO,
    min_volume: int = MIN_VOLUME,
    calls_only: bool = True,   # calls more actionable for bullish signals
) -> List[UnusualOption]:
    """
    Return unusual options contracts for a ticker.
    Focuses on calls by default (bullish signal alignment).
    """
    url = YAHOO_OPTIONS_URL.format(ticker=ticker.upper())
    try:
        resp = _get(url)
        data = resp.json()
    except Exception as exc:
        logger.debug("Options fetch failed for %s: %s", ticker, exc)
        return []

    unusual: List[UnusualOption] = []

    try:
        result = data["optionChain"]["result"][0]
        option_blocks = result.get("options", [])
    except (KeyError, IndexError):
        return []

    contract_lists = []
    for block in option_blocks:
        if not calls_only or True:   # always fetch calls
            contract_lists.extend(block.get("calls", []))
        if not calls_only:
            contract_lists.extend(block.get("puts", []))

    for contract in contract_lists:
        volume = int(contract.get("volume", 0) or 0)
        oi = int(contract.get("openInterest", 0) or 0)
        if volume < min_volume or oi == 0:
            continue
        ratio = volume / oi
        if ratio < min_vol_oi_ratio:
            continue

        strike = float(contract.get("strike", 0) or 0)
        last_price = float(contract.get("lastPrice", 0) or 0)
        iv = float(contract.get("impliedVolatility", 0) or 0) * 100
        exp_ts = contract.get("expiration", 0)
        try:
            exp_str = datetime.utcfromtimestamp(exp_ts).strftime("%Y-%m-%d")
        except Exception:
            exp_str = ""
        last_trade_ts = contract.get("lastTradeDate", {})
        if isinstance(last_trade_ts, dict):
            ts_raw = last_trade_ts.get("raw", 0)
        else:
            ts_raw = last_trade_ts or 0
        try:
            ts_str = datetime.utcfromtimestamp(ts_raw).strftime("%Y-%m-%dT%H:%M:%S")
        except Exception:
            ts_str = ""

        option_type = "CALL" if "C" in contract.get("contractSymbol", "C") else "PUT"

        unusual.append(UnusualOption(
            ticker=ticker.upper(),
            option_type=option_type,
            strike=strike,
            expiration=exp_str,
            volume=volume,
            open_interest=oi,
            volume_oi_ratio=round(ratio, 1),
            premium=round(last_price * 100, 2),
            implied_volatility=round(iv, 1),
            timestamp=ts_str,
        ))

    unusual.sort(key=lambda o: o.volume_oi_ratio, reverse=True)
    return unusual[:5]   # top 5


def fetch_unusual_options_batch(
    tickers: List[str],
    **kw,
) -> Dict[str, List[UnusualOption]]:
    results: Dict[str, List[UnusualOption]] = {}
    for ticker in tickers:
        results[ticker] = fetch_unusual_options(ticker, **kw)
    return results
