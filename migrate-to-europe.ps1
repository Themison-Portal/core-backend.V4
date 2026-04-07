# ============================================
# Database VM Migration: US → Europe
# ============================================
# This script migrates the database from us-central1-a to a European region

param(
    [Parameter(Mandatory = $false)]
    [string]$EuropeanRegion = "europe-west1",  # Belgium (default)
    
    [Parameter(Mandatory = $false)]
    [string]$Zone = "europe-west1-b",
    
    [Parameter(Mandatory = $false)]
    [string]$ProjectId = "braided-visitor-484216-i0"
)

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Database Migration: US → Europe" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Source VM: themison-db-vm (us-central1-a)" -ForegroundColor Yellow
Write-Host "Target Region: $Zone" -ForegroundColor Green
Write-Host ""

# ============================================
# Step 1: Export Database from Current VM
# ============================================
Write-Host "Step 1: Exporting database from US VM..." -ForegroundColor Green

$exportScript = @'
# Export database
docker exec postgres-db pg_dump -U postgres -d postgres -F c -f /tmp/database_backup.dump

# Copy to host
docker cp postgres-db:/tmp/database_backup.dump /tmp/database_backup.dump

# Also export as SQL for verification
docker exec postgres-db pg_dump -U postgres -d postgres -f /tmp/database_backup.sql
docker cp postgres-db:/tmp/database_backup.sql /tmp/database_backup.sql
'@

Write-Host "Creating export script..." -ForegroundColor Yellow
$exportScript | Out-File -FilePath ".\export-db.sh" -Encoding UTF8

Write-Host "Uploading export script to US VM..." -ForegroundColor Yellow
gcloud compute scp .\export-db.sh themison-db-vm:/tmp/export-db.sh --project=$ProjectId --zone=us-central1-a

Write-Host "Executing database export..." -ForegroundColor Yellow
gcloud compute ssh themison-db-vm --project=$ProjectId --zone=us-central1-a --command="bash /tmp/export-db.sh"

Write-Host "Downloading database backup..." -ForegroundColor Yellow
gcloud compute scp themison-db-vm:/tmp/database_backup.dump .\database_backup.dump --project=$ProjectId --zone=us-central1-a
gcloud compute scp themison-db-vm:/tmp/database_backup.sql .\database_backup.sql --project=$ProjectId --zone=us-central1-a

Write-Host "✅ Database exported successfully!" -ForegroundColor Green
Write-Host ""

# ============================================
# Step 2: Create New VM in Europe
# ============================================
Write-Host "Step 2: Creating new VM in Europe ($Zone)..." -ForegroundColor Green

Write-Host "Checking if VM already exists..." -ForegroundColor Yellow
$existingVM = gcloud compute instances list --project=$ProjectId --filter="name=themison-db-vm-eu AND zone:$Zone" --format="value(name)" 2>$null

if ($existingVM) {
    Write-Host "⚠️  VM 'themison-db-vm-eu' already exists in $Zone" -ForegroundColor Yellow
    $response = Read-Host "Delete and recreate? (yes/no)"
    if ($response -eq "yes") {
        Write-Host "Deleting existing VM..." -ForegroundColor Yellow
        gcloud compute instances delete themison-db-vm-eu --project=$ProjectId --zone=$Zone --quiet
    }
    else {
        Write-Host "Using existing VM..." -ForegroundColor Yellow
    }
}

