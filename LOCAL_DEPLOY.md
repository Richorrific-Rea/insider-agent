# Deploy en VM local / máquina propia

Tres opciones según tu OS. Todas usan el mismo entrypoint:
```
python main.py --once
```

---

## Prerequisitos (todas las opciones)

```bash
# 1. Clona el repo
git clone https://github.com/Richorrific-Rea/insider-agent.git
cd insider-agent

# 2. Python 3.11+ requerido
python3 --version   # debe ser 3.11 o superior

# 3. Crea el virtualenv e instala dependencias
make install
# (equivalente a: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)

# 4. Configura tus variables de entorno
cp .env.example .env
# Edita .env con tu editor favorito:
#   EDGAR_USER_AGENT="Tu Nombre tu@email.com"   ← obligatorio
#   ANTHROPIC_API_KEY=sk-ant-...                 ← opcional (fallback a texto plano)
#   SLACK_WEBHOOK_URL=https://hooks.slack.com/... ← opcional (sin esto usa --dry-run)

# 5. Prueba que funciona antes de programar
make run-dry
```

---

## Opción A — crontab (Linux / macOS, más simple)

**Cuándo usarlo:** VM sencilla, Raspberry Pi, Mac siempre encendida, cualquier Unix.

```bash
# Abre el crontab de tu usuario
crontab -e
```

Agrega esta línea (ajusta la ruta al directorio donde clonaste el repo):

```cron
# insider-agent — cada 15 min, lun-vie, 09:00-16:00 ET
# Si tu servidor está en UTC, cambia 9-16 por 13-20 (ET verano = UTC-4)
*/15 9-16 * * 1-5 cd /home/YOUR_USER/insider-agent && .venv/bin/python main.py --once >> insider-agent.log 2>&1
```

Verifica que quedó guardado:
```bash
crontab -l
```

**Ver logs:**
```bash
tail -f /home/YOUR_USER/insider-agent/insider-agent.log
```

**Quitar el cron:**
```bash
crontab -e   # borra la línea manualmente
```

---

## Opción B — systemd timer (Linux, más robusto)

**Cuándo usarlo:** servidor Linux dedicado (Ubuntu, Debian, etc.) donde quieres que el servicio sobreviva reinicios y tenga logs en journald.

### 1. Instalar los archivos de servicio

```bash
# Reemplaza YOUR_USER con tu usuario Linux
INSTALL_DIR=$(pwd)
USER=$(whoami)

# Copia los templates y rellena tu usuario y ruta
sed "s|YOUR_USER|$USER|g; s|/home/YOUR_USER/insider-agent|$INSTALL_DIR|g" \
    deploy/insider-agent.service | sudo tee /etc/systemd/system/insider-agent.service

sed "s|YOUR_USER|$USER|g" \
    deploy/insider-agent.timer | sudo tee /etc/systemd/system/insider-agent.timer
```

> **Nota sobre la zona horaria:** el timer usa UTC. Si tu servidor está en UTC:
> - Horario de verano (ET = UTC-4): usa `13:00-20:00`
> - Horario de invierno (ET = UTC-5): usa `14:00-21:00`
>
> Edita `/etc/systemd/system/insider-agent.timer` y ajusta la línea `OnCalendar`.

### 2. Habilitar y arrancar

```bash
sudo systemctl daemon-reload
sudo systemctl enable insider-agent.timer    # arranca en cada reboot
sudo systemctl start insider-agent.timer     # arranca ahora
```

### 3. Verificar

```bash
# Ver el estado del timer
systemctl status insider-agent.timer

# Ver cuándo se disparará la próxima vez
systemctl list-timers insider-agent.timer

# Correr manualmente una vez (para probar)
sudo systemctl start insider-agent.service

# Ver logs
journalctl -u insider-agent.service -f
```

### 4. Desinstalar

```bash
sudo systemctl stop insider-agent.timer
sudo systemctl disable insider-agent.timer
sudo rm /etc/systemd/system/insider-agent.{service,timer}
sudo systemctl daemon-reload
```

---

## Opción C — launchd (macOS)

**Cuándo usarlo:** Mac de escritorio/laptop que usas como servidor ligero.

### 1. Instalar el plist

```bash
INSTALL_DIR=$(pwd)
USER=$(whoami)

sed "s|YOUR_USER|$USER|g; s|/Users/YOUR_USER/insider-agent|$INSTALL_DIR|g" \
    deploy/com.insider-agent.plist \
    > ~/Library/LaunchAgents/com.insider-agent.plist
```

> **Nota sobre la zona horaria:** el plist usa la zona horaria local de tu Mac.
> Si tu Mac está en ET, las horas `9`–`15` en el plist corresponden directamente al horario de mercado.
> Si está en otra zona, ajusta los valores `Hour` en el plist.

### 2. Cargar el agente

```bash
launchctl load ~/Library/LaunchAgents/com.insider-agent.plist
```

### 3. Verificar

```bash
# Ver si está cargado
launchctl list | grep insider-agent

# Correr manualmente una vez
launchctl start com.insider-agent

# Ver logs
tail -f /Users/YOUR_USER/insider-agent/insider-agent.log
```

### 4. Desinstalar

```bash
launchctl unload ~/Library/LaunchAgents/com.insider-agent.plist
rm ~/Library/LaunchAgents/com.insider-agent.plist
```

---

## Makefile targets para VM local

```bash
make install-cron      # agrega entrada al crontab del usuario actual
make uninstall-cron    # elimina la entrada del crontab
make install-systemd   # instala y activa el servicio + timer systemd
make uninstall-systemd # desactiva y elimina el servicio systemd
make install-launchd   # instala el plist de launchd en macOS
make uninstall-launchd # desinstala el plist de launchd
make logs-local        # tail del log local
```

---

## Comparación rápida

| | crontab | systemd timer | launchd |
|---|---|---|---|
| OS | Linux / macOS | Linux | macOS |
| Complejidad | Baja | Media | Media |
| Logs | Archivo .log | journald (rotación automática) | Archivo .log |
| Sobrevive reboot | ✅ | ✅ | ✅ (si está en LaunchAgents) |
| Recupera runs perdidos | ❌ | ✅ (`Persistent=true`) | ❌ |
| Recomendado para | Dev / uso personal | Servidor de producción | Mac personal |

---

## Troubleshooting

**El cron no se ejecuta:**
- Verifica la ruta absoluta al `.venv/bin/python`
- En macOS, el cron necesita permisos de "Full Disk Access" en System Preferences → Security

**`EDGAR_USER_AGENT` no encontrado:**
- Asegúrate de que `.env` existe en el directorio del proyecto
- El cron no hereda tu shell, entonces las variables de entorno deben estar en `.env` o en la línea del cron con `KEY=VALUE comando`

**Señales duplicadas en Slack:**
- Revisa que `state.json` persiste entre ejecuciones (no en `/tmp`)
- Verifica `STATE_FILE_PATH` en tu `.env`
