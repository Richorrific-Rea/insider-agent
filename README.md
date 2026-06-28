# insider-agent

A signal pipeline + Telegram bot that monitors **7 independent data sources** plus **financial news**, cross-references them into a confidence score, and fires alerts written by a character somewhere between Jordan Belfort and Gordon Gekko.

It watches what corporate insiders, politicians, activist investors, institutional funds, short sellers, and options traders are doing. **Any combination of signals with a high enough score fires an alert** — no Form 4 required. Financial news triggers a 5-day scan window so stocks in the news get automatically monitored for market signals.

You can also **talk to it on Telegram** in plain Spanish: tell it what you bought, ask for your portfolio, and it will alert you when exit signals appear.

> **Disclaimer:** This system generates **ideas to research**, not investment recommendations. Every alert includes a mandatory disclaimer. Do not use this software as the basis for financial decisions.

---

## What it monitors

| Source | Data | Where it comes from |
|---|---|---|
| **SEC Form 4** | Corporate insider purchases (CEOs, CFOs, Directors) | SEC EDGAR — free |
| **Congressional PTR** | Senate and House trading disclosures | Senate EFTS + House eFD — free |
| **13D / 13G** | Activist investors crossing ≥5% stake | SEC EDGAR — free |
| **13F** | Institutional funds opening new positions (>$100M AUM) | SEC EDGAR — free |
| **Short interest** | Short sellers covering / increasing positions | Yahoo Finance — free |
| **Unusual options** | Abnormal call/put volume vs open interest | Yahoo Finance — free |
| **Price spikes / drops** | Stocks moving significantly | Yahoo Finance — free |
| **Financial news** | Yahoo Finance, Reuters, MarketWatch, Seeking Alpha RSS | Free RSS feeds |

Everything is free. No paid data feeds required.

**Any signal combination with a score ≥ 40 (MEDIA tier) fires an alert** — even without a Form 4 anchor. Short interest declining + unusual calls alone can trigger. Financial news mentions add tickers to a 5-day scan window automatically.

---

## How scoring works

Every 15 minutes, the pipeline collects all active signals for each ticker and scores them. The key insight is **independence** — a corporate insider and a senator don't coordinate. When both buy the same stock in the same week, that's two completely separate actors reaching the same conclusion.

### Signal weights

| Signal | Points | Why it matters |
|---|---|---|
| CEO / CFO / President buying | 30 | Maximum inside knowledge |
| Director buying | 20 | Board-level knowledge |
| Other officer | 15 | Operational knowledge |
| Politician buying | 20 per person, max 50 | Possible committee-level information |
| **Activist 13D** | **40** | Deep due diligence + intent to act |
| Passive 13G (≥5%) | 15 | High conviction, no active agenda |
| Institutional 13F new position | 10 per fund, max 25 | Smart money accumulating |
| Short interest declining | 15 | Bears are covering |
| Unusual call options | 25 | Most forward-looking signal |
| Price confirming (spike + volume) | 20 | Thesis playing out in real time |

Multipliers apply for trade size ($100k–$5M+), recency (0–30 days), and activist stake size. Bonuses apply for insider clusters (+10/+20) and convergence across multiple independent source types (+10 to +55).

### Signal tiers

| Score | Tier | What it means |
|---|---|---|
| 0 – 25 | **BAJA** | Single weak signal |
| 26 – 55 | **MEDIA** | Solid signal worth investigating |
| 56 – 85 | **ALTA** | Multiple independent sources converging |
| 86 + | **MUY ALTA** | Strong convergence — priority research |

---

## The broker — Wolf of Wall Street meets Gordon Gekko

The LLM analysis scales with signal strength. At low tiers it's a calm analyst. At **MUY ALTA** it's Jordan Belfort at his peak — Stratton Oakmont in full swing, giving the speech of his life.

Every analysis includes:
1. **What the company does** — one sentence, no corporate jargon
2. **A theory** — why are insiders moving? M&A rumors, FDA readout, sector cycle, contract win
3. **The facts** — who bought, how much, when
4. **The personality** — calibrated to signal strength, with actual movie quotes when they fit

| Tier | Character | Energy |
|---|---|---|
| BAJA | Sober analyst, Gekko reference if it fits | Informative |
| MEDIA | Young Belfort, natural hunger | "Act as if", Stratton energy |
| ALTA | Belfort at his peak, morning speech | "¡Stratton Oakmont!", "Greed is good" |
| MUY ALTA | Belfort on the ferry | "¡No me voy!", "ALGO SABEN", "el 87 no terminó bien" |