if (-not $existingVM -or $response -eq "yes") {
    Write-Host "Creating new VM instance..." -ForegroundColor Yellow
    gcloud compute instances create themison-db-vm-eu `
        --project=$ProjectId `
        --zone=$Zone `
        --machine-type=e2-medium `
        --image-family=cos-stable `
        --image-project=cos-cloud `
        --boot-disk-size=20GB `
        --boot-disk-type=pd-standard `
        --tags=database-server `
        --metadata=google-logging-enabled=true
    
    Write-Host "✅ VM created successfully!" -ForegroundColor Green
}

Write-Host ""

# ============================================
# Step 3: Setup Docker and Services on New VM
# ============================================
Write-Host "Step 3: Setting up Docker services on European VM..." -ForegroundColor Green

# Upload docker-compose file
Write-Host "Uploading docker-compose.prod.yml..." -ForegroundColor Yellow
gcloud compute scp .\docker-compose.prod.yml themison-db-vm-eu:/tmp/docker-compose.yml --project=$ProjectId --zone=$Zone

# Upload pre-init script
if (Test-Path ".\pre-init.sql") {
    Write-Host "Uploading pre-init.sql..." -ForegroundColor Yellow
    gcloud compute scp .\pre-init.sql themison-db-vm-eu:/tmp/pre-init.sql --project=$ProjectId --zone=$Zone
}

$setupScript = @'
#!/bin/bash
set -e

echo "Installing Docker Compose..."
sudo mkdir -p /usr/local/bin
sudo curl -L "https://github.com/docker/compose/releases/download/v2.24.5/docker-compose-linux-x86_64" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

echo "Setting up directories..."
sudo mkdir -p /var/lib/postgresql/data
sudo mkdir -p /var/lib/redis/data
sudo mkdir -p /docker-entrypoint-initdb.d

# Copy pre-init script if exists
if [ -f /tmp/pre-init.sql ]; then
    sudo cp /tmp/pre-init.sql /docker-entrypoint-initdb.d/
fi

echo "Starting Docker services..."
cd /tmp
sudo docker-compose -f docker-compose.yml up -d

echo "Waiting for PostgreSQL to be ready..."
sleep 15

echo "✅ Docker services started!"
sudo docker ps
'@

Write-Host "Creating setup script..." -ForegroundColor Yellow
$setupScript | Out-File -FilePath ".\setup-europe.sh" -Encoding UTF8

Write-Host "Uploading and executing setup script..." -ForegroundColor Yellow
gcloud compute scp .\setup-europe.sh themison-db-vm-eu:/tmp/setup-europe.sh --project=$ProjectId --zone=$Zone
gcloud compute ssh themison-db-vm-eu --project=$ProjectId --zone=$Zone --command="bash /tmp/setup-europe.sh"

Write-Host "✅ Docker services deployed!" -ForegroundColor Green
Write-Host ""

# ============================================
# Step 4: Import Database
# ============================================
Write-Host "Step 4: Importing database to European VM..." -ForegroundColor Green

Write-Host "Uploading database backup..." -ForegroundColor Yellow
gcloud compute scp .\database_backup.dump themison-db-vm-eu:/tmp/database_backup.dump --project=$ProjectId --zone=$Zone

$importScript = @'
#!/bin/bash
set -e

echo "Copying backup into container..."
docker cp /tmp/database_backup.dump postgres-db:/tmp/database_backup.dump

echo "Restoring database..."
docker exec postgres-db pg_restore -U postgres -d postgres --clean --if-exists -F c /tmp/database_backup.dump

echo "Verifying tables..."
docker exec postgres-db psql -U postgres -d postgres -c "\dt"

echo "✅ Database restored successfully!"
'@

Write-Host "Creating import script..." -ForegroundColor Yellow
$importScript | Out-File -FilePath ".\import-db.sh" -Encoding UTF8

Write-Host "Uploading and executing import script..." -ForegroundColor Yellow
gcloud compute scp .\import-db.sh themison-db-vm-eu:/tmp/import-db.sh --project=$ProjectId --zone=$Zone
gcloud compute ssh themison-db-vm-eu --project=$ProjectId --zone=$Zone --command="bash /tmp/import-db.sh"

Write-Host "✅ Database imported successfully!" -ForegroundColor Green
Write-Host ""

# ============================================
# Step 5: Get New Internal IP
# ============================================
Write-Host "Step 5: Getting new connection details..." -ForegroundColor Green

$newInternalIP = gcloud compute instances describe themison-db-vm-eu --project=$ProjectId --zone=$Zone --format="get(networkInterfaces[0].networkIP)"

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "✅ MIGRATION COMPLETED!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "NEW CONNECTION DETAILS:" -ForegroundColor Yellow
Write-Host "  Internal IP: $newInternalIP" -ForegroundColor White
Write-Host "  Database URL: postgresql+asyncpg://postgres:postgres@${newInternalIP}:5432/postgres" -ForegroundColor White
Write-Host "  Redis URL: redis://${newInternalIP}:6379" -ForegroundColor White
Write-Host ""
Write-Host "NEXT STEPS:" -ForegroundColor Yellow
Write-Host "  1. Update .env.production with new IP: $newInternalIP" -ForegroundColor White
Write-Host "  2. Test connection from your backend" -ForegroundColor White
Write-Host "  3. Once verified, delete old US VM: themison-db-vm" -ForegroundColor White
Write-Host ""
Write-Host "VM Locations:" -ForegroundColor Yellow
Write-Host "  ❌ Old (US): themison-db-vm (us-central1-a)" -ForegroundColor Red
Write-Host "  ✅ New (EU): themison-db-vm-eu ($Zone)" -ForegroundColor Green
Write-Host ""

# Save connection details to file
$connectionInfo = @"
# Updated Connection Details (European VM)
# Generated: $(Get-Date)

SUPABASE_DB_URL=postgresql+asyncpg://postgres:postgres@${newInternalIP}:5432/postgres
REDIS_URL=redis://${newInternalIP}:6379

# VM Details
VM_NAME=themison-db-vm-eu
VM_ZONE=$Zone
VM_INTERNAL_IP=$newInternalIP

# SSH Access
# gcloud compute ssh themison-db-vm-eu --project=$ProjectId --zone=$Zone

# Console Link
# https://console.cloud.google.com/compute/instancesDetail/zones/$Zone/instances/themison-db-vm-eu?project=$ProjectId
"@

$connectionInfo | Out-File -FilePath ".\EUROPE-CONNECTION.txt" -Encoding UTF8

Write-Host "Connection details saved to: EUROPE-CONNECTION.txt" -ForegroundColor Green
Write-Host ""
Write-Host "To delete old US VM (after verification):" -ForegroundColor Yellow
Write-Host "  gcloud compute instances delete themison-db-vm --project=$ProjectId --zone=us-central1-a" -ForegroundColor White
Write-Host ""
