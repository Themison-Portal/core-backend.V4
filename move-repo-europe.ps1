# Themison Backend - Move Repo (Artifact Registry) & Service to Europe
# This script creates a new Artifact Registry in EuropeDist, builds the image, and updates the Cloud Run service.

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

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Moving Repo & Service to Europe ($Region)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# 1. Enable Artifact Registry
Write-Status "Enabling Artifact Registry API..."
gcloud services enable artifactregistry.googleapis.com

# 2. Create European Repository
Write-Status "Creating Artifact Registry repository in $Region..."
# Check if repo exists
$repoCheck = gcloud artifacts repositories describe $RepoName --location=$Region --project=$ProjectId 2>$null
if (-not $repoCheck) {
    gcloud artifacts repositories create $RepoName `
        --repository-format=docker `
        --location=$Region `
        --description="European Docker Repository" `
        --project=$ProjectId
    Write-Success "Repository '$RepoName' created in $Region"
}
else {
    Write-Status "Repository '$RepoName' already exists in $Region"
}

# 3. Configure Docker
Write-Status "Configuring Docker authentication..."
gcloud auth configure-docker "${Region}-docker.pkg.dev" --quiet

# 4. Build and Push Image
$EuImageUri = "${Region}-docker.pkg.dev/${ProjectId}/${RepoName}/${ImageName}:${Tag}"

Write-Status "Building Docker image..."
docker build -t $EuImageUri .

Write-Status "Pushing image to European Registry..."
docker push $EuImageUri

Write-Success "Image pushed to: $EuImageUri"

# 5. Generate service-europe.yaml
Write-Status "Generating service-europe.yaml..."

# Read existing service_trials.yaml for config (fallback if service.yaml is unreadable)
# We assume standard Cloud Run config but update image and location

$ServiceYamlContent = @"
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: core-backend-eu
  labels:
    cloud.googleapis.com/location: $Region
  annotations:
    run.googleapis.com/ingress: all
spec:
  template:
    metadata:
      annotations:
        autoscaling.knative.dev/maxScale: '10'
        run.googleapis.com/vpc-access-connector: themison-connector-eu # Update if strictly needed, or remove if public
        run.googleapis.com/vpc-access-egress: all-traffic
    spec:
      containerConcurrency: 80
      timeoutSeconds: 300
      containers:
      - image: $EuImageUri
        ports:
        - containerPort: 8080
          name: http1
        resources:
          limits:
            cpu: 1000m
            memory: 1Gi
        env:
        - name: ENVIRONMENT
          value: production
        - name: SUPABASE_DB_URL
          value: postgresql+asyncpg://postgres:postgres@10.132.0.2:5432/postgres # Internal IP of EU DB
        - name: REDIS_URL
          value: redis://10.132.0.2:6379 # Internal IP of EU Redis
        - name: FRONTEND_URL
          value: "*"
        # Secrets should be mounted or referenced
        # For simplicity in this script, we assume secrets are managed via Secret Manager references if possible, 
        # or you can add them manually. 
        # Here we just put placeholders or references if known.
"@

$ServiceYamlContent | Out-File -FilePath "service-europe.yaml" -Encoding UTF8
Write-Success "Generated service-europe.yaml"

Write-Status "Deploying to Cloud Run (Europe)..."
gcloud run deploy core-backend-eu `
    --image $EuImageUri `
    --region $Region `
    --project $ProjectId `
    --allow-unauthenticated `
    --port 8080 `
    --set-env-vars="SUPABASE_DB_URL=postgresql+asyncpg://postgres:postgres@10.132.0.2:5432/postgres,REDIS_URL=redis://10.132.0.2:6379"

# Note: We are setting env vars directly to override defaults. 
# Secrets need to be re-attached if they were used.

Write-Success "Deployment initiated!"
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host "1. Verify the service URL."
Write-Host "2. Check if secrets (OPENAI_API_KEY, etc.) need to be re-added via console or command line."
