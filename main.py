"""
Local / cron entrypoint.

Usage:
  python main.py --serve                     # pipeline cada 15 min + escucha Telegram (recomendado)
  python main.py --once                      # un solo ciclo y sale
  python main.py --once --dry-run            # imprime en terminal, no envía a Telegram

  # Portfolio (también disponible desde Telegram en modo --serve)
  python main.py --add AAPL 50 185.00        # agregar posición
  python main.py --remove AAPL               # quitar posición
  python main.py --portfolio                 # ver portafolio
  python main.py --watch NVDA                # agregar a watchlist
  python main.py --unwatch NVDA              # quitar de watchlist
  python main.py --watchlist                 # ver watchlist
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import time

from config import load_config
from pipeline import run_once
from portfolio import PortfolioStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

PIPELINE_INTERVAL = 15 * 60   # 15 minutes in seconds


# ── Portfolio CLI commands ─────────────────────────────────────────────────────

def _cmd_portfolio(args, cfg=None) -> Optional[int]:
    if cfg is None:
        try:
            cfg = load_config()
        except ValueError as exc:
            logger.error("Config error: %s", exc)
            return 1

    store = PortfolioStore(path=cfg.state_file_path)

    if args.add:
        ticker, shares, price = args.add
        try:
            shares, price = float(shares), float(price)
        except ValueError:
            print("Error: shares y price deben ser números. Ejemplo: --add AAPL 50 185.00")
            return 1
        pos = store.add_position(ticker, shares, price, notes=args.note or "")
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
            print("Portafolio vacío. Usa --add TICKER SHARES PRICE o dile al bot que compraste algo.")
        else:
            print(f"\n{'─'*55}")
            print(f"  PORTAFOLIO ({len(positions)} posición{'es' if len(positions)!=1 else ''})")
            print(f"{'─'*55}")
            for p in positions:
                print(f"  {p.label}")
                print(f"    Costo total: ${p.cost_basis:,.2f}")
            print(f"{'─'*55}\n")
        return 0

    if args.watch:
        ticker = args.watch.upper()
        if store.watch(ticker):
            print(f"✓ {ticker} en watchlist. Alertas cuando suba ≥{cfg.watchlist_spike_pct:.0f}% en un día.")
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
            print("Watchlist vacía.")
        else:
            print(f"\n{'─'*40}")
            print(f"  WATCHLIST ({len(wl)} ticker{'s' if len(wl)!=1 else ''})")
            print(f"  Alerta cuando suba ≥{cfg.watchlist_spike_pct:.0f}% en un día")
            print(f"{'─'*40}")
            for t in wl:
                print(f"  {t}")
            print(f"{'─'*40}\n")
        return 0

    return None


# ── Serve mode: pipeline + listener ───────────────────────────────────────────

def _pipeline_loop(cfg) -> None:
    """Runs the signal pipeline every 15 minutes in a background thread."""
    logger.info("Pipeline loop started — runs every %d min.", PIPELINE_INTERVAL // 60)
    while True:
        try:
            notified = run_once(cfg)
            logger.info("Pipeline cycle done. %d signal(s).", notified)
        except Exception as exc:
            logger.error("Pipeline cycle error: %s", exc)
        time.sleep(PIPELINE_INTERVAL)


def _run_serve(cfg) -> int:
    """
    Starts both:
    - Pipeline loop (every 15 min) in a background thread
    - Telegram listener (polling every 3s) in a background thread
    Blocks until Ctrl+C.
    """
    from telegram_listener import TelegramListener

    # Start Telegram listener
    listener = TelegramListener(cfg)
    listener.start()

    # Start pipeline loop in background thread
    pipeline_thread = threading.Thread(
        target=_pipeline_loop, args=(cfg,), daemon=True, name="pipeline"
    )
    pipeline_thread.start()

    # Run first pipeline cycle immediately
    logger.info("Running first pipeline cycle now…")
    try:
        run_once(cfg)
    except Exception as exc:
        logger.error("Initial pipeline cycle error: %s", exc)

    logger.info(
        "insider-agent running. Listening to Telegram. "
        "Pipeline runs every 15 min. Press Ctrl+C to stop."
    )

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down…")
        listener.stop()

    return 0


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="insider-agent — signal pipeline + Telegram bot."
    )

    parser.add_argument("--serve",   action="store_true",
                        help="Run pipeline every 15 min AND listen to Telegram (recommended).")
    parser.add_argument("--once",    action="store_true",
                        help="Run one pipeline cycle and exit.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print signals to stdout, don't send to Telegram.")

    parser.add_argument("--add", nargs=3, metavar=("TICKER","SHARES","PRICE"))
    parser.add_argument("--remove",    metavar="TICKER")
    parser.add_argument("--portfolio", action="store_true")
    parser.add_argument("--note",      metavar="TEXT")
    parser.add_argument("--watch",     metavar="TICKER")
    parser.add_argument("--unwatch",   metavar="TICKER")
    parser.add_argument("--watchlist", action="store_true")

    args = parser.parse_args()

    try:
        cfg = load_config()
    except ValueError as exc:
        logger.error("Configuration error: %s", exc)
        return 1

    # Portfolio management commands
    result = _cmd_portfolio(args, cfg)
    if result is not None:
        return result

    # Pipeline modes
    if args.serve:
        return _run_serve(cfg)

    if args.once:
        notified = run_once(cfg, dry_run=args.dry_run)
        logger.info("Done. %d signal(s) processed.", notified)
        return 0

    parser.print_help()
    print("\nTip: usa --serve para el modo completo (pipeline + bot de Telegram).")
    return 1


# fix missing Optional import
from typing import Optional

if __name__ == "__main__":
    sys.exit(main())
