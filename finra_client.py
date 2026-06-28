"""
Short interest data via Yahoo Finance quoteSummary.

We use Yahoo's undocumented but stable quoteSummary endpoint to get:
  - shortPercentOfFloat  (current)
  - sharesShort          (current)
  - sharesShortPriorMonth (prior period)

This lets us compute the month-over-month change without storing history.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

YAHOO_URL = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
MIN_DELAY = 0.3
_last_req: float = 0.0


def _get(url: str, **kw) -> requests.Response:
    global _last_req
    elapsed = time.monotonic() - _last_req
    if elapsed < MIN_DELAY:
        time.sleep(MIN_DELAY - elapsed)
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; insider-agent/1.0)",
        "Accept": "application/json",
    }
    r = requests.get(url, headers=headers, timeout=15, **kw)
    _last_req = time.monotonic()
    r.raise_for_status()
    return r


@dataclass
class ShortInterestData:
    ticker: str
    current_pct: float        # shortPercentOfFloat as percentage (e.g. 5.2 = 5.2%)
    prior_pct: float          # prior month
    decline_pct: float        # how much it fell (positive = shorts covering)
    shares_short: float
    shares_short_prior: float
    report_date: str          # date of the most recent data point
    days_to_cover: float = 0.0

    @property
    def is_declining(self) -> bool:
        return self.decline_pct >= 10.0


def fetch_short_interest(ticker: str) -> Optional[ShortInterestData]:
    """
    Fetch short interest for a single ticker via Yahoo Finance.
    Returns None on failure.
    """
    url = YAHOO_URL.format(ticker=ticker)
    try:
        resp = _get(url, params={"modules": "defaultKeyStatistics,summaryDetail"})
        data = resp.json()
    except Exception as exc:
        logger.debug("Short interest fetch failed for %s: %s", ticker, exc)
        return None

    try:
        stats = data["quoteSummary"]["result"][0]["defaultKeyStatistics"]
    except (KeyError, IndexError, TypeError):
        return None

    def _raw(key: str) -> float:
        val = stats.get(key, {})
        if isinstance(val, dict):
            return float(val.get("raw", 0) or 0)
        try:
            return float(val or 0)
        except (ValueError, TypeError):
            return 0.0

    current_pct = _raw("shortPercentOfFloat") * 100
    shares_short = _raw("sharesShort")
    shares_short_prior = _raw("sharesShortPriorMonth")
    days_to_cover = _raw("shortRatio")

    # Compute prior pct (approximate: prior_shares / float)
    float_shares = _raw("floatShares")
    prior_pct = (shares_short_prior / float_shares * 100) if float_shares > 0 else 0.0

    # Decline: positive means shorts are covering (bullish signal)
    if prior_pct > 0:
        decline_pct = ((prior_pct - current_pct) / prior_pct) * 100
    else:
        decline_pct = 0.0

    report_date = ""
    date_val = stats.get("sharesShortPreviousMonthDate", {})
    if isinstance(date_val, dict) and date_val.get("fmt"):
        report_date = date_val["fmt"]

    return ShortInterestData(
        ticker=ticker.upper(),
        current_pct=round(current_pct, 2),
        prior_pct=round(prior_pct, 2),
        decline_pct=round(decline_pct, 1),
        shares_short=shares_short,
        shares_short_prior=shares_short_prior,
        report_date=report_date,
        days_to_cover=round(days_to_cover, 1),
    )


def fetch_short_interest_batch(
    tickers: List[str],
) -> Dict[str, Optional[ShortInterestData]]:
    """Fetch short interest for multiple tickers."""
    results: Dict[str, Optional[ShortInterestData]] = {}
    for ticker in tickers:
        results[ticker] = fetch_short_interest(ticker)
    return results
