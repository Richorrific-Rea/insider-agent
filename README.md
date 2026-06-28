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

## Installation

### What you need before starting
- **Python 3.11 or newer**
- **Git**
- **A Telegram account** (free) — this is where alerts are delivered
- **An LLM API key** (optional but recommended) — Groq and Gemini are free

---

### macOS

**Step 1 — Install Homebrew** (if you don't have it)
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

**Step 2 — Install Python and Git**
```bash
brew install python@3.11 git
```

**Step 3 — Clone and set up**
```bash
git clone https://github.com/Richorrific-Rea/insider-agent.git
cd insider-agent
make setup
```

`make setup` creates a virtual environment, installs all dependencies, and launches the interactive configuration wizard.

**Step 4 — Test it**
```bash
python main.py --once --dry-run
```

You should see signals printed in your terminal. If you see `INFO pipeline: No new qualifying insider signals` it means no signals passed the filters in this specific batch — that's normal. Try lowering `MIN_TRADE_VALUE_USD=10000` in your `.env` to force output.

**Step 5 — Schedule it** (runs automatically every 15 min)
```bash
make install-launchd    # recommended for Mac — runs in background, survives reboots
# or
make install-cron       # simpler alternative via crontab
```

---

### Linux (Ubuntu / Debian / Raspberry Pi)

**Step 1 — Install Python and dependencies**
```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip git make
```

> For other distros: use `dnf install python3.11 git make` (Fedora/RHEL) or `pacman -S python git make` (Arch).

**Step 2 — Clone and set up**
```bash
git clone https://github.com/Richorrific-Rea/insider-agent.git
cd insider-agent
make setup
```

**Step 3 — Test it**
```bash
python3 main.py --once --dry-run
```

**Step 4 — Schedule it**

Recommended for a persistent server (survives reboots, logs via journald):
```bash
make install-systemd
```

Or via crontab:
```bash
make install-cron
```

Check the timer is running:
```bash
systemctl status insider-agent.timer
```

View logs:
```bash
journalctl -u insider-agent.service -f
```

---

### Windows

There are two options. **WSL is strongly recommended** — it's simpler and everything works out of the box.

#### Option A — WSL (Windows Subsystem for Linux) — recommended

**Step 1 — Enable WSL** (run in PowerShell as Administrator)
```powershell
wsl --install
```
Restart your computer when prompted. This installs Ubuntu by default.

**Step 2 — Open Ubuntu** from the Start menu and follow the Linux instructions above.

That's it — WSL gives you a full Linux environment on Windows.

#### Option B — Native Windows (PowerShell)

**Step 1 — Install Python**

Download Python 3.11+ from [python.org/downloads](https://www.python.org/downloads/). During installation, **check "Add Python to PATH"**.

Verify:
```powershell
python --version   # should show 3.11 or newer
```

**Step 2 — Install Git**

Download from [git-scm.com](https://git-scm.com/download/win) and install with default settings.

**Step 3 — Clone the repo**
```powershell
git clone https://github.com/Richorrific-Rea/insider-agent.git
cd insider-agent
```

**Step 4 — Create virtual environment and install dependencies**

Windows does not have `make` by default, so run the underlying commands directly:
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

**Step 5 — Run the setup wizard**
```powershell
python setup.py
```

**Step 6 — Test it**
```powershell
python main.py --once --dry-run
```

**Step 7 — Schedule it on Windows**

Open Task Scheduler and create a new task:
- **Trigger:** Daily, repeat every 15 minutes from 9:00 AM to 4:00 PM
- **Action:** Start a program
  - Program: `C:\path\to\insider-agent\.venv\Scripts\python.exe`
  - Arguments: `main.py --once`
  - Start in: `C:\path\to\insider-agent`
- **Conditions:** Run only when network is available

Or use the quick PowerShell command to register the task:
```powershell
$action = New-ScheduledTaskAction `
  -Execute "$PWD\.venv\Scripts\python.exe" `
  -Argument "main.py --once" `
  -WorkingDirectory $PWD

$trigger = New-ScheduledTaskTrigger `
  -RepetitionInterval (New-TimeSpan -Minutes 15) `
  -RepetitionDuration (New-TimeSpan -Hours 7) `
  -At "09:00AM" `
  -Daily

Register-ScheduledTask `
  -TaskName "insider-agent" `
  -Action $action `
  -Trigger $trigger `
  -RunLevel Highest
```

> **Note for Windows users:** The `make install-*` commands in the Makefile do not work natively on Windows. Use the PowerShell Task Scheduler approach above, or switch to WSL.

---

### Setup wizard walkthrough

After running `make setup` (or `python setup.py` on Windows), the wizard guides you through:

**1. EDGAR User-Agent** *(required)*
The SEC requires you to identify yourself. Format: `Your Name your@email.com`. This is only used in the HTTP `User-Agent` header — it never gets committed to the repo.

**2. LLM API key** *(optional — free options available)*

| Provider | Where to get it | Cost |
|---|---|---|
| **Groq** | [console.groq.com](https://console.groq.com) | Free |
| **Google Gemini** | [aistudio.google.com](https://aistudio.google.com) | Free |
| Anthropic (Claude) | [console.anthropic.com](https://console.anthropic.com) | Paid |
| OpenAI | [platform.openai.com](https://platform.openai.com) | Paid |
| Ollama (local) | [ollama.com](https://ollama.com) | Free (runs on your machine) |

Skip this step to use plain-text signal summaries at no cost.

**3. Telegram bot** *(optional — needed to receive alerts)*
- Open Telegram → search `@BotFather` → send `/newbot` → follow the steps
- The wizard validates your token, asks you to send a message to your bot, then **auto-detects your chat ID**
- Sends a test message to confirm everything works

**4. Signal filters** *(optional — defaults are good to start)*
Minimum trade value, allowed insider roles, cluster window. Skip to use defaults.

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
