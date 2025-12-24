#!/usr/bin/env bash
set -euo pipefail

# =========================
# Config
# =========================
PROJECT_ID="gmailn8n-471501"
REGION="us-central1"
SERVICE_NAME="cloudops-agent"
REPO_NAME="cloudops"
IMAGE_NAME="cloudops-agent"

TAG="$(git rev-parse --short HEAD 2>/dev/null || echo v1)"
IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${IMAGE_NAME}:${TAG}"

echo "== Build & Push =="
echo "IMAGE: ${IMAGE_URI}"

# =========================
# Build & push image
# =========================
gcloud builds submit \
  --project "${PROJECT_ID}" \
  --tag "${IMAGE_URI}"

# =========================
# Apply service.yaml
# =========================
echo
echo "== Deploy Cloud Run (service.yaml) =="

sed "s|IMAGE_PLACEHOLDER|${IMAGE_URI}|g" service.yaml | \
  gcloud run services replace - \
    --project "${PROJECT_ID}" \
    --region "${REGION}"

# =========================
# Output
# =========================
URL="$(gcloud run services describe "${SERVICE_NAME}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --format='value(status.url)')"

echo
echo "✅ Deployed:"
echo "URL: ${URL}"