Example at **MUY ALTA**:
> *"Immunovant hace anticuerpos para autoinmunes — BIOTECH EN EL CENTRO DEL SECTOR MÁS CALIENTE DEL MERCADO. Mi teoría: hay un readout de Phase 3 que los que están adentro ya leyeron. '¡No me voy, no me voy!' — cuando el CFO, un ejecutivo senior Y un director meten $443k EL MISMO DÍA, el nombre del juego es claro: mover el dinero. Act as if esto fuera la señal del año. Porque LO ES. Esto no es asesoramiento — esto es lo que VEO."*

Supports any LLM provider — **Groq and Gemini are free**:

| Provider | `LLM_PROVIDER=` | Free tier |
|---|---|---|
| **Groq** (recommended) | `groq` | ✅ Yes |
| **Google Gemini** | `gemini` | ✅ Yes |
| Anthropic | `anthropic` | No |
| OpenAI | `openai` | No |
| Ollama (local, offline) | `ollama` | ✅ Yes |
| Any OpenAI-compatible API | `custom` | Varies |

---

## Telegram bot — conversational interface

When running in `--serve` mode, the agent listens to your Telegram messages in real time. Just talk to it naturally in Spanish — it uses an LLM to understand what you mean, map company names to tickers, and ask for missing info.

```
You:  "oye compré 50 de Apple a 178"
Bot:  ✓ Listo. AAPL en tu portafolio.
      50 acc @ $178.00 = $8.9k
      Te aviso si algo cambia.

You:  "compré nvidia"
Bot:  ¿Cuántas acciones y a qué precio compraste NVDA?

You:  "100 a 890"
Bot:  ✓ Listo. NVDA en tu portafolio.
      100 acc @ $890.00 = $89k

You:  "portafolio"
Bot:  Portafolio (2 posiciones)
      • AAPL  50 acc @ $178.00
      • NVDA  100 acc @ $890.00

You:  "vendí apple"
Bot:  ✓ AAPL removido del portafolio y watchlist.

You:  "vigila Tesla"
Bot:  ✓ TSLA en la watchlist.
      Alertas cuando suba ≥7% en un día.
```

The LLM understands: company names in Spanish or English, abbreviations, nicknames ("la de los chips" → NVDA, "la de Elon" → TSLA). If something is ambiguous it asks, never guesses.

**Start the full agent (pipeline + bot):**
```bash
python main.py --serve
```

---

## Portfolio tracking & exit alerts

Once a position is registered (via Telegram chat or CLI), the agent monitors it every 15 minutes and fires an **exit alert** if the same signals start reversing.

```bash
# CLI alternative to chatting with the bot:
python main.py --add IMVT 500 5.62 --note "MUY ALTA score=106 — 3 insiders same day"
python main.py --portfolio
python main.py --remove IMVT
```

When you add a position it is **automatically added to the watchlist** too — no extra step needed.

Exit signals are the mirror of entry signals: insider selling, politicians selling, activists reducing stake, short interest rising, unusual PUT options. Exit alerts only fire at **ALTA (56+)** or **MUY ALTA (86+)** — insider selling is noisier than buying.

---

## Watchlist — price alerts without signal correlation

Add any ticker you want to monitor for sudden price moves, regardless of whether there's insider activity.

```bash
python main.py --watch NVDA
python main.py --watch AAPL
python main.py --watchlist     # see everything you're watching
python main.py --unwatch TSLA
```

Alert tiers (default threshold: 7%):

| Move | Tier | Typical cause |
|---|---|---|
| +7% – 12% | Notable | Earnings surprise, analyst upgrade |
| +12% – 18% | Fuerte | M&A rumors, FDA approval, major contract |
| +18%+ | Extremo | Takeover bid, binary event |

The main flow (portfolio + signal tickers) uses `is_spiking` which requires both price movement AND volume confirmation. Watchlist alerts use `is_moving` — price only, no volume gate.

---

## Installation

### Requirements
- Python 3.11 or newer
- Git
- A Telegram account (free) — to receive alerts
- An LLM API key (optional) — Groq and Gemini have free tiers

---

### macOS

**1 — Install Homebrew** (skip if you already have it)
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

**2 — Install Python and Git**
```bash
brew install python@3.11 git
```

**3 — Clone and set up**
```bash
git clone https://github.com/Richorrific-Rea/insider-agent.git
cd insider-agent
make setup
```

`make setup` creates a virtual environment, installs all dependencies, and launches the interactive wizard.

**4 — Test it**
```bash
python main.py --once --dry-run
```

