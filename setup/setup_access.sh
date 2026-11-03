#!/usr/bin/env bash
# MTS Rate Management - access setup (run in Cloud Shell as a project owner, signed in with @mtslogistics.net)
# Same pattern as the Email KPI project: keyless service account + Gmail domain-wide delegation.
# Creates no databases and no Cloud Run services yet - only the project, APIs and the agent's identity.
set -euo pipefail

PROJECT_ID="mts-rates"                    # change if the ID is taken
BILLING_ACCOUNT="01F0A0-F15039-00BB26"    # same billing account as mts-email-kpi
ORG_ID="36561736310"                      # mtslogistics.net
REGION="europe-west1"
SA_NAME="rates-agent"
COMPANY_DOMAIN="mtslogistics.net"

ACTIVE=$(gcloud config get-value account 2>/dev/null)
if [[ "$ACTIVE" != *@"$COMPANY_DOMAIN" ]]; then
  echo "ERROR: active account is '$ACTIVE', expected @$COMPANY_DOMAIN. Run: gcloud auth login"; exit 1
fi
echo "Running as $ACTIVE in org $ORG_ID"

# 1. Project + billing (skips creation if it already exists)
if ! gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud projects create "$PROJECT_ID" --name="MTS Rate Management" --organization="$ORG_ID"
fi
gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT"
gcloud config set project "$PROJECT_ID"

# 2. APIs for Phases 1-4
gcloud services enable \
  gmail.googleapis.com sheets.googleapis.com drive.googleapis.com \
  run.googleapis.com sqladmin.googleapis.com pubsub.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com \
  cloudscheduler.googleapis.com secretmanager.googleapis.com \
  iamcredentials.googleapis.com

# 3. Agent service account (no key file - keyless delegation)
SA_EMAIL="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
if ! gcloud iam service-accounts describe "$SA_EMAIL" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" --display-name="Rates agent"
fi
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --member="serviceAccount:$SA_EMAIL" --role="roles/iam.serviceAccountTokenCreator"
for ROLE in roles/cloudsql.client roles/secretmanager.secretAccessor roles/pubsub.editor; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA_EMAIL" --role="$ROLE" --condition=None >/dev/null
done

# 4. Gmail must be able to publish new-mail notifications to Pub/Sub
if ! gcloud pubsub topics describe gmail-inbound >/dev/null 2>&1; then
  gcloud pubsub topics create gmail-inbound   # errors are shown, not hidden
fi
gcloud pubsub topics add-iam-policy-binding gmail-inbound \
  --member="serviceAccount:gmail-api-push@system.gserviceaccount.com" --role="roles/pubsub.publisher" >/dev/null

# 5. Values you need next
echo
echo "=== Service account email (share the rate sheets with this address) ==="
echo "$SA_EMAIL"
echo "=== Client ID for Admin Console > Domain-wide delegation ==="
gcloud iam service-accounts describe "$SA_EMAIL" --format='value(oauth2ClientId)'
echo
echo "Done. Paste the two values above back into the chat."
