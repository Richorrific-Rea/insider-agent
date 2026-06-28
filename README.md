# insider-agent

A scheduled pipeline that monitors **SEC EDGAR Form 4 filings**, congressional trading disclosures, activist investor filings, short interest, and unusual options activity — cross-references them into a confidence score, and sends actionable alerts to **Telegram**.

It also watches your portfolio and fires **exit alerts** when the same signals start reversing.

> **Disclaimer:** This system generates **ideas to research**, not investment recommendations. Every alert includes a mandatory disclaimer. Do not use this software as the basis for financial decisions.

---

## What it does

Every 15 minutes during market hours (Mon–Fri 9am–4pm ET), the pipeline:

1. Fetches the latest **SEC EDGAR Form 4** filings (corporate insider purchases)
2. Cross-references **congressional trading disclosures** (Senate EFTS + House eFD)
3. Pulls **activist investor filings** (SEC 13D / 13G — anyone crossing ≥5% stake)
4. Fetches **institutional positions** (SEC 13F — funds with >$100M AUM)
5. Checks **short interest** trends via Yahoo Finance
6. Detects **unusual options activity** (call/put volume vs open interest)
7. Scores each ticker across all active sources → **BAJA / MEDIA / ALTA / MUY ALTA**
8. Enriches the alert with an **LLM brief** (personality scales with signal strength)
9. Sends the result to **Telegram**
10. Monitors your **portfolio** and fires exit alerts if the signals reverse

---

## Signal scoring

Each independent source that confirms the same thesis adds points. The key insight is **independence** — a corporate insider and a senator don't coordinate. When both buy the same stock, that's two completely separate actors reaching the same conclusion.

### Base weights

| Signal | Points | Why |
|---|---|---|
| CEO / CFO / President buying (Form 4) | 30 | Maximum insider knowledge |
| Director buying | 20 | High knowledge, less operational |
| Other officer buying | 15 | Partial knowledge |
| Politician buying (PTR) | 20 | Potential committee-level information |
| **Activist 13D** | **40** | Highest weight — deep due diligence + intent to influence |
| Passive 13G (≥5%) | 15 | High conviction, no active agenda |
| Institutional 13F new position | 10/fund, max 25 | Accumulation signal |
| Short interest declining | 15 | Short sellers covering |
| Unusual call options | 25 | Most forward-looking signal |

### Multipliers

**Magnitude** (trade size):

| Range | Multiplier |
|---|---|
| < $100k | ×0.5 |
| $100k – $500k | ×1.0 |
| $500k – $1M | ×1.3 |
| $1M – $5M | ×1.6 |
| > $5M | ×2.0 |

**Recency** (days since trade):

| Days | Multiplier |
|---|---|
| 0–3 | ×1.2 |
| 4–7 | ×1.0 |
| 8–14 | ×0.8 |
| 15–30 | ×0.6 |
| > 30 | ×0.3 |

### Bonuses

- **Insider cluster** (2+ distinct insiders buying same stock in one week): +10 / +20
- **Convergence** (number of independent signal types firing): +10 / +25 / +40 / +55

### Tiers

| Score | Tier | Description |
|---|---|---|
| 0 – 25 | **BAJA** | Single weak signal — log it |
| 26 – 55 | **MEDIA** | Solid signal, worth investigating |
| 56 – 85 | **ALTA** | Multiple independent sources converging |
| 86 + | **MUY ALTA** | Strong convergence — priority research |

---

## LLM personality

The agent's tone scales with signal strength. It acts like an **80s Wall Street broker** — calm and analytical at low tiers, progressively unhinged at MUY ALTA:

- **BAJA** → sober analyst, just the facts
- **MEDIA** → experienced broker, something interesting here
- **ALTA** → energetic 80s broker, "the smart money is moving"
- **MUY ALTA** → fully unhinged, CAPS LOCK, "THIS IS A MONSTER SETUP BABY"

The facts are always accurate. Only the tone changes. The disclaimer is always present.

Supports any LLM provider — Groq and Gemini have **free tiers**:

| Provider | Set via `LLM_PROVIDER=` | Free tier |
|---|---|---|
| Anthropic | `anthropic` | No |
| OpenAI | `openai` | No |
| **Groq** | `groq` | **Yes** |
| **Google Gemini** | `gemini` | **Yes** |
| Ollama (local) | `ollama` | **Yes** |
| Any OpenAI-compatible | `custom` + `LLM_BASE_URL` | Varies |

---

## Portfolio tracking & exit alerts

The agent remembers positions you've entered and monitors them for **exit signals** — the mirror of the entry signals but for sells:

- Insiders selling (Form 4, open-market sales)
- Politicians selling (PTR)
- Activists reducing stake
- Short interest rising
- Unusual PUT options

Exit alerts only fire at **ALTA (56+)** or **MUY ALTA (86+)** to avoid noise — insider selling is noisier than buying.

```bash
# After the agent sends a signal and you decide to buy:
python main.py --add IMVT 500 5.62 --note "MUY ALTA score=106 — 3 insiders same day"

# View your portfolio:
python main.py --portfolio

# When you exit the position:
python main.py --remove IMVT
```

---

