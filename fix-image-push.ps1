# Fix Image Push to Europe
# This script explicitly builds and pushes the Docker image to the European registry, then redeploys.

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
Write-Host "Fixing Docker Image Upload to Europe" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# 1. Verify Repository Exists
Write-Status "Verifying Artifact Registry repository..."
$repoCheck = gcloud artifacts repositories describe $RepoName --location=$Region --project=$ProjectId --format="value(name)" 2>$null

if (-not $repoCheck) {
    Write-Status "Repository not found. Creating..."
    gcloud artifacts repositories create $RepoName `
        --repository-format=docker `
        --location=$Region `
        --description="European Docker Repository" `
        --project=$ProjectId
    Write-Success "Repository created."
}
else {
    Write-Success "Repository '$RepoName' found."
}

# 2. Configure Docker Auth
Write-Status "Configuring Docker authentication..."
gcloud auth configure-docker "${Region}-docker.pkg.dev" --quiet

# 3. Build & Push
$EuImageUri = "${Region}-docker.pkg.dev/${ProjectId}/${RepoName}/${ImageName}:${Tag}"

Write-Status "Building Docker image (this may take a few minutes)..."
# Using --platform linux/amd64 to ensure compatibility with Cloud Run
docker build --platform linux/amd64 -t $EuImageUri .

if ($LASTEXITCODE -eq 0) {
    Write-Success "Build successful."
    
    Write-Status "Pushing image to European Registry..."
    docker push $EuImageUri
    
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Image pushed successfully to: $EuImageUri"
        
        # 4. Redeploy Service
        Write-Status "Redeploying Cloud Run service with new image..."
        gcloud run services update core-backend-eu `
            --image $EuImageUri `
            --region $Region `
            --project $ProjectId `
            --quiet
            
        Write-Success "Deployment updated. Checking status..."
        Start-Sleep -Seconds 10
        gcloud run services describe core-backend-eu --region $Region --format="value(status.url, status.conditions[0].status)"
    }
    else {
        Write-Error-Custom "Failed to push docker image. Check your internet connection or docker login."
    }
}
else {
    Write-Error-Custom "Docker build failed."
}
