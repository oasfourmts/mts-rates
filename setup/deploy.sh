#!/usr/bin/env bash
# MTS Rates - deploy the web app (run in Cloud Shell from the repo folder, signed in with @mtslogistics.net)
#
#   git clone https://github.com/oasfourmts/mts-rates.git && cd mts-rates
#   ADMIN_EMAILS="you@mtslogistics.net" bash setup/deploy.sh
#
# Safe to run again: every step checks what exists first. A rerun builds the current code and redeploys it.
# The image is built in Cloud Build by default; BUILD_WITH=docker builds it here with Docker instead.
# Creates: Artifact Registry repo, Cloud SQL Postgres 16 (+ database and owner role), two generated database
# passwords in Secret Manager, a service account for the web app, the Cloud Run jobs mts-rates-migrate and
# mts-rates-import, and the Cloud Run service mts-rates behind IAP (only @mtslogistics.net can open it).
# Needs setup/setup_access.sh to have run first (project, billing, rates-agent).
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-mts-rates}"
REGION="europe-west1"
COMPANY_DOMAIN="mtslogistics.net"
SERVICE="mts-rates"
INSTANCE="mts-rates-db"
DB_TIER="${DB_TIER:-db-f1-micro}"          # smallest shared-core tier; resize later with gcloud sql instances patch
DB_NAME="mts"
OWNER_ROLE="rates_owner"                   # used only by the migrate and import jobs
APP_ROLE="rates_app"                       # the web app: read everything, write exchange rates and audit log
AGENT_SA="rates-agent@$PROJECT_ID.iam.gserviceaccount.com"
WEB_SA_NAME="rates-web"
WEB_SA="$WEB_SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
REPO="$REGION-docker.pkg.dev/$PROJECT_ID/mts-rates"
IMPORT_MONTH="${IMPORT_MONTH:-$(TZ=Africa/Cairo date +%Y-%m)}"   # SKIP_IMPORT=1 to skip the sheet import

step() { echo; echo "==> $*"; }
# A service account created a moment ago can take up to a minute to be usable in IAM bindings
# (4 Nov 2026: "Service account ... does not exist" right after it was created). Retry until it is.
retry() {
  local attempt
  for attempt in 1 2 3 4 5 6; do
    if "$@" >/dev/null 2>&1; then return 0; fi
    echo "  not ready yet (try $attempt of 6), waiting $((attempt * 5))s..."
    sleep $((attempt * 5))
  done
  "$@"   # last try, showing the error
}
fail() { echo "ERROR: $*" >&2; exit 1; }

cd "$(dirname "$0")/.."
[[ -f Dockerfile && -d db/migrations ]] || fail "run this from the mts-rates repo folder"

# --- 0. Checks ----------------------------------------------------------------------------------------------
ACTIVE=$(gcloud config get-value account 2>/dev/null)
[[ "$ACTIVE" == *@"$COMPANY_DOMAIN" ]] || fail "active account is '$ACTIVE', expected @$COMPANY_DOMAIN. Run: gcloud auth login"
gcloud config set project "$PROJECT_ID" >/dev/null
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
gcloud iam service-accounts describe "$AGENT_SA" >/dev/null 2>&1 || fail "$AGENT_SA not found. Run setup/setup_access.sh first."
BUILD_WITH="${BUILD_WITH:-cloudbuild}"   # cloudbuild (default): built inside Google Cloud; docker: built here
[[ "$BUILD_WITH" == cloudbuild || "$BUILD_WITH" == docker ]] || fail "BUILD_WITH must be cloudbuild or docker"
[[ "$BUILD_WITH" == cloudbuild ]] || command -v docker >/dev/null || fail "docker is not available here"

if [[ -z "${ADMIN_EMAILS:-}" ]]; then
  read -rp "Who may enter exchange rates? Comma-separated @$COMPANY_DOMAIN emails [$ACTIVE]: " ADMIN_EMAILS
  ADMIN_EMAILS="${ADMIN_EMAILS:-$ACTIVE}"
fi
ADMIN_EMAILS=$(echo "$ADMIN_EMAILS" | tr 'A-Z' 'a-z' | tr -d ' ')
IFS=',' read -ra _admins <<< "$ADMIN_EMAILS"
for a in "${_admins[@]}"; do [[ "$a" == *@"$COMPANY_DOMAIN" ]] || fail "admin '$a' is not an @$COMPANY_DOMAIN address"; done

VERSION=$(git rev-parse --short HEAD 2>/dev/null || echo manual)
[[ -z "$(git status --porcelain 2>/dev/null)" ]] || VERSION="$VERSION-local$(date +%H%M)"
IMAGE="$REPO/app:$VERSION"
echo "Project $PROJECT_ID ($PROJECT_NUMBER), running as $ACTIVE"
echo "Code version $VERSION ($(git branch --show-current 2>/dev/null || echo '?'))"
echo "Admins: $ADMIN_EMAILS"