Signals print in your terminal. If you see `No new qualifying signals` it means no filings passed the filters in this batch — normal. Try `MIN_TRADE_VALUE_USD=10000` in your `.env` to force output.

**5 — Run the full agent** (pipeline + Telegram bot, recommended)
```bash
python main.py --serve
```

This starts the pipeline (every 15 min) **and** listens to your Telegram messages at the same time. You can now chat with it: "compré 50 de Apple a 178", "portafolio", "vigila Tesla".

**6 — Or schedule it as a background service** (runs automatically, no terminal needed)
```bash
make install-launchd    # macOS — background agent, survives reboots
make install-cron       # alternative via crontab
```

> Note: `--serve` and the scheduler/launchd modes are mutually exclusive. Use `--serve` if you want the interactive Telegram bot. Use `make install-launchd` / `make install-cron` if you only need scheduled pipeline alerts without the bot.

---

### Linux (Ubuntu / Debian / Raspberry Pi)

**1 — Install Python and dependencies**
```bash
sudo apt update && sudo apt install -y python3.11 python3.11-venv python3-pip git make
```

> Fedora/RHEL: `sudo dnf install python3.11 git make`
> Arch: `sudo pacman -S python git make`

**2 — Clone and set up**
```bash
git clone https://github.com/Richorrific-Rea/insider-agent.git
cd insider-agent
make setup
```

**3 — Test it**
```bash
python3 main.py --once --dry-run
```

**4 — Schedule it**

Recommended for a server (survives reboots, logs via journald):
```bash
make install-systemd
systemctl status insider-agent.timer   # verify it's running
journalctl -u insider-agent.service -f  # watch logs
```

Or simpler via crontab:
```bash
make install-cron
```

---

### Windows

#### Option A — WSL (recommended, 2 minutes)

Open PowerShell as Administrator:
```powershell
wsl --install
```

Restart, open the Ubuntu app, then follow the **Linux** instructions above.

#### Option B — Native Windows (PowerShell)

**1 — Install Python**

