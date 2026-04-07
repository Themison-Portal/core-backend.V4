# Themison Backend - Google Cloud VM Setup Script (PowerShell)
# This script automates the deployment of Themison Backend on Google Cloud

param(
    [string]$ProjectId = "braided-visitor-484216-i0",
    [string]$Zone = "us-central1-a",
    [string]$Region = "us-central1",
    [string]$VmName = "themison-backend-vm",
    [string]$MachineType = "e2-medium",
    [string]$BootDiskSize = "50GB"
)

# Colors for output
function Write-Status {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor Yellow
}

function Write-Success {
    param([string]$Message)
    Write-Host "[SUCCESS] $Message" -ForegroundColor Green
}

function Write-Error-Custom {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

Write-Host "========================================" -ForegroundColor Green
Write-Host "Themison Backend - GCP Deployment" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green

# Check if gcloud is installed
try {
    $null = Get-Command gcloud -ErrorAction Stop
} catch {
    Write-Error-Custom "gcloud CLI is not installed. Please install it first."
    Write-Host "Visit: https://cloud.google.com/sdk/docs/install"
    exit 1
}

# Step 1: Authenticate and set project
Write-Status "Step 1: Authenticating with Google Cloud..."
gcloud auth login
gcloud config set project $ProjectId
Write-Success "Authenticated and project set to $ProjectId"

# Step 2: Enable required APIs
Write-Status "Step 2: Enabling required Google Cloud APIs..."
gcloud services enable compute.googleapis.com
gcloud services enable secretmanager.googleapis.com
gcloud services enable cloudresourcemanager.googleapis.com
Write-Success "APIs enabled"

# Step 3: Create firewall rules
Write-Status "Step 3: Creating firewall rules..."
try {
    gcloud compute firewall-rules create allow-themison-http `
        --project=$ProjectId `
        --allow=tcp:80,tcp:8000 `
        --target-tags=http-server `
        --description="Allow HTTP traffic to Themison backend" 2>$null
} catch {
    Write-Status "HTTP firewall rule already exists"
}

try {
    gcloud compute firewall-rules create allow-themison-https `
        --project=$ProjectId `
        --allow=tcp:443 `
        --target-tags=https-server `
        --description="Allow HTTPS traffic to Themison backend" 2>$null
} catch {
    Write-Status "HTTPS firewall rule already exists"
}
Write-Success "Firewall rules configured"

# Step 4: Create VM instance
Write-Status "Step 4: Creating Compute Engine VM..."
$startupScript = @"
#!/bin/bash
echo "Themison VM is ready"
docker --version
"@

try {
    gcloud compute instances create $VmName `
        --project=$ProjectId `
        --zone=$Zone `
        --machine-type=$MachineType `
        --image-family=cos-stable `
        --image-project=cos-cloud `
        --boot-disk-size=$BootDiskSize `
        --boot-disk-type=pd-balanced `
        --tags=http-server,https-server `
        --metadata=startup-script=$startupScript
    Write-Success "VM created: $VmName"
} catch {
    Write-Status "VM already exists or creation failed"
}

# Step 5: Get VM external IP
Write-Status "Step 5: Getting VM external IP..."
$ExternalIp = gcloud compute instances describe $VmName `
    --zone=$Zone `
    --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
Write-Success "VM External IP: $ExternalIp"

# Step 6: Create secrets (interactive)
Write-Status "Step 6: Setting up secrets in Secret Manager..."
Write-Host "Please provide the following credentials:" -ForegroundColor Yellow

$OpenAiKey = Read-Host "OpenAI API Key"
$OpenAiKey | gcloud secrets create openai-api-key --data-file=- 2>$null
if ($LASTEXITCODE -ne 0) {
    $OpenAiKey | gcloud secrets versions add openai-api-key --data-file=-
}

$AnthropicKey = Read-Host "Anthropic API Key"
$AnthropicKey | gcloud secrets create anthropic-api-key --data-file=- 2>$null
if ($LASTEXITCODE -ne 0) {
    $AnthropicKey | gcloud secrets versions add anthropic-api-key --data-file=-
}

$SupabaseUrl = Read-Host "Supabase URL"
$SupabaseUrl | gcloud secrets create supabase-url --data-file=- 2>$null
if ($LASTEXITCODE -ne 0) {
    $SupabaseUrl | gcloud secrets versions add supabase-url --data-file=-
}

$SupabaseServiceKey = Read-Host "Supabase Service Key"
$SupabaseServiceKey | gcloud secrets create supabase-service-key --data-file=- 2>$null
if ($LASTEXITCODE -ne 0) {
    $SupabaseServiceKey | gcloud secrets versions add supabase-service-key --data-file=-
}

$SupabaseAnonKey = Read-Host "Supabase Anon Key"
$SupabaseAnonKey | gcloud secrets create supabase-anon-key --data-file=- 2>$null
if ($LASTEXITCODE -ne 0) {
    $SupabaseAnonKey | gcloud secrets versions add supabase-anon-key --data-file=-
}

Write-Success "Secrets created in Secret Manager"

# Step 7: Grant VM access to secrets
Write-Status "Step 7: Granting VM access to secrets..."
$ProjectNumber = gcloud projects describe $ProjectId --format='value(projectNumber)'
$ServiceAccount = "$ProjectNumber-compute@developer.gserviceaccount.com"

$secrets = @("openai-api-key", "anthropic-api-key", "supabase-url", "supabase-service-key", "supabase-anon-key")
foreach ($secret in $secrets) {
    gcloud secrets add-iam-policy-binding $secret `
        --member="serviceAccount:$ServiceAccount" `
        --role="roles/secretmanager.secretAccessor" `
        --quiet
}
Write-Success "VM granted access to secrets"

# Step 8: Deploy application to VM
Write-Status "Step 8: Deploying application to VM..."
Write-Host "Connecting to VM and deploying application..." -ForegroundColor Yellow

$deployScript = @"
set -e

# Clone repository
if [ ! -d 'core-backend.V4' ]; then
    git clone https://github.com/Themison-Portal/core-backend.V4.git
else
    cd core-backend.V4 && git pull && cd ..
fi

cd core-backend.V4

# Create production .env file
cat > .env.production << 'ENVEOF'
OPENAI_API_KEY=`$(gcloud secrets versions access latest --secret='openai-api-key')
ANTHROPIC_API_KEY=`$(gcloud secrets versions access latest --secret='anthropic-api-key')
SUPABASE_URL=`$(gcloud secrets versions access latest --secret='supabase-url')
SUPABASE_SERVICE_KEY=`$(gcloud secrets versions access latest --secret='supabase-service-key')
SUPABASE_ANON_KEY=`$(gcloud secrets versions access latest --secret='supabase-anon-key')
SUPABASE_DB_URL=postgresql+asyncpg://postgres:postgres@db:5432/postgres
SUPABASE_DB_PASSWORD=postgres
REDIS_URL=redis://redis:6379
FRONTEND_URL=https://your-frontend-domain.com
ENVIRONMENT=production
ENVEOF

# Start Docker containers
docker-compose -f docker-compose.prod.yml up -d

echo 'Deployment complete!'
docker ps
"@

gcloud compute ssh $VmName --zone=$Zone --command=$deployScript

Write-Success "Application deployed to VM"

# Final summary
Write-Host "========================================" -ForegroundColor Green
Write-Host "Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "VM Name: " -NoNewline; Write-Host $VmName -ForegroundColor Yellow
Write-Host "External IP: " -NoNewline; Write-Host $ExternalIp -ForegroundColor Yellow
Write-Host "API URL: " -NoNewline; Write-Host "http://$ExternalIp:8000" -ForegroundColor Yellow
Write-Host "Health Check: " -NoNewline; Write-Host "http://$ExternalIp:8000/health" -ForegroundColor Yellow
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host "1. Configure your domain DNS to point to $ExternalIp"
Write-Host "2. Set up SSL certificate (Let's Encrypt recommended)"
Write-Host "3. Update FRONTEND_URL in .env.production"
Write-Host "4. Monitor logs: " -NoNewline
Write-Host "gcloud compute ssh $VmName --zone=$Zone --command='docker-compose logs -f'" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Green
