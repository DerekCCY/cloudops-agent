#!/usr/bin/env bash
set -euo pipefail

# =====================================
# Cloud Run deploy template (Day 6)
# - No secrets stored here
# - Default: dry-run
# =====================================

PROJECT_ID="${PROJECT_ID:-}"
REGION="${REGION:-${REGION}}"
SERVICE_NAME="${SERVICE_NAME:-${SERVICE_NAME}}"
IMAGE="${IMAGE:-}"  # Day 10 will set this after build+push

SERVICE_YAML="${SERVICE_YAML:-service.yaml}"
DRY_RUN="${DRY_RUN:-true}"  # true|false

if [[ -z "$PROJECT_ID" ]]; then
  echo "ERROR: PROJECT_ID is empty. Example: export PROJECT_ID='my-gcp-project'"
  exit 1
fi

if [[ ! -f "$SERVICE_YAML" ]]; then
  echo "ERROR: $SERVICE_YAML not found."
  exit 1
fi

echo "Project: $PROJECT_ID"
echo "Region:  $REGION"
echo "Service: $SERVICE_NAME"
echo "YAML:    $SERVICE_YAML"
echo "Image:   ${IMAGE:-<empty>}"
echo

echo "Render placeholders -> /tmp/service.rendered.yaml"
export PROJECT_ID REGION SERVICE_NAME IMAGE
envsubst < "$SERVICE_YAML" > /tmp/service.rendered.yaml

echo
if [[ "$DRY_RUN" == "true" ]]; then
  echo "[DRY RUN] Validating Cloud Run config (no deployment)"
  gcloud run services replace /tmp/service.rendered.yaml \
    --project "$PROJECT_ID" --region "$REGION" --dry-run
  echo
  echo "OK. Day 10: set IMAGE and run with DRY_RUN=false (remove --dry-run)."
else
  echo "[DEPLOY] Applying Cloud Run config"
  gcloud run services replace /tmp/service.rendered.yaml \
    --project "$PROJECT_ID" --region "$REGION"
fi
