#!/usr/bin/env bash
set -euo pipefail

# =========================================================
# ✅ 你最常需要改 / 設定的地方（建議用 export，不用改檔）
# =========================================================
PROJECT_ID="${PROJECT_ID:-gmailn8n-471501}"      # 必填：GCP project id
REGION="${REGION:-us-central1}"                 # 你已選：us-central1
SERVICE_NAME="${SERVICE_NAME:-cloudops-agent}"  # Cloud Run service 名稱
REPO_NAME="${REPO_NAME:-cloudops}"              # Artifact Registry repo 名稱
IMAGE_NAME="${IMAGE_NAME:-cloudops-agent}"      # Docker image 名稱
TAG="${TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo v1)}"
SA_NAME="${SA_NAME:-cloudrun-runtime}"
SA_EMAIL="${SA_EMAIL:-${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com}"


# Production defaults（可不改）
MEMORY="${MEMORY:-1Gi}"
CPU="${CPU:-1}"
CONCURRENCY="${CONCURRENCY:-20}"
TIMEOUT="${TIMEOUT:-300}"            # 5 min
MIN_INSTANCES="${MIN_INSTANCES:-0}"  # demo 想更順可改 1
MAX_INSTANCES="${MAX_INSTANCES:-10}"
INGRESS="${INGRESS:-all}"            # all / internal / internal-and-cloud-load-balancing
ALLOW_UNAUTH="${ALLOW_UNAUTH:-false}" # production 預設 false（需要登入 token 才能呼叫）

# IAM invoker（Day6+）：指定誰能呼叫（Cloud Run private 時很重要）
INVOKER_MEMBER="${INVOKER_MEMBER:-}" # e.g. user:you@gmail.com 或 group:team@x.com

# Secret
SECRET_NAME="${SECRET_NAME:-gemini-api-key}"

IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${IMAGE_NAME}:${TAG}"

# =========================================================
# 0) 基本檢查
# =========================================================
if [[ "${PROJECT_ID}" == "YOUR_PROJECT_ID" ]]; then
  echo "ERROR: PROJECT_ID not set."
  echo "Run: export PROJECT_ID='your-project-id'"
  exit 1
fi

echo "== Deploy config =="
echo "PROJECT_ID:   ${PROJECT_ID}"
echo "REGION:       ${REGION}"
echo "SERVICE_NAME: ${SERVICE_NAME}"
echo "IMAGE_URI:    ${IMAGE_URI}"
echo "AUTH:         ALLOW_UNAUTH=${ALLOW_UNAUTH}"
echo

# =========================================================
# 1) 啟用必要 API
# =========================================================
echo "==> Enabling required GCP APIs..."
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  iam.googleapis.com \
  --project "${PROJECT_ID}"

# =========================================================
# 2) Artifact Registry：建立 docker repo（可重複執行）
# =========================================================
echo "==> Ensuring Artifact Registry repo exists..."
gcloud artifacts repositories create "${REPO_NAME}" \
  --repository-format=docker \
  --location="${REGION}" \
  --description="Docker repo for CloudOps Agent" \
  --project "${PROJECT_ID}" >/dev/null 2>&1 || true

# =========================================================
# 3) Secret Manager：確保 GEMINI_API_KEY secret 存在
#    第一次建立時需要你本機有 GEMINI_API_KEY
# =========================================================
echo "==> Ensuring Secret Manager secret exists: ${SECRET_NAME}"
if ! gcloud secrets describe "${SECRET_NAME}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  if [[ -z "${GEMINI_API_KEY:-}" ]]; then
    echo "ERROR: Secret ${SECRET_NAME} not found and GEMINI_API_KEY is not set locally."
    echo "First time create secret:"
    echo "  export GEMINI_API_KEY='...'"
    echo "  ./cloudrun_deploy_prod.sh"
    exit 1
  fi
  printf "%s" "$GEMINI_API_KEY" | gcloud secrets create "${SECRET_NAME}" --data-file=- --project "${PROJECT_ID}"
else
  echo "    Secret exists (skip create)."
fi

# =========================================================
# 4) Build & Push image（用 Cloud Build）
# =========================================================
echo "==> Building & pushing Docker image (Cloud Build)..."
gcloud builds submit --tag "${IMAGE_URI}" --project "${PROJECT_ID}"

# =========================================================
# 5) Deploy Cloud Run
# =========================================================
echo "==> Deploying Cloud Run service..."

AUTH_FLAG="--no-allow-unauthenticated"
if [[ "${ALLOW_UNAUTH}" == "true" ]]; then
  AUTH_FLAG="--allow-unauthenticated"
fi

# 你的 analyzer 只讀 container filesystem，所以鎖在 /app
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE_URI}" \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  ${AUTH_FLAG} \
  --service-account "${SA_EMAIL}" \
  --ingress "${INGRESS}" \
  --port 8000 \
  --memory "${MEMORY}" \
  --cpu "${CPU}" \
  --concurrency "${CONCURRENCY}" \
  --timeout "${TIMEOUT}" \
  --min-instances "${MIN_INSTANCES}" \
  --max-instances "${MAX_INSTANCES}" \
  --cpu-boost \
  --set-env-vars "WORKSPACE_ROOT=/app" \
  --set-secrets "GEMINI_API_KEY=${SECRET_NAME}:latest" \
  --labels "app=cloudops-agent,env=prod" \
  --execution-environment gen2

URL="$(gcloud run services describe "${SERVICE_NAME}" --region "${REGION}" --project "${PROJECT_ID}" --format="value(status.url)")"
echo
echo "==> Deployed URL: ${URL}"
echo

# =========================================================
# 6) Day6+：如果是 private service，授權 INVOKER_MEMBER 呼叫
# =========================================================
if [[ "${ALLOW_UNAUTH}" != "true" ]]; then
  if [[ -z "${INVOKER_MEMBER}" ]]; then
    echo "NOTE: Service is private. Set INVOKER_MEMBER to grant invoke permission, e.g.:"
    echo "  export INVOKER_MEMBER='user:you@gmail.com'"
    echo "  ./cloudrun_deploy_prod.sh"
  else
    echo "==> Granting Cloud Run Invoker to: ${INVOKER_MEMBER}"
    gcloud run services add-iam-policy-binding "${SERVICE_NAME}" \
      --member="${INVOKER_MEMBER}" \
      --role="roles/run.invoker" \
      --region "${REGION}" \
      --project "${PROJECT_ID}"
  fi
fi

# =========================================================
# 7) 驗收指令
# =========================================================
echo
echo "== Verify =="
echo "Health:"
echo "  curl -s ${URL}/healthz"
echo "Docs:"
echo "  curl -I ${URL}/docs"

if [[ "${ALLOW_UNAUTH}" != "true" ]]; then
  echo
  echo "Generate (auth required):"
  echo "  TOKEN=\$(gcloud auth print-identity-token)"
  echo "  curl -X POST ${URL}/generate \\"
  echo "    -H \"Authorization: Bearer \$TOKEN\" \\"
  echo "    -H \"Content-Type: application/json\" \\"
  echo "    -d '{\"text\":\"hello\"}'"
else
  echo
  echo "Generate (public):"
  echo "  curl -X POST ${URL}/generate -H \"Content-Type: application/json\" -d '{\"text\":\"hello\"}'"
fi
