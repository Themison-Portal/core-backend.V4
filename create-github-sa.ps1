$ErrorActionPreference = "Stop"

$PROJECT_ID = "braided-visitor-484216-i0"
$SA_NAME = "github-actions-deploy2" # Added '2' just in case an old one exists
$SA_EMAIL = "$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"

Write-Host "Creating Service Account: $SA_NAME..."
gcloud iam service-accounts create $SA_NAME --display-name="GitHub Actions Cloud Run Deploy" --project=$PROJECT_ID

Write-Host "Granting Cloud Run Admin role..."
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$SA_EMAIL" --role="roles/run.admin" --condition=None

Write-Host "Granting Artifact Registry Writer role..."
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$SA_EMAIL" --role="roles/artifactregistry.writer" --condition=None

Write-Host "Granting Storage Admin role..."
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$SA_EMAIL" --role="roles/storage.objectAdmin" --condition=None

Write-Host "Granting Service Account User role..."
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$SA_EMAIL" --role="roles/iam.serviceAccountUser" --condition=None

Write-Host "Generating JSON Key file..."
gcloud iam service-accounts keys create gcp_sa_key.json --iam-account=$SA_EMAIL --project=$PROJECT_ID

Write-Host "Done! The key has been saved to gcp_sa_key.json"
