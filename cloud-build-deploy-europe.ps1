# Cloud Build Deployment to Europe
# Uses Google Cloud Build to build and push the image, bypassing local Docker issues.

param(
    [string]$ProjectId = "braided-visitor-484216-i0",
    [string]$Region = "europe-west1",
    [string]$RepoName = "themison-repo-eu",
    [string]$ImageName = "core-backend",
    [string]$Tag = "latest"
)

# Colors
function Write-Status { param([string]$Msg) Write-Host "[INFO] $Msg" -ForegroundColor Yellow }
function Write-Success { param([string]$Msg) Write-Host "[SUCCESS] $Msg" -ForegroundColor Green }
function Write-Error-Custom { param([string]$Msg) Write-Host "[ERROR] $Msg" -ForegroundColor Red }

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Cloud Build Deployment to Europe" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# 1. Enable Cloud Build API
Write-Status "Enabling Cloud Build API..."
gcloud services enable cloudbuild.googleapis.com

# 2. Submit Build to Cloud Build
$EuImageUri = "${Region}-docker.pkg.dev/${ProjectId}/${RepoName}/${ImageName}:${Tag}"

Write-Status "Submitting build to Google Cloud Build..."
Write-Status "Target Image: $EuImageUri"

# This command sends the current directory to Cloud Build
# It builds the image and pushes it directly to the Artifact Registry
gcloud builds submit --tag $EuImageUri .

if ($LASTEXITCODE -eq 0) {
    Write-Success "Cloud Build successful! Image pushed to registry."
    
    # 3. Redeploy Service
    Write-Status "Updating Cloud Run service with new image..."
    gcloud run services update core-backend-eu `
        --image $EuImageUri `
        --region $Region `
        --project $ProjectId `
        --quiet
        
    Write-Success "Service updated! Checking status..."
    Start-Sleep -Seconds 10
    gcloud run services describe core-backend-eu --region $Region --format="value(status.url, status.conditions[0].status)"
}
else {
    Write-Error-Custom "Cloud Build failed. Check the logs above."
}
