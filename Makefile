# insider-agent Makefile
# GCP target: project=datadog-sandbox, region=us-central1, function=insider-agent-run

PROJECT     ?= datadog-sandbox
REGION      ?= us-central1
FUNCTION    ?= insider-agent-run
RUNTIME     ?= python311
SA          ?= insider-agent-sa@$(PROJECT).iam.gserviceaccount.com
SCHEDULE    ?= "*/15 9-16 * * 1-5"   # every 15 min, Mon-Fri, 09:00-16:00 ET
TZ          ?= America/New_York

# ── Local dev ─────────────────────────────────────────────────────────────────

.PHONY: install
install:
	python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest

.PHONY: test
test:
	.venv/bin/pytest tests/ -v

.PHONY: run-dry
run-dry:
	.venv/bin/python main.py --once --dry-run

.PHONY: lint
lint:
	.venv/bin/python -m py_compile \
		config.py edgar_client.py form4_parser.py signals.py \
		enrich.py notify.py state.py pipeline.py main.py cloud_function.py
	@echo "Syntax OK"

# ── GCP — run these steps yourself (requires gcloud auth) ────────────────────

.PHONY: gcp-enable-apis
gcp-enable-apis:
	gcloud services enable \
		cloudfunctions.googleapis.com \
		cloudscheduler.googleapis.com \
		secretmanager.googleapis.com \
		run.googleapis.com \
		cloudbuild.googleapis.com \
		--project=$(PROJECT)

.PHONY: gcp-create-sa
gcp-create-sa:
	gcloud iam service-accounts create insider-agent-sa \
		--display-name="Insider Agent" \
		--project=$(PROJECT)
	gcloud projects add-iam-policy-binding $(PROJECT) \
		--member="serviceAccount:$(SA)" \
		--role="roles/secretmanager.secretAccessor"
	gcloud projects add-iam-policy-binding $(PROJECT) \
		--member="serviceAccount:$(SA)" \
		--role="roles/datastore.user"

.PHONY: gcp-create-secrets
gcp-create-secrets:
	@echo "Creating secrets (you will be prompted to paste values):"
	@echo "--- EDGAR_USER_AGENT ---"
	@read -p "Value: " v && printf "%s" "$$v" | \
		gcloud secrets create EDGAR_USER_AGENT --data-file=- --project=$(PROJECT) || \
		printf "%s" "$$v" | gcloud secrets versions add EDGAR_USER_AGENT --data-file=- --project=$(PROJECT)
	@echo "--- ANTHROPIC_API_KEY ---"
	@read -p "Value: " v && printf "%s" "$$v" | \
		gcloud secrets create ANTHROPIC_API_KEY --data-file=- --project=$(PROJECT) || \
		printf "%s" "$$v" | gcloud secrets versions add ANTHROPIC_API_KEY --data-file=- --project=$(PROJECT)
	@echo "--- SLACK_WEBHOOK_URL ---"
	@read -p "Value: " v && printf "%s" "$$v" | \
		gcloud secrets create SLACK_WEBHOOK_URL --data-file=- --project=$(PROJECT) || \
		printf "%s" "$$v" | gcloud secrets versions add SLACK_WEBHOOK_URL --data-file=- --project=$(PROJECT)

.PHONY: deploy
deploy:
	gcloud functions deploy $(FUNCTION) \
		--gen2 \
		--runtime=$(RUNTIME) \
		--region=$(REGION) \
		--source=. \
		--entry-point=run_pipeline \
		--trigger-http \
		--no-allow-unauthenticated \
		--service-account=$(SA) \
		--set-secrets="EDGAR_USER_AGENT=EDGAR_USER_AGENT:latest,ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,SLACK_WEBHOOK_URL=SLACK_WEBHOOK_URL:latest" \
		--set-env-vars="STATE_BACKEND=firestore,GCP_PROJECT=$(PROJECT)" \
		--memory=256Mi \
		--timeout=300s \
		--project=$(PROJECT)