Download Python 3.11+ from [python.org/downloads](https://www.python.org/downloads/). During installation, **check "Add Python to PATH"**.

```powershell
python --version   # verify: should show 3.11+
```

**2 — Install Git**

Download from [git-scm.com](https://git-scm.com/download/win), install with default settings.

**3 — Clone and install**
```powershell
git clone https://github.com/Richorrific-Rea/insider-agent.git
cd insider-agent
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

**4 — Run the setup wizard**
```powershell
python setup.py
```

**5 — Test it**
```powershell
python main.py --once --dry-run
```

**6 — Schedule it** via Task Scheduler
```powershell
$action = New-ScheduledTaskAction `
  -Execute "$PWD\.venv\Scripts\python.exe" `
  -Argument "main.py --once" `
  -WorkingDirectory $PWD

$trigger = New-ScheduledTaskTrigger `
  -RepetitionInterval (New-TimeSpan -Minutes 15) `
  -RepetitionDuration (New-TimeSpan -Hours 7) `
  -At "09:00AM" -Daily

Register-ScheduledTask -TaskName "insider-agent" -Action $action -Trigger $trigger -RunLevel Highest
```

> `make` commands don't work natively on Windows. Use the PowerShell commands above, or switch to WSL.

---

### Setup wizard walkthrough

After `make setup` (or `python setup.py` on Windows):

**1. EDGAR User-Agent** *(required)*
The SEC requires you to identify yourself: `Your Name your@email.com`. This only goes in your local `.env` file — never committed to the repo.

**2. LLM API key** *(optional — free options available)*

| Provider | Where to get it | Cost |
|---|---|---|
| **Groq** | [console.groq.com](https://console.groq.com) | Free |
| **Google Gemini** | [aistudio.google.com](https://aistudio.google.com) | Free |
| Anthropic | [console.anthropic.com](https://console.anthropic.com) | Paid |
| OpenAI | [platform.openai.com](https://platform.openai.com) | Paid |
| Ollama | [ollama.com](https://ollama.com) | Free (runs locally) |

Skip entirely to use plain-text signal summaries at no cost.

**3. Telegram bot** *(optional — needed to receive alerts)*
- Open Telegram → search `@BotFather` → send `/newbot`
- The wizard validates your token, asks you to send a message to your bot, then **auto-detects your chat ID**
- Sends a test message to confirm everything works

**4. Signal filters** *(optional — defaults work well)*
Minimum trade value ($100k default), allowed insider roles (CEO/CFO/PRES/DIR), cluster window, confluence window.

---

## Configuration reference

All settings via environment variables in `.env`. See `.env.example` for the full list.

| Variable | Default | Description |
|---|---|---|
| `EDGAR_USER_AGENT` | — | **Required.** `"Your Name your@email.com"` |
| `LLM_PROVIDER` | `anthropic` | `groq` / `gemini` / `openai` / `ollama` / `custom` |
| `LLM_API_KEY` | — | API key for chosen provider |
| `LLM_MODEL` | auto | Overrides the default model for the provider |
| `TELEGRAM_BOT_TOKEN` | — | From @BotFather |
| `TELEGRAM_CHAT_ID` | — | Auto-detected by setup wizard |
| `MIN_TRADE_VALUE_USD` | `100000` | Minimum insider trade size to consider |
| `ALLOWED_ROLES` | `CEO,CFO,PRES,DIR` | Insider roles to include |
| `PRICE_SPIKE_PCT` | `5.0` | Min % move + volume to trigger in main flow |
| `WATCHLIST_SPIKE_PCT` | `7.0` | Min % move for standalone watchlist alerts |
| `USE_CONGRESS_DATA` | `true` | Fetch congressional trading data |
| `CONFLUENCE_WINDOW_DAYS` | `14` | Days window for insider + politician correlation |
| `STATE_BACKEND` | `file` | `file` / `firestore` / `gcs` |

---

## Deployment options

### A — Local machine (start here)

```bash
make install-launchd    # macOS
make install-cron       # Linux or macOS
make install-systemd    # Linux with systemd
```

Full guide: [LOCAL_DEPLOY.md](LOCAL_DEPLOY.md)

### B — GCP Cloud Functions gen2

Runs serverlessly, triggered by Cloud Scheduler every 15 min during market hours.

```bash
gcloud auth login
make gcp-enable-apis
make gcp-create-sa
make gcp-create-secrets    # paste API keys interactively — never stored in repo
make deploy
make scheduler             # Mon–Fri, 9am–4pm ET, every 15 min
```

Full guide: [DEPLOY.md](DEPLOY.md)

---

## Module map

| File | What it does |
|---|---|
| `config.py` | Loads all settings from environment / `.env` |
| `edgar_client.py` | EDGAR Atom feed + XML downloader (rate-limited, fair-access UA) |
| `form4_parser.py` | Parses Form 4 XML → `Transaction` dataclass |
| `congress_client.py` | Senate EFTS + House eFD congressional trades |
| `congress_parser.py` | `PoliticianTrade` dataclass |
| `sec_extra_client.py` | EDGAR 13D / 13G / 13F fetching and parsing |
| `finra_client.py` | Short interest via Yahoo Finance |
| `options_client.py` | Unusual options via Yahoo Finance options chain |
| `price_client.py` | Price snapshots + spike/drop detection via Yahoo Finance |
| `news_client.py` | RSS fetcher (Yahoo/Reuters/MarketWatch/SeekingAlpha) + LLM ticker extraction |
| `signals.py` | Hard filters + insider cluster detection |
| `scorer.py` | Multi-source scoring engine → `TierScore` |
| `enrich.py` | LLM analysis with 80s broker personality (multi-provider) |
| `notify.py` | Telegram messages — optimized for mobile |
| `exit_signals.py` | Exit signal detection + scoring for portfolio positions |
| `portfolio.py` | Portfolio positions + watchlist store |
| `pipeline.py` | Full orchestration — all sources → score → alert |
| `main.py` | CLI entrypoint — `--serve`, `--once`, `--dry-run`, portfolio commands |
| `telegram_listener.py` | Telegram long-polling bot — parses natural language via LLM |
| `cloud_function.py` | GCP Cloud Functions gen2 HTTP entrypoint |
| `setup.py` | Interactive configuration wizard |
| `state.py` | Deduplication + state cache (File / Firestore / GCS) |

---

## Development

```bash
make install    # create .venv and install dependencies
make test       # run pytest (73 tests, zero network calls)
make run-dry    # one pipeline cycle, prints to terminal
make lint       # syntax check all modules
```

---

## Guardrails

- **No financial advice** — LLM prompts explicitly prohibit price predictions or buy/sell recommendations. The company analysis and "theory" sections are clearly framed as speculation, not guidance. Every Telegram message includes a disclaimer.
- **EDGAR fair access** — identifiable `User-Agent` header + ≤10 req/s enforced. Do not remove the rate limiter.
- **Secrets stay local** — `.env` and `state.json` are in `.gitignore`. The setup wizard never commits credentials. Use `.env.example` as a template.
