# Cloud Run Config (Generated on Day 6)

This folder contains Cloud Run deployment configuration templates.

## Files
- `service.yaml`: declarative Cloud Run service config (**no secret values**)
- `cloudrun_deploy.sh`: helper script template (defaults to **dry-run**)

## Fill placeholders
You must provide:
- `PROJECT_ID`
- `REGION`
- `SERVICE_NAME`
- `IMAGE` (**Day 10** after build+push)

Example:
```bash
export PROJECT_ID="YOUR_GCP_PROJECT"
export REGION="${REGION}"
export SERVICE_NAME="${SERVICE_NAME}"
export IMAGE="us-docker.pkg.dev/YOUR_GCP_PROJECT/REPO/IMAGE:TAG"
envsubst < service.yaml > /tmp/service.rendered.yaml
```

## Secrets (do NOT put secret values in repo)
Create secrets in Secret Manager (example):
```bash
gcloud secrets create openai-api-key --replication-policy="automatic"
# then add secret versions via CLI or console (DO NOT commit values)
```

In `service.yaml`, secrets are referenced like:
```yaml
- name: OPENAI_API_KEY
  valueFrom:
    secretKeyRef:
      name: openai-api-key
      key: latest
```

## Dry run (no deployment)
```bash
chmod +x ./cloudrun_deploy.sh
export PROJECT_ID="YOUR_GCP_PROJECT"
export REGION="${REGION}"
export SERVICE_NAME="${SERVICE_NAME}"
export DRY_RUN="true"
./cloudrun_deploy.sh
```

## Real deployment
Build + push image first, then:
```bash
export IMAGE="us-docker.pkg.dev/YOUR_GCP_PROJECT/REPO/IMAGE:TAG"
export DRY_RUN="false"
./cloudrun_deploy.sh
```
