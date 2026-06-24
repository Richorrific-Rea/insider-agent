# CLAUDE.md — insider-agent

## Qué hace este proyecto
Pipeline poll → filtro → enrich → notify para señales de insiders en SEC EDGAR Form 4.
Genera **ideas para investigar**, no recomendaciones de inversión.

## Estructura
- `config.py` → dataclass Config, cargado desde env
- `edgar_client.py` → EDGAR Atom feed + descarga de XML
- `form4_parser.py` → XML ownershipDocument → `Transaction`
- `signals.py` → `passes_filters` + `detect_clusters` → `Signal`
- `enrich.py` → Anthropic API (fallback a texto plano)
- `notify.py` → Telegram MarkdownV2 + disclaimer
- `state.py` → `StateStore` interface + FileStateStore / FirestoreStateStore / GCSStateStore
- `pipeline.py` → `run_once(cfg, dry_run)`
- `main.py` → CLI (`--once`, `--dry-run`)
- `cloud_function.py` → GCP gen2 HTTP entrypoint

## Comandos frecuentes
```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Run
python main.py --once --dry-run

# Tests
pytest tests/ -v

# Compile-check all modules
python -m py_compile config.py edgar_client.py form4_parser.py signals.py enrich.py notify.py state.py pipeline.py main.py cloud_function.py
```

## Guardrails no negociables
1. **Sin asesoría financiera** — el system prompt del LLM + el disclaimer de Telegram son obligatorios.
2. **EDGAR fair access** — User-Agent con contacto + ≤10 req/s. No quitar el rate limiter.
3. **Secretos fuera del repo** — `.env` y `state.json` en `.gitignore`. Nunca commitear claves.

## Dependencias externas
- SEC EDGAR (sin API key, solo User-Agent)
- Anthropic API (opcional; fallback si falta la key)
- Telegram bot token + chat ID (opcionales; `--dry-run` si faltan)
- GCP (opcional; solo si STATE_BACKEND=firestore o gcs)

## Fases del proyecto
- Fase 0: Scaffolding ✅
- Fase 1: Validación contra EDGAR real + hardening del parser
- Fase 2: Tests pytest con fixtures XML sintéticas
- Fase 3: GitHub Actions CI
- Fase 4: FirestoreStateStore / GCSStateStore completos
- Fase 5: Deploy GCP Cloud Function + Cloud Scheduler