# --- 1. APIs -------------------------------------------------------------------------------------------------
step "Enabling APIs"
gcloud services enable run.googleapis.com sqladmin.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com iap.googleapis.com sheets.googleapis.com iamcredentials.googleapis.com \
  cloudbuild.googleapis.com

# --- 2. Container image -------------------------------------------------------------------------------------
step "Building $IMAGE ($BUILD_WITH)"
if ! gcloud artifacts repositories describe mts-rates --location="$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create mts-rates --repository-format=docker --location="$REGION" \
    --description="MTS Rates images"
fi
if gcloud artifacts docker images describe "$IMAGE" >/dev/null 2>&1; then
  echo "image $VERSION is already in the registry; not rebuilding"
elif [[ "$BUILD_WITH" == cloudbuild ]]; then
  # Built inside Google Cloud, so nothing has to travel from Cloud Shell to the registry (on 4 Nov 2026 Cloud
  # Shell's pushes were refused). Runs as rates-build, which can only push images, read the uploaded source
  # and write build logs.
  BUILD_SA="rates-build@$PROJECT_ID.iam.gserviceaccount.com"
  gcloud iam service-accounts describe "$BUILD_SA" >/dev/null 2>&1 || \
    gcloud iam service-accounts create rates-build --display-name="MTS Rates image builds"
  for ROLE in roles/logging.logWriter roles/storage.objectViewer; do
    retry gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$BUILD_SA" \
      --role="$ROLE" --condition=None
  done
  retry gcloud artifacts repositories add-iam-policy-binding mts-rates --location="$REGION" \
    --member="serviceAccount:$BUILD_SA" --role=roles/artifactregistry.writer
  echo "uploading the code and building in Cloud Build (about 3 minutes)..."
  if ! gcloud builds submit . --region="$REGION" --config=cloudbuild.yaml --substitutions="_IMAGE=$IMAGE" \
      --service-account="projects/$PROJECT_ID/serviceAccounts/$BUILD_SA" \
      --default-buckets-behavior=regional-user-owned-bucket --suppress-logs; then
    fail "the build failed. Logs: gcloud builds list --region=$REGION --limit=1, then gcloud builds log <ID> --region=$REGION"
  fi
else
  gcloud auth configure-docker "$REGION-docker.pkg.dev" --quiet >/dev/null
  docker build -t "$IMAGE" .
  # Cloud Shell's connection to the registry sometimes drops; retry before giving up.
  for attempt in 1 2 3 4 5; do
    if docker push "$IMAGE"; then break; fi
    [[ $attempt == 5 ]] && fail "could not upload the image after 5 tries. Run again without BUILD_WITH=docker to build in Cloud Build."
    echo "upload failed (try $attempt of 5), retrying in $((attempt * 10))s..."
    sleep $((attempt * 10))
  done
fi

# --- 3. Database passwords (generated once, never printed) ----------------------------------------------------
step "Database passwords in Secret Manager"
for SECRET in db-owner-password db-app-password; do
  if ! gcloud secrets describe "$SECRET" >/dev/null 2>&1; then
    openssl rand -base64 36 | tr -d '\n/+=' | \
      gcloud secrets create "$SECRET" --replication-policy=automatic --data-file=- >/dev/null
    echo "created $SECRET"
  else
    echo "$SECRET exists"
  fi
done

# --- 4. Cloud SQL ------------------------------------------------------------------------------------------
step "Cloud SQL instance $INSTANCE (first creation takes about 10 minutes)"
if ! gcloud sql instances describe "$INSTANCE" >/dev/null 2>&1; then
  gcloud sql instances create "$INSTANCE" \
    --database-version=POSTGRES_16 --edition=ENTERPRISE --tier="$DB_TIER" --region="$REGION" \
    --availability-type=ZONAL --storage-type=SSD --storage-size=10GB --storage-auto-increase \
    --backup-start-time=23:00 --enable-point-in-time-recovery --retained-backups-count=14 \
    --maintenance-window-day=FRI --maintenance-window-hour=1 --deletion-protection
fi
CONN=$(gcloud sql instances describe "$INSTANCE" --format='value(connectionName)')
gcloud sql databases describe "$DB_NAME" --instance="$INSTANCE" >/dev/null 2>&1 || \
  gcloud sql databases create "$DB_NAME" --instance="$INSTANCE"
OWNER_PW=$(gcloud secrets versions access latest --secret=db-owner-password)
if gcloud sql users list --instance="$INSTANCE" --format='value(name)' | grep -qx "$OWNER_ROLE"; then
  gcloud sql users set-password "$OWNER_ROLE" --instance="$INSTANCE" --password="$OWNER_PW" >/dev/null