.PHONY: scheduler
scheduler:
	gcloud scheduler jobs create http insider-agent-trigger \
		--location=$(REGION) \
		--schedule=$(SCHEDULE) \
		--time-zone=$(TZ) \
		--uri="$$(gcloud functions describe $(FUNCTION) --gen2 --region=$(REGION) --project=$(PROJECT) --format='value(serviceConfig.uri)')" \
		--oidc-service-account-email=$(SA) \
		--project=$(PROJECT) || \
	gcloud scheduler jobs update http insider-agent-trigger \
		--location=$(REGION) \
		--schedule=$(SCHEDULE) \
		--time-zone=$(TZ) \
		--project=$(PROJECT)

.PHONY: trigger-now
trigger-now:
	gcloud scheduler jobs run insider-agent-trigger \
		--location=$(REGION) \
		--project=$(PROJECT)

.PHONY: logs
logs:
	gcloud functions logs read $(FUNCTION) \
		--gen2 \
		--region=$(REGION) \
		--project=$(PROJECT) \
		--limit=50

# ── Local VM deploy ───────────────────────────────────────────────────────────

INSTALL_DIR ?= $(shell pwd)
LOCAL_USER  ?= $(shell whoami)
# Cron schedule: every 15 min Mon-Fri 09:00-16:00 (adjust hours for your TZ)
LOCAL_CRON_SCHEDULE ?= */15 9-16 * * 1-5
LOG_FILE    ?= $(INSTALL_DIR)/insider-agent.log

.PHONY: install-cron
install-cron:
	@echo "Adding crontab entry for user $(LOCAL_USER)..."
	(crontab -l 2>/dev/null | grep -v "insider-agent"; \
	 echo "$(LOCAL_CRON_SCHEDULE) cd $(INSTALL_DIR) && $(INSTALL_DIR)/.venv/bin/python main.py --once >> $(LOG_FILE) 2>&1") | crontab -
	@echo "Done. Run 'crontab -l' to verify."

.PHONY: uninstall-cron
uninstall-cron:
	@echo "Removing insider-agent crontab entry..."
	(crontab -l 2>/dev/null | grep -v "insider-agent") | crontab -
	@echo "Done."

.PHONY: install-systemd
install-systemd:
	@echo "Installing systemd service and timer..."
	sed "s|YOUR_USER|$(LOCAL_USER)|g; s|/home/YOUR_USER/insider-agent|$(INSTALL_DIR)|g" \
		deploy/insider-agent.service | sudo tee /etc/systemd/system/insider-agent.service > /dev/null
	sed "s|YOUR_USER|$(LOCAL_USER)|g" \
		deploy/insider-agent.timer | sudo tee /etc/systemd/system/insider-agent.timer > /dev/null
	sudo systemctl daemon-reload
	sudo systemctl enable insider-agent.timer
	sudo systemctl start insider-agent.timer
	@echo "Done. Run 'systemctl status insider-agent.timer' to verify."

.PHONY: uninstall-systemd
uninstall-systemd:
	sudo systemctl stop insider-agent.timer || true
	sudo systemctl disable insider-agent.timer || true
	sudo rm -f /etc/systemd/system/insider-agent.{service,timer}
	sudo systemctl daemon-reload
	@echo "Systemd service and timer removed."

.PHONY: run-systemd-now
run-systemd-now:
	sudo systemctl start insider-agent.service

.PHONY: install-launchd
install-launchd:
	@echo "Installing launchd agent for user $(LOCAL_USER)..."
	sed "s|YOUR_USER|$(LOCAL_USER)|g; s|/Users/YOUR_USER/insider-agent|$(INSTALL_DIR)|g" \
		deploy/com.insider-agent.plist > ~/Library/LaunchAgents/com.insider-agent.plist
	launchctl load ~/Library/LaunchAgents/com.insider-agent.plist
	@echo "Done. Run 'launchctl list | grep insider-agent' to verify."

.PHONY: uninstall-launchd
uninstall-launchd:
	launchctl unload ~/Library/LaunchAgents/com.insider-agent.plist || true
	rm -f ~/Library/LaunchAgents/com.insider-agent.plist
	@echo "launchd agent removed."

.PHONY: run-launchd-now
run-launchd-now:
	launchctl start com.insider-agent

.PHONY: logs-local
logs-local:
	tail -f $(LOG_FILE)
