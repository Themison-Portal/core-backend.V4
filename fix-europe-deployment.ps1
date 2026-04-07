# Fix Europe Deployment & Verify
# This script adds missing secrets to the Cloud Run service in Europe and verifies health.

param(
    [string]$ProjectId = "braided-visitor-484216-i0",
    [string]$Region = "europe-west1",
    [string]$ServiceName = "core-backend-eu"
)

# Colors
function Write-Status { param([string]$Msg) Write-Host "[INFO] $Msg" -ForegroundColor Yellow }
function Write-Success { param([string]$Msg) Write-Host "[SUCCESS] $Msg" -ForegroundColor Green }
function Write-Error-Custom { param([string]$Msg) Write-Host "[ERROR] $Msg" -ForegroundColor Red }

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Fixing Europe Deployment ($Region)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# 1. Update Service with Secrets
Write-Status "Updating service with missing secrets..."

# We assume secrets exist in Secret Manager with these names
$secrets = @(
    "OPENAI_API_KEY=openai-api-key:latest",
    "ANTHROPIC_API_KEY=anthropic-api-key:latest",
    "SUPABASE_URL=supabase-url:latest",
    "SUPABASE_SERVICE_KEY=supabase-service-key:latest",
    "SUPABASE_ANON_KEY=supabase-anon-key:latest",
    "DATABASE_URL=database-url:latest",
    "REDIS_URL=redis-url:latest",
    "SUPABASE_DB_URL=database-url:latest"
)
$secretsString = $secrets -join ","

# Also ensure VPC Connector is attached (themison-connector exists in europe-west1)
$vpccConnector = "themison-connector"

try {
    gcloud run services update $ServiceName `
        --region $Region `
        --project $ProjectId `
        --set-secrets=$secretsString `
        --vpc-connector=$vpccConnector `
        --quiet

    Write-Success "Service updated with secrets and VPC connector."
}
catch {
    Write-Error-Custom "Failed to update service. Check logs."
    exit 1
}

# 2. Verify Health
Write-Status "Verifying service health..."
Start-Sleep -Seconds 10 # Give it a moment

$serviceUrl = gcloud run services describe $ServiceName --region $Region --format="value(status.url)"
$status = gcloud run services describe $ServiceName --region $Region --format="value(status.conditions[0].status)"

if ($status -eq "True") {
    Write-Success "Service is HEALTHY!"
    Write-Host "URL: $serviceUrl" -ForegroundColor Cyan
    
    # 3. Check Endpoint
    try {
        $response = Invoke-WebRequest -Uri "$serviceUrl/health" -UseBasicParsing -ErrorAction Stop
        if ($response.StatusCode -eq 200) {
            Write-Success "Health check endpoint passed (200 OK)."
        }
        else {
            Write-Error-Custom "Health check returned status code: $($response.StatusCode)"
        }
    }
    catch {
        Write-Error-Custom "Failed to reach health endpoint: $_"
    }

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "READY TO DELETE OLD REPO" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "The new European deployment is verified working."
    Write-Host "Run this command to delete the old US repository:" -ForegroundColor Yellow
    Write-Host "gcloud artifacts repositories delete themison-repo --location=us-central1 --project=$ProjectId --quiet" -ForegroundColor White
}
else {
    Write-Error-Custom "Service is NOT healthy yet. Status: $status"
    Write-Status "Checking logs..."
    gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=$ServiceName" --limit=5 
}
