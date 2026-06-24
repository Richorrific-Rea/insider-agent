# insider-agent

Pipeline programado que sondea SEC EDGAR (Form 4), detecta **compras de insiders** con valor de señal, las enriquece con un LLM y avisa por Telegram.

> **Aviso legal:** Este sistema genera **ideas para investigar**, no recomendaciones de inversión. Toda señal incluye el disclaimer correspondiente. No uses este software como base para decisiones financieras.

---

## Arquitectura

```
EDGAR Atom feed (Form 4)
       │
       ▼
edgar_client.py  ──→  form4_parser.py  ──→  signals.py
                                                  │
                                           passes_filters()
                                           detect_clusters()
                                                  │
                                            enrich.py (Anthropic)
                                                  │
                                            notify.py (Telegram)
                                                  │
                                            state.py (dedup)
```

### Módulos

| Archivo | Responsabilidad |
|---|---|
| `config.py` | Carga configuración desde env / `.env` |
| `edgar_client.py` | Descarga feed Atom + XML de filings |
| `form4_parser.py` | Parsea ownershipDocument XML → `Transaction` |
| `signals.py` | Filtros duros + detección de clusters |
| `enrich.py` | Brief factual vía Anthropic API (fallback a texto plano) |
| `notify.py` | Envía mensaje a Telegram (MarkdownV2) + disclaimer |
| `state.py` | Dedup de accessions + caché cross-poll (File / Firestore / GCS) |
| `pipeline.py` | Orquesta el ciclo completo |
| `main.py` | Entrypoint cron/local (`--once`, `--dry-run`) |
| `cloud_function.py` | Entrypoint GCP Cloud Functions gen2 |

---

## Instalación rápida

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edita .env con tu EDGAR_USER_AGENT (obligatorio)
```

## Ejecución local

```bash
# Dry-run: imprime señales en stdout sin postear a Telegram
python main.py --once --dry-run

# Live: envía a Telegram
python main.py --once
```

## Variables de entorno

Ver [.env.example](.env.example) para la lista completa con descripción.

Las obligatorias:
- `EDGAR_USER_AGENT` — `"Tu Nombre tu@email.com"` (requerido por SEC)

Las opcionales clave:
- `ANTHROPIC_API_KEY` — para briefs enriquecidos; sin ella usa texto plano
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` — para notificaciones; sin ellos solo funciona `--dry-run`

---

## Deploy

Hay dos opciones:

### Opción A — VM local / máquina propia

Ver [LOCAL_DEPLOY.md](LOCAL_DEPLOY.md) para la guía completa. Resumen rápido:

```bash
# crontab (Linux / macOS)
make install-cron

# systemd timer (Linux)
make install-systemd

# launchd (macOS)
make install-launchd
```

### Opción B — GCP Cloud Functions gen2

Ver [DEPLOY.md](DEPLOY.md) para la guía completa. Resumen:
1. `gcloud auth login`
2. `make gcp-enable-apis && make gcp-create-sa && make gcp-create-secrets`
3. `make deploy`
4. `make scheduler`

---

## Tests

```bash
pip install pytest
pytest tests/ -v
```

---

## Guardrails

- **Sin asesoría financiera:** El LLM tiene un system prompt que prohíbe explícitamente recomendaciones de precio. Telegram incluye siempre el disclaimer.
- **EDGAR fair access:** User-Agent identificable + ≤10 req/s (≥0.15 s entre requests).
- **Secretos fuera del repo:** `.env` y `state.json` están en `.gitignore`.
