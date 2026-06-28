"""
Local / cron entrypoint.

Usage:
  python main.py --once                      # run one cycle (live)
  python main.py --once --dry-run            # print instead of sending to Telegram

  # Portfolio management
  python main.py --add AAPL 50 185.00        # add position (ticker shares buy_price)
  python main.py --add AAPL 50 185.00 --note "MUY ALTA score=106"
  python main.py --remove AAPL               # remove position
  python main.py --portfolio                 # list current positions

  # Watchlist (price alerts, no signal correlation needed)
  python main.py --watch NVDA                # add ticker to watchlist
  python main.py --unwatch NVDA              # remove from watchlist
  python main.py --watchlist                 # show watchlist
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from config import load_config
from pipeline import run_once
from portfolio import PortfolioStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _cmd_portfolio(args) -> int:
    try:
        cfg = load_config()
    except ValueError as exc:
        logger.error("Config error: %s", exc)
        return 1

    store = PortfolioStore(path=cfg.state_file_path)

    if args.add:
        ticker, shares, price = args.add
        try:
            shares = float(shares)
            price  = float(price)
        except ValueError:
            print(f"Error: shares y price deben ser números. Ejemplo: --add AAPL 50 185.00")
            return 1
        note = args.note or ""
        pos = store.add_position(ticker, shares, price, notes=note)
        print(f"✓ Posición agregada: {pos.label}")
        return 0

    if args.remove:
        ticker = args.remove
        if store.remove_position(ticker):
            print(f"✓ Posición eliminada: {ticker.upper()}")
        else:
            print(f"No se encontró posición para {ticker.upper()}")
        return 0

    if args.portfolio:
        positions = store.get_positions()
        if not positions:
            print("Portafolio vacío. Usa --add TICKER SHARES PRICE para agregar una posición.")
        else:
            print(f"\n{'─'*55}")
            print(f"  PORTAFOLIO ({len(positions)} posición{'es' if len(positions) != 1 else ''})")
            print(f"{'─'*55}")
            for p in positions:
                print(f"  {p.label}")
                print(f"    Costo total: ${p.cost_basis:,.2f}")
            print(f"{'─'*55}\n")
        return 0

    if args.watch:
        ticker = args.watch.upper()
        if store.watch(ticker):
            print(f"✓ {ticker} agregado a watchlist. Alertarás cuando suba ≥7% en un día.")
        else:
            print(f"{ticker} ya estaba en la watchlist.")
        return 0

    if args.unwatch:
        ticker = args.unwatch.upper()
        if store.unwatch(ticker):
            print(f"✓ {ticker} removido de la watchlist.")
        else:
            print(f"{ticker} no estaba en la watchlist.")
        return 0

    if args.watchlist:
        wl = store.get_watchlist()
        if not wl:
            print("Watchlist vacía. Usa --watch TICKER para agregar acciones.")
        else:
            print(f"\n{'─'*40}")
            print(f"  WATCHLIST ({len(wl)} ticker{'s' if len(wl) != 1 else ''})")
            print(f"  Alerta cuando suba ≥{cfg_for_display()}% en un día")
            print(f"{'─'*40}")
            for t in wl:
                print(f"  {t}")
            print(f"{'─'*40}\n")
        return 0

    return None   # no portfolio command matched


def cfg_for_display() -> str:
    """Returns watchlist threshold for display — loads config lazily."""
    try:
        from config import load_config
        return str(load_config().watchlist_spike_pct)
    except Exception:
        return "7.0"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="insider-agent — SEC EDGAR insider purchase signal pipeline."
    )

    # ── Pipeline ──────────────────────────────────────────────────────────
    parser.add_argument("--once", action="store_true",
                        help="Run one poll cycle and exit.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print signals to stdout instead of sending to Telegram.")

    # ── Portfolio management ───────────────────────────────────────────────
    parser.add_argument("--add", nargs=3, metavar=("TICKER", "SHARES", "PRICE"),
                        help="Add a portfolio position. Example: --add AAPL 50 185.00")
    parser.add_argument("--remove", metavar="TICKER",
                        help="Remove a portfolio position.")
    parser.add_argument("--portfolio", action="store_true",
                        help="List current portfolio positions.")
    parser.add_argument("--note", metavar="TEXT",
                        help="Optional note when adding a position (e.g. signal context).")

    # ── Watchlist ──────────────────────────────────────────────────────────
    parser.add_argument("--watch",    metavar="TICKER",
                        help="Add a ticker to the price watchlist.")
    parser.add_argument("--unwatch",  metavar="TICKER",
                        help="Remove a ticker from the price watchlist.")
    parser.add_argument("--watchlist", action="store_true",
                        help="List all tickers in the price watchlist.")

    args = parser.parse_args()

    # Handle portfolio commands first
    result = _cmd_portfolio(args)
    if result is not None:
        return result

    if not args.once:
        parser.print_help()
        print("\nUsa --once para correr el pipeline, o --portfolio / --add / --remove para el portafolio.")
        return 1

    try:
        cfg = load_config()
    except ValueError as exc:
        logger.error("Configuration error: %s", exc)
        return 1

    notified = run_once(cfg, dry_run=args.dry_run)
    logger.info("Done. %d signal(s) processed.", notified)
    return 0


if __name__ == "__main__":
    sys.exit(main())
