"""
Portfolio tracker — stores positions the user has entered based on agent signals.

Interface: PortfolioStore
  add_position(ticker, shares, buy_price, buy_date, notes)
  remove_position(ticker)
  get_positions() → List[Position]
  save()

Uses the same state.json file as StateStore (separate key "portfolio").
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import date
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Position:
    ticker: str
    shares: float
    buy_price: float
    buy_date: str           # ISO YYYY-MM-DD
    notes: str = ""         # optional memo (e.g. "IMVT MUY ALTA score=106")

    @property
    def cost_basis(self) -> float:
        return self.shares * self.buy_price

    @property
    def label(self) -> str:
        return (
            f"{self.ticker}: {self.shares:,.0f} acc @ ${self.buy_price:,.2f} "
            f"el {self.buy_date}"
            + (f" — {self.notes}" if self.notes else "")
        )


class PortfolioStore:
    def __init__(self, path: str = "state.json"):
        self._path = path
        self._positions: dict[str, Position] = {}
        self._watchlist: set = set()
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            for d in data.get("portfolio", []):
                p = Position(**d)
                self._positions[p.ticker.upper()] = p
            self._watchlist = {t.upper() for t in data.get("watchlist", [])}
        except Exception as exc:
            logger.warning("Could not load portfolio from %s: %s", self._path, exc)

    def get_positions(self) -> List[Position]:
        return list(self._positions.values())

    def get_position(self, ticker: str) -> Optional[Position]:
        return self._positions.get(ticker.upper())

    def add_position(
        self,
        ticker: str,
        shares: float,
        buy_price: float,
        buy_date: Optional[str] = None,
        notes: str = "",
    ) -> Position:
        p = Position(
            ticker=ticker.upper(),
            shares=shares,
            buy_price=buy_price,
            buy_date=buy_date or date.today().isoformat(),
            notes=notes,
        )
        self._positions[p.ticker] = p
        self.save()
        return p

    def remove_position(self, ticker: str) -> bool:
        removed = self._positions.pop(ticker.upper(), None)
        if removed:
            self.save()
        return removed is not None

    # ── Watchlist ──────────────────────────────────────────────────────────

    def get_watchlist(self) -> List[str]:
        return sorted(self._watchlist)

    def watch(self, ticker: str) -> bool:
        """Add ticker to watchlist. Returns True if it was new."""
        t = ticker.upper()
        if t in self._watchlist:
            return False
        self._watchlist.add(t)
        self.save()
        return True

    def unwatch(self, ticker: str) -> bool:
        """Remove ticker from watchlist. Returns True if it was present."""
        t = ticker.upper()
        if t not in self._watchlist:
            return False
        self._watchlist.discard(t)
        self.save()
        return True

    def save(self) -> None:
        # Merge with existing state.json without touching other keys
        data: dict = {}
        if os.path.exists(self._path):
            try:
                with open(self._path, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass
        data["portfolio"] = [asdict(p) for p in self._positions.values()]
        data["watchlist"] = sorted(self._watchlist)
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self._path)
        except OSError as exc:
            logger.error("Failed to save portfolio: %s", exc)
