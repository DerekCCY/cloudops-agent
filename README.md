conda create -n cloudagent python=3.11
conda activate cloudagent
pip install -r requirements.txt

### Run locally
REPO_ROOT="Your repo"
uvicorn app.main:app --reload --port 8000
curl http://127.0.0.1:8000/health
curl -X POST "http://127.0.0.1:8000/generate" \
  -H "Content-Type: application/json" \
  -d "{
    \"text\": \"請分析這個 ${REPO_ROOT}。先呼叫 project_analyzer，確認 port 與啟動方式。然後呼叫 cloudrun_config_generator 產生 Cloud Run 設定模板，寫入 deploy/ 資料夾（service.yaml, cloudrun_deploy.sh, README_cloudrun.md）。不要做真部署，只給 dry-run 驗證指令。然後再run cloudrun_review_report去生成summary\"}"

### Run with docker
docker build -t cloudops-agent
docker run --rm --name cloudops\
  -p 8001:8000 \
  --env-file .env \
  -e PORT=8000 \
  -e WORKSPACE_ROOT=/workspace \
  -v "$(pwd)":/workspace \

Need to create .env and put your GEMINI_API_KEY=XXX.

### Run with GCP
chmod +x cloudrun_deploy.sh
(Change these settings with your own)
export PROJECT_ID=gmailn8n-471501
export REGION=us-central1
export SERVICE_NAME=cloudops-agent
export REPO_NAME=cloudops
export IMAGE_NAME=cloudops-agent

export MODE=full
export TAG=$(git rev-parse --short HEAD)

##### private service
export INVOKER_MEMBER="user:your_gmail@gmail.com"

./cloudrun_deploy.sh
If you don't want to rebuild image, run
export MODE=deploy
./cloudrun_deploy_prod.sh

#### Check whether running on cloud successfully
gcloud run services describe cloudops-agent \
  --region us-central1 --project gmailn8n-471501 \
  --format="yaml(status.url,status.conditions)"

gcloud run services logs read cloudops-agent \
  --region us-central1 --project gmailn8n-471501 \
  --limit 200

URL="$(gcloud run services describe cloudops-agent --region us-central1 --project gmailn8n-471501 --format='value(status.url)')"
TOKEN="$(gcloud auth print-identity-token)"

curl -s "$URL/healthz"
curl -I "$URL/docs"

curl -X POST "$URL/generate" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text":"hello"}'
