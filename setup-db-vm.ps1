# Deploy PostgreSQL on Google Cloud VM (Automated Script)

param(
    [string]$ProjectId = "braided-visitor-484216-i0", # Confirmed Project ID
    [string]$Zone = "us-central1-a",
    [string]$VmName = "themison-db-vm",
    [string]$SqlFile = "schema.sql", # Default SQL file to import
    [string]$MachineType = "e2-medium",
    [string]$BootDiskSize = "50GB"
)

# Function to check command success
function Test-CommandSuccess {
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Error occurred. Exiting." -ForegroundColor Red
        exit 1
    }
}

Write-Host "========================================" -ForegroundColor Green
Write-Host "Deploying PostgreSQL to GCP VM: $VmName"
Write-Host "Project: $ProjectId"
Write-Host "========================================" -ForegroundColor Green

# 1. Authenticate & Set Project
Write-Host "[1/6] Setting project..." -ForegroundColor Yellow
gcloud config set project $ProjectId
Test-CommandSuccess

# 2. Check if VM exists, create if not
Write-Host "[2/6] Checking VM status..." -ForegroundColor Yellow
$vmStatus = gcloud compute instances describe $VmName --zone=$Zone --format="value(status)" 2>$null

if ($vmStatus -eq "RUNNING") {
    Write-Host "VM $VmName is already running." -ForegroundColor Green
}
else {
    Write-Host "Creating VM $VmName..." -ForegroundColor Yellow
    # Create VM with Docker pre-installed (COS image)
    gcloud compute instances create $VmName `
        --project=$ProjectId `
        --zone=$Zone `
        --machine-type=$MachineType `
        --image-family=cos-stable `
        --image-project=cos-cloud `
        --boot-disk-size=$BootDiskSize `
        --boot-disk-type=pd-balanced `
        --tags="db-server,ssh-server"
    Test-CommandSuccess
    
    Write-Host "Waiting for VM to initialize..." -ForegroundColor Yellow
    Start-Sleep -Seconds 30
}

# 3. Create Firewall Rules (Allow SSH only, DB internal/localhost)
Write-Host "[3/6] Configuring Firewall..." -ForegroundColor Yellow
# Allow SSH
gcloud compute firewall-rules create allow-ssh-db `
    --project=$ProjectId `
    --allow=tcp:22 `
    --target-tags=ssh-server `
    --description="Allow SSH access" 2>$null

# DB should NOT be exposed publicly by default. 
# We rely on SSH tunneling or internal VPC access.
# If you need external access (NOT RECOMMENDED), uncomment below:
# gcloud compute firewall-rules create allow-postgres `
#     --project=$ProjectId `
#     --allow=tcp:5432 `
#     --target-tags=db-server `
#     --source-ranges=YOUR_IP_ONLY

# 4. Copy Files to VM
Write-Host "[4/6] Copying configuration and SQL files..." -ForegroundColor Yellow
# Copy docker-compose.prod.yml
gcloud compute scp docker-compose.prod.yml $VmName`:~/docker-compose.yml --zone=$Zone --project=$ProjectId
Test-CommandSuccess

# Prepare SQL file (Combine pre-init.sql + schema.sql)
$LocalInitFile = "init_combined.sql"
if (Test-Path "pre-init.sql") {
    Write-Host "Found pre-init.sql. Combining with $SqlFile..." -ForegroundColor Cyan
    Get-Content "pre-init.sql", $SqlFile | Set-Content $LocalInitFile -Encoding UTF8
}
else {
    Copy-Item $SqlFile $LocalInitFile
}

# Copy SQL file if it exists locally
if (Test-Path $LocalInitFile) {
    Write-Host "Uploading $LocalInitFile to VM..." -ForegroundColor Green
    gcloud compute scp $LocalInitFile $VmName`:~/init.sql --zone=$Zone --project=$ProjectId
    Test-CommandSuccess
}
else {
    Write-Host "Warning: SQL file '$SqlFile' not found locally. Skipping upload." -ForegroundColor Red
}

# 5. Start PostgreSQL Container
Write-Host "[5/6] Starting PostgreSQL Container..." -ForegroundColor Yellow
$startCmd = @"
docker-compose down
docker-compose up -d
sleep 10 # Wait for DB to start
"@
gcloud compute ssh $VmName --zone=$Zone --project=$ProjectId --command=$startCmd
Test-CommandSuccess

# 6. Import Data (if SQL uploaded)
Write-Host "[6/6] Importing Data..." -ForegroundColor Yellow
if (Test-Path $LocalInitFile) {
    $importCmd = @"
# Copy SQL file into container
docker cp ~/init.sql postgres-db:/tmp/init.sql

# Execute SQL
echo 'Executing SQL script...'
docker exec -i postgres-db psql -U postgres -d postgres -f /tmp/init.sql
"@
    gcloud compute ssh $VmName --zone=$Zone --project=$ProjectId --command=$importCmd
    Test-CommandSuccess
    Write-Host "Data import completed!" -ForegroundColor Green
}

Write-Host "========================================" -ForegroundColor Green
Write-Host "Deployment Complete!" -ForegroundColor Green
Write-Host "Access DB via SSH Tunnel: " 
Write-Host "gcloud compute ssh $VmName --zone=$Zone -- -L 5432:localhost:5432" -ForegroundColor Cyan
Write-Host "Then connect locally to port 5432."
Write-Host "========================================" -ForegroundColor Green