## Quick start

### Requirements
- Python 3.11+
- Git
- A Telegram account (to receive alerts)

### Install & configure

```bash
git clone https://github.com/Richorrific-Rea/insider-agent.git
cd insider-agent

# Install dependencies and run the interactive setup wizard
make setup
```

The wizard walks you through:
1. **EDGAR User-Agent** — your name + email (required by SEC fair-access policy)
2. **LLM API key** — Groq or Gemini are free; skip for plain-text fallback
3. **Telegram bot** — creates bot via @BotFather, auto-detects your chat ID, sends a test message
4. **Signal filters** — optional (defaults work well out of the box)

### Test it

```bash
python main.py --once --dry-run   # prints signals to terminal, no Telegram
python main.py --once             # live run
```

### Schedule it (runs every 15 min automatically)

```bash
make install-launchd    # macOS (recommended)
make install-cron       # Linux or macOS
make install-systemd    # Linux with systemd
```

---

## Architecture

```
EDGAR Atom feed (Form 4)
Congressional PTR (Senate + House)
EDGAR 13D / 13G (activists)            ─→  scorer.py  ─→  enrich.py  ─→  notify.py
EDGAR 13F (institutional funds)              (score +       (LLM brief    (Telegram)
Yahoo Finance short interest                  tier)          per tier)
Yahoo Finance options chain
         │
         ▼
    portfolio.py  ←── user adds positions
         │
         ▼
    exit_signals.py  ─→  Telegram exit alert
```

### Module map

| File | Responsibility |
|---|---|
| `config.py` | Env-based Config dataclass with validation |
| `edgar_client.py` | EDGAR Atom feed + XML downloader (rate-limited) |
| `form4_parser.py` | ownershipDocument XML → `Transaction` |
| `congress_client.py` | Senate EFTS + House eFD congressional trades |
| `congress_parser.py` | `PoliticianTrade` dataclass |
| `sec_extra_client.py` | EDGAR 13D / 13G / 13F fetching and parsing |
| `finra_client.py` | Short interest via Yahoo Finance |
| `options_client.py` | Unusual options via Yahoo Finance options chain |
| `signals.py` | Hard filters + cluster detection |
| `scorer.py` | Multi-source scoring engine → `TierScore` |
| `enrich.py` | LLM brief with personality tiers (multi-provider) |
| `notify.py` | Telegram messages with tier-appropriate formatting |
| `exit_signals.py` | Exit signal detection + scoring |
| `portfolio.py` | User portfolio positions store |
| `pipeline.py` | Full orchestration (entry + exit cycles) |
| `main.py` | CLI entrypoint (`--once`, `--dry-run`, `--add`, `--portfolio`) |
| `cloud_function.py` | GCP Cloud Functions gen2 HTTP entrypoint |
| `setup.py` | Interactive configuration wizard |
| `state.py` | Dedup + cache (File / Firestore / GCS backends) |

---

## Configuration

All configuration is via environment variables. Copy `.env.example` to `.env` and fill in:

| Variable | Required | Description |
|---|---|---|
| `EDGAR_USER_AGENT` | **Yes** | `"Your Name your@email.com"` — SEC fair-access policy |
| `LLM_PROVIDER` | No | `anthropic` / `groq` / `gemini` / `openai` / `ollama` / `custom` |
| `LLM_API_KEY` | No | API key for chosen provider (skip for plain-text fallback) |
| `TELEGRAM_BOT_TOKEN` | No | From @BotFather |
| `TELEGRAM_CHAT_ID` | No | Auto-detected by setup wizard |
| `MIN_TRADE_VALUE_USD` | No | Minimum insider trade size (default: $100,000) |
| `ALLOWED_ROLES` | No | `CEO,CFO,PRES,DIR` (default) |
| `STATE_BACKEND` | No | `file` (default) / `firestore` / `gcs` |

See `.env.example` for the full list with descriptions.

---

## Deployment options

### A — Local machine / Mac (recommended to start)

```bash
make install-launchd    # macOS background agent
make install-cron       # crontab (Linux / macOS)
make install-systemd    # systemd timer (Linux)
```

See [LOCAL_DEPLOY.md](LOCAL_DEPLOY.md) for the full guide.

### B — GCP Cloud Functions gen2

```bash
gcloud auth login
make gcp-enable-apis
make gcp-create-sa
make gcp-create-secrets   # paste keys interactively
make deploy
make scheduler            # Cloud Scheduler: every 15 min, Mon–Fri, 9am–4pm ET
```

See [DEPLOY.md](DEPLOY.md) for the full guide.

---

## Development

```bash
make install    # create venv + install deps
make test       # run pytest (73 tests, no network)
make run-dry    # one cycle, dry run
make lint       # py_compile all modules
```

---

## Guardrails

- **No financial advice** — the LLM system prompt explicitly prohibits price predictions or buy/sell recommendations. Every Telegram message includes a mandatory disclaimer.
- **EDGAR fair access** — identifiable User-Agent header + ≤10 req/s (≥0.15s between requests). Do not remove the rate limiter.
- **Secrets stay local** — `.env` and `state.json` are in `.gitignore`. Never commit API keys or tokens.
