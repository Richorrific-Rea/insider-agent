# Deploy a GCP Cloud Functions gen2

Pipeline destino: proyecto `datadog-sandbox`, región `us-central1`, función `insider-agent-run`.
Se dispara cada 15 minutos, lunes a viernes, 09:00–16:00 ET (horario de mercado).

> **Tú ejecutas estos pasos.** El código ya está listo; solo necesitas auth y secretos.

---

## Prerequisitos

```bash
# Instala gcloud si no lo tienes
brew install --cask google-cloud-sdk

# Python deps locales (para testear antes de subir)
make install
make test
```

---

## Paso 1 — Autenticación

```bash
gcloud auth login
gcloud config set project datadog-sandbox
```

---

## Paso 2 — Habilitar APIs

```bash
make gcp-enable-apis
```

Habilita: Cloud Functions, Cloud Scheduler, Secret Manager, Cloud Run, Cloud Build.

---

## Paso 3 — Service Account

```bash
make gcp-create-sa
```

Crea `insider-agent-sa@datadog-sandbox.iam.gserviceaccount.com` con permisos de:
- `secretmanager.secretAccessor` (leer secretos)
- `datastore.user` (leer/escribir Firestore para el state)

---

## Paso 4 — Secretos en Secret Manager

```bash
make gcp-create-secrets
```

Te pedirá pegar el valor de cada secreto interactivamente:
- `EDGAR_USER_AGENT` → `"Ricardo Rea ricarorea2584@gmail.com"`
- `ANTHROPIC_API_KEY` → tu clave de Anthropic
- `SLACK_WEBHOOK_URL` → tu webhook de Slack

Los secretos **nunca tocan el repositorio ni el código** — GCP los inyecta como variables de entorno en tiempo de ejecución.

---

## Paso 5 — Deploy de la Cloud Function

```bash
make deploy
```

Lo que hace:
- Sube el código fuente al proyecto
- Crea la función gen2 con runtime Python 3.11
- Inyecta los 3 secretos vía `--set-secrets`
- Configura `STATE_BACKEND=firestore` y `GCP_PROJECT=datadog-sandbox`
- Sin acceso HTTP público (`--no-allow-unauthenticated`); solo el scheduler puede invocarla

---

## Paso 6 — Crear el Cloud Scheduler

```bash
make scheduler
```

Crea el job `insider-agent-trigger` con schedule:
```
*/15 9-16 * * 1-5    # cada 15 min, lun-vie, 09:00-16:00 ET
```
Usa OIDC con la service account para autenticar contra la función.

---

## Disparar manualmente

```bash
make trigger-now    # fuerza una ejecución inmediata del scheduler
```

---

## Ver logs

```bash
make logs           # últimas 50 líneas de logs de la función
```

---

## Actualizar tras un cambio de código

```bash
git push origin main   # dispara el CI
make deploy            # re-deploy a Cloud Functions
```

---

## Arquitectura de secretos

```
Secret Manager
  ├── EDGAR_USER_AGENT      → env var en la función
  ├── ANTHROPIC_API_KEY     → env var en la función
  └── SLACK_WEBHOOK_URL     → env var en la función

Firestore (datadog-sandbox)
  └── collection: insider_agent_state
        └── document: state
              ├── seen_accessions: [...]
              └── recent_transactions: [...]
```

El state en Firestore permite que cada invocación del scheduler retome donde la anterior lo dejó, sin duplicar señales.

---

## Costos estimados (uso típico)

| Servicio | Uso mensual estimado | Costo |
|---|---|---|
| Cloud Functions gen2 | ~1,300 invocaciones/mes × 300s = ~50h CPU | ~$0–5 |
| Cloud Scheduler | 1 job | Gratis (primeros 3 jobs) |
| Secret Manager | 3 secretos, ~1,300 accesos | ~$0 |
| Firestore | ~1,300 escrituras/mes | ~$0 |
| Anthropic API | ~1,300 señales/mes (si todas pasan filtros) | Variable |

En la práctica, pocas señales pasan los filtros, por lo que el costo de Anthropic es bajo.
