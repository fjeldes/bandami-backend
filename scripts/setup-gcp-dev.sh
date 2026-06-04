#!/bin/bash
# ================================================================
# GCP Dev Environment Setup — Automated
# Reads all variables from backend/.env.dev
# Creates: Artifact Registry, Secret Manager, IAM, Cloud Build trigger, Cloud Scheduler
#
# Usage:
#   chmod +x scripts/setup-gcp-dev.sh
#   ./scripts/setup-gcp-dev.sh
# ================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env.dev"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found."
    echo "Create it first: cp backend/.env.example backend/.env.dev && edit with real values"
    exit 1
fi

# Load env vars safely (handle all characters)
while IFS='=' read -r key value; do
    # Skip comments and empty lines
    [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
    # Remove inline comments, trim whitespace
    value=$(echo "$value" | sed 's/[[:space:]]*#.*//' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    # Remove quotes if present
    value=$(echo "$value" | sed 's/^["'"'"']\(.*\)["'"'"']$/\1/')
    export "$key=$value"
done < "$ENV_FILE"

# GCP config
PROJECT_ID="bandami-dev"
REGION="us-central1"
SERVICE="dev-ielts-backend"
REPO="dev-backend"

# GitHub config (for Cloud Build trigger)
GH_OWNER="${GH_OWNER:-felipejeldesramirez}"
GH_REPO="${GH_REPO:-ielts-saas}"

echo "==========================================="
echo "  GCP Dev Environment Setup"
echo "  Project: $PROJECT_ID"
echo "  Region:  $REGION"
echo "  Service: $SERVICE"
echo "==========================================="

gcloud config set project "$PROJECT_ID" 2>/dev/null || true

# ---- 1. Enable APIs ----
echo ""
echo "── Step 1/5: Enabling APIs ──"
gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    cloudscheduler.googleapis.com \
    secretmanager.googleapis.com \
    --quiet
echo "  Done."

# ---- 2. Artifact Registry ----
echo ""
echo "── Step 2/5: Creating Artifact Registry ──"
gcloud artifacts repositories create "$REPO" \
    --repository-format=docker \
    --location="$REGION" \
    --description="Docker images for dev backend" \
    --quiet 2>/dev/null && echo "  Created." || echo "  Already exists."

# ---- 3. Secret Manager ----
echo ""
echo "── Step 3/5: Creating secrets in Secret Manager ──"

create_secret() {
    local name="$1"
    local value="$2"
    if [ -z "$value" ]; then
        echo "  SKIP $name (empty)"
        return
    fi
    if gcloud secrets describe "$name" --project="$PROJECT_ID" --quiet &>/dev/null; then
        echo -n "$value" | gcloud secrets versions add "$name" --data-file=- --project="$PROJECT_ID" --quiet
        echo "  OK   $name (new version added)"
    else
        echo -n "$value" | gcloud secrets create "$name" \
            --data-file=- \
            --replication-policy=automatic \
            --project="$PROJECT_ID" \
            --quiet
        echo "  OK   $name (created)"
    fi
}

echo "  Secrets marked OK were loaded from .env.dev"
create_secret "DEV_DATABASE_URL"          "$DATABASE_URL"
create_secret "DEV_OPENAI_API_KEY"        "$OPENAI_API_KEY"
create_secret "DEV_GEMINI_API_KEY"        "$GEMINI_API_KEY"
create_secret "DEV_JWT_SECRET_KEY"        "$JWT_SECRET_KEY"
create_secret "DEV_BREVO_API_KEY"         "$BREVO_API_KEY"
create_secret "DEV_STRIPE_SECRET_KEY"     "$STRIPE_SECRET_KEY"
create_secret "DEV_STRIPE_WEBHOOK_SECRET" "$STRIPE_WEBHOOK_SECRET"
create_secret "DEV_GOOGLE_CLIENT_ID"      "$GOOGLE_CLIENT_ID"
create_secret "DEV_GOOGLE_CLIENT_SECRET"  "$GOOGLE_CLIENT_SECRET"

# ---- 4. IAM for Cloud Build ----
echo ""
echo "── Step 4/5: Setting IAM permissions ──"

PROJECT_NUM=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
CB_SA="${PROJECT_NUM}@cloudbuild.gserviceaccount.com"

SECRETS=(
    "DEV_DATABASE_URL" "DEV_OPENAI_API_KEY" "DEV_GEMINI_API_KEY"
    "DEV_JWT_SECRET_KEY" "DEV_BREVO_API_KEY" "DEV_STRIPE_SECRET_KEY"
    "DEV_STRIPE_WEBHOOK_SECRET" "DEV_GOOGLE_CLIENT_ID" "DEV_GOOGLE_CLIENT_SECRET"
)

for secret in "${SECRETS[@]}"; do
    gcloud secrets add-iam-policy-binding "$secret" \
        --member="serviceAccount:${CB_SA}" \
        --role="roles/secretmanager.secretAccessor" \
        --project="$PROJECT_ID" \
        --quiet 2>/dev/null && echo "  IAM  $secret → Cloud Build" || echo "  SKIP $secret (already set or missing)"
done

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${CB_SA}" \
    --role="roles/run.admin" \
    --project="$PROJECT_ID" \
    --quiet 2>/dev/null && echo "  IAM  run.admin → Cloud Build" || true

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${CB_SA}" \
    --role="roles/iam.serviceAccountUser" \
    --project="$PROJECT_ID" \
    --quiet 2>/dev/null && echo "  IAM  iam.serviceAccountUser → Cloud Build" || true

# ---- 5. Cloud Build Trigger ----
echo ""
echo "── Step 5/5: Creating Cloud Build trigger ──"

# Delete old trigger if it exists (idempotent)
gcloud builds triggers delete "dev-backend-deploy" \
    --region="$REGION" --project="$PROJECT_ID" --quiet 2>/dev/null || true

gcloud builds triggers create github \
    --name="dev-backend-deploy" \
    --repo-owner="$GH_OWNER" \
    --repo-name="$GH_REPO" \
    --branch-pattern="develop" \
    --build-config="backend/cloudbuild.yaml" \
    --region="$REGION" \
    --project="$PROJECT_ID" \
    --quiet 2>/dev/null && echo "  Trigger created: develop → Cloud Build" || echo "  Trigger already exists."

# ---- 6. Keep-Alive Scheduler ----
echo ""
echo "── Creating Keep-Alive Scheduler ──"

SERVICE_URL=$(gcloud run services describe "$SERVICE" \
    --region="$REGION" --project="$PROJECT_ID" \
    --format="value(status.url)" 2>/dev/null || echo "")

if [ -n "$SERVICE_URL" ]; then
    gcloud scheduler jobs delete "dev-keep-alive" \
        --location="$REGION" --project="$PROJECT_ID" --quiet 2>/dev/null || true
    gcloud scheduler jobs create http "dev-keep-alive" \
        --schedule="*/3 * * * *" \
        --uri="${SERVICE_URL}/api/health" \
        --http-method=GET \
        --location="$REGION" \
        --attempt-deadline=30s \
        --project="$PROJECT_ID" \
        --quiet && echo "  Keep-alive: ${SERVICE_URL}/api/health every 3 min" || echo "  Scheduler creation failed (will work after first deploy)"
else
    echo "  Service not deployed yet. Scheduler will be created after first deploy."
    echo "  Run this script again after: git push origin develop"
fi

echo ""
echo "==========================================="
echo "  Setup complete!"
echo ""
echo "  Artifact Registry: $REGION-docker.pkg.dev/$PROJECT_ID/$REPO"
echo "  Cloud Run service: $SERVICE"
echo "  Secrets:           ${#SECRETS[@]} created in Secret Manager"
echo ""
echo "  Next steps:"
echo "    1. git push origin develop"
echo "    2. Wait 3-5 min for Cloud Build"
echo "    3. curl https://dev-api.bandami.com/api/health"
echo ""
echo "  Don't forget DNS: dev-api.bandami.com → CNAME → ghs.googlehosted.com"
echo "  Then: Cloud Run → Edit → Domain Mappings → Add dev-api.bandami.com"
echo "==========================================="