else
  gcloud sql users create "$OWNER_ROLE" --instance="$INSTANCE" --password="$OWNER_PW" >/dev/null
fi
unset OWNER_PW
echo "connection $CONN, owner role $OWNER_ROLE"

# --- 5. Service accounts --------------------------------------------------------------------------------------
step "Service accounts"
gcloud iam service-accounts describe "$WEB_SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$WEB_SA_NAME" --display-name="MTS Rates web app"
retry gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$WEB_SA" \
  --role=roles/cloudsql.client --condition=None
# The web app can read only its own password; rates-agent (the jobs) already reads secrets and uses Cloud SQL.
retry gcloud secrets add-iam-policy-binding db-app-password --member="serviceAccount:$WEB_SA" \
  --role=roles/secretmanager.secretAccessor
echo "web app runs as $WEB_SA; jobs run as $AGENT_SA"

JOB_FLAGS=(--image="$IMAGE" --region="$REGION" --service-account="$AGENT_SA" --set-cloudsql-instances="$CONN"
  --set-env-vars="DB_HOST=/cloudsql/$CONN,DB_NAME=$DB_NAME,DB_USER=$OWNER_ROLE" --max-retries=0 --task-timeout=15m
  --command=python)

# --- 6. Migrations ----------------------------------------------------------------------------------------------
step "Applying database migrations"
gcloud run jobs deploy mts-rates-migrate "${JOB_FLAGS[@]}" --args=-m,app.db.migrate \
  --set-secrets=DB_PASSWORD=db-owner-password:latest,APP_DB_PASSWORD=db-app-password:latest >/dev/null
gcloud run jobs execute mts-rates-migrate --region="$REGION" --wait || \
  fail "migrations failed: see Cloud Run > Jobs > mts-rates-migrate > Logs"

# --- 7. Web app behind IAP ---------------------------------------------------------------------------------------
step "Deploying $SERVICE"
AUDIENCE="/projects/$PROJECT_NUMBER/locations/$REGION/services/$SERVICE"
gcloud run deploy "$SERVICE" --image="$IMAGE" --region="$REGION" --service-account="$WEB_SA" \
  --no-allow-unauthenticated --iap --set-cloudsql-instances="$CONN" \
  --set-env-vars="^|^AUTH_MODE=iap|IAP_AUDIENCE=$AUDIENCE|ALLOWED_DOMAIN=$COMPANY_DOMAIN|ADMIN_EMAILS=$ADMIN_EMAILS|DB_HOST=/cloudsql/$CONN|DB_NAME=$DB_NAME|DB_USER=$APP_ROLE" \
  --set-secrets=DB_PASSWORD=db-app-password:latest \
  --cpu=1 --memory=512Mi --min-instances=0 --max-instances=4 --concurrency=40 --timeout=60

step "IAP: only @$COMPANY_DOMAIN can open the app"
IAP_AGENT=$(gcloud beta services identity create --service=iap.googleapis.com --format='value(email)' 2>/dev/null || true)
IAP_AGENT="${IAP_AGENT:-service-$PROJECT_NUMBER@gcp-sa-iap.iam.gserviceaccount.com}"
gcloud run services add-iam-policy-binding "$SERVICE" --region="$REGION" \
  --member="serviceAccount:$IAP_AGENT" --role=roles/run.invoker >/dev/null
gcloud iap web add-iam-policy-binding --member="domain:$COMPANY_DOMAIN" \
  --role=roles/iap.httpsResourceAccessor --region="$REGION" --resource-type=cloud-run --service="$SERVICE" >/dev/null
URL=$(gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')

# --- 8. Sheet import --------------------------------------------------------------------------------------------
gcloud run jobs deploy mts-rates-import "${JOB_FLAGS[@]}" --args=-m,app.db.import_sheets,--month,"$IMPORT_MONTH" \
  --set-secrets=DB_PASSWORD=db-owner-password:latest >/dev/null
IMPORT_NOTE="skipped (SKIP_IMPORT=1)"
if [[ "${SKIP_IMPORT:-0}" != "1" ]]; then
  step "Importing the $IMPORT_MONTH rate sheets (already-imported tabs are skipped)"
  if gcloud run jobs execute mts-rates-import --region="$REGION" --wait; then
    IMPORT_NOTE="done; see the job's logs for row counts and import issues"
  else
    IMPORT_NOTE="FAILED. Most likely the sheets are not shared with $AGENT_SA (Viewer). Share them, then run:
    gcloud run jobs execute mts-rates-import --region=$REGION --wait"
  fi
fi

echo
echo "=================================================================="
echo "App:      $URL"
echo "Version:  $VERSION"
echo "Import:   $IMPORT_NOTE"
echo "Admins:   $ADMIN_EMAILS (may enter exchange rates)"
echo "=================================================================="
echo "Open the app signed in with an @$COMPANY_DOMAIN account. Paste this summary back into the chat."
