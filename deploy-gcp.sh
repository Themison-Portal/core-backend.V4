#!/bin/bash

# Themison Backend - Google Cloud VM Setup Script
# This script automates the deployment of Themison Backend on Google Cloud

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
PROJECT_ID="${PROJECT_ID:-your-project-id}"
ZONE="${ZONE:-us-central1-a}"
REGION="${REGION:-us-central1}"
VM_NAME="themison-backend-vm"
MACHINE_TYPE="e2-medium"
BOOT_DISK_SIZE="50GB"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Themison Backend - GCP Deployment${NC}"
echo -e "${GREEN}========================================${NC}"

# Function to print status
print_status() {
    echo -e "${YELLOW}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    print_error "gcloud CLI is not installed. Please install it first."
    echo "Visit: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

# Step 1: Authenticate and set project
print_status "Step 1: Authenticating with Google Cloud..."
gcloud auth login
gcloud config set project $PROJECT_ID
print_success "Authenticated and project set to $PROJECT_ID"

# Step 2: Enable required APIs
print_status "Step 2: Enabling required Google Cloud APIs..."
gcloud services enable compute.googleapis.com
gcloud services enable secretmanager.googleapis.com
gcloud services enable cloudresourcemanager.googleapis.com
print_success "APIs enabled"

# Step 3: Create firewall rules
print_status "Step 3: Creating firewall rules..."
gcloud compute firewall-rules create allow-themison-http \
    --project=$PROJECT_ID \
    --allow=tcp:80,tcp:8000 \
    --target-tags=http-server \
    --description="Allow HTTP traffic to Themison backend" \
    2>/dev/null || print_status "Firewall rule already exists"

gcloud compute firewall-rules create allow-themison-https \
    --project=$PROJECT_ID \
    --allow=tcp:443 \
    --target-tags=https-server \
    --description="Allow HTTPS traffic to Themison backend" \
    2>/dev/null || print_status "Firewall rule already exists"
print_success "Firewall rules configured"

# Step 4: Create VM instance
print_status "Step 4: Creating Compute Engine VM..."
gcloud compute instances create $VM_NAME \
    --project=$PROJECT_ID \
    --zone=$ZONE \
    --machine-type=$MACHINE_TYPE \
    --image-family=cos-stable \
    --image-project=cos-cloud \
    --boot-disk-size=$BOOT_DISK_SIZE \
    --boot-disk-type=pd-balanced \
    --tags=http-server,https-server \
    --metadata=startup-script='#!/bin/bash
        echo "Themison VM is ready"
        docker --version
    ' || print_status "VM already exists"

print_success "VM created: $VM_NAME"

# Step 5: Get VM external IP
print_status "Step 5: Getting VM external IP..."
EXTERNAL_IP=$(gcloud compute instances describe $VM_NAME \
    --zone=$ZONE \
    --format='get(networkInterfaces[0].accessConfigs[0].natIP)')
print_success "VM External IP: $EXTERNAL_IP"

# Step 6: Create secrets (interactive)
print_status "Step 6: Setting up secrets in Secret Manager..."
echo -e "${YELLOW}Please provide the following credentials:${NC}"

read -p "OpenAI API Key: " OPENAI_KEY
echo -n "$OPENAI_KEY" | gcloud secrets create openai-api-key --data-file=- 2>/dev/null || \
    echo -n "$OPENAI_KEY" | gcloud secrets versions add openai-api-key --data-file=-

read -p "Anthropic API Key: " ANTHROPIC_KEY
echo -n "$ANTHROPIC_KEY" | gcloud secrets create anthropic-api-key --data-file=- 2>/dev/null || \
    echo -n "$ANTHROPIC_KEY" | gcloud secrets versions add anthropic-api-key --data-file=-

read -p "Supabase URL: " SUPABASE_URL
echo -n "$SUPABASE_URL" | gcloud secrets create supabase-url --data-file=- 2>/dev/null || \
    echo -n "$SUPABASE_URL" | gcloud secrets versions add supabase-url --data-file=-

read -p "Supabase Service Key: " SUPABASE_SERVICE_KEY
echo -n "$SUPABASE_SERVICE_KEY" | gcloud secrets create supabase-service-key --data-file=- 2>/dev/null || \
    echo -n "$SUPABASE_SERVICE_KEY" | gcloud secrets versions add supabase-service-key --data-file=-

read -p "Supabase Anon Key: " SUPABASE_ANON_KEY
echo -n "$SUPABASE_ANON_KEY" | gcloud secrets create supabase-anon-key --data-file=- 2>/dev/null || \
    echo -n "$SUPABASE_ANON_KEY" | gcloud secrets versions add supabase-anon-key --data-file=-

print_success "Secrets created in Secret Manager"

# Step 7: Grant VM access to secrets
print_status "Step 7: Granting VM access to secrets..."
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
SERVICE_ACCOUNT="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"

for secret in openai-api-key anthropic-api-key supabase-url supabase-service-key supabase-anon-key; do
    gcloud secrets add-iam-policy-binding $secret \
        --member="serviceAccount:$SERVICE_ACCOUNT" \
        --role="roles/secretmanager.secretAccessor" \
        --quiet
done
print_success "VM granted access to secrets"

# Step 8: Deploy application to VM
print_status "Step 8: Deploying application to VM..."
echo -e "${YELLOW}Connecting to VM and deploying application...${NC}"

gcloud compute ssh $VM_NAME --zone=$ZONE --command="
    set -e
    
    # Clone repository
    if [ ! -d 'core-backend.V4' ]; then
        git clone https://github.com/Themison-Portal/core-backend.V4.git
    else
        cd core-backend.V4 && git pull && cd ..
    fi
    
    cd core-backend.V4
    
    # Create production .env file
    cat > .env.production << 'EOF'
OPENAI_API_KEY=\$(gcloud secrets versions access latest --secret='openai-api-key')
ANTHROPIC_API_KEY=\$(gcloud secrets versions access latest --secret='anthropic-api-key')
SUPABASE_URL=\$(gcloud secrets versions access latest --secret='supabase-url')
SUPABASE_SERVICE_KEY=\$(gcloud secrets versions access latest --secret='supabase-service-key')
SUPABASE_ANON_KEY=\$(gcloud secrets versions access latest --secret='supabase-anon-key')
SUPABASE_DB_URL=postgresql+asyncpg://postgres:postgres@db:5432/postgres
SUPABASE_DB_PASSWORD=postgres
REDIS_URL=redis://redis:6379
FRONTEND_URL=https://your-frontend-domain.com
ENVIRONMENT=production
EOF
    
    # Start Docker containers
    docker-compose -f docker-compose.prod.yml up -d
    
    echo 'Deployment complete!'
    docker ps
"

print_success "Application deployed to VM"

# Final summary
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Deployment Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "VM Name: ${YELLOW}$VM_NAME${NC}"
echo -e "External IP: ${YELLOW}$EXTERNAL_IP${NC}"
echo -e "API URL: ${YELLOW}http://$EXTERNAL_IP:8000${NC}"
echo -e "Health Check: ${YELLOW}http://$EXTERNAL_IP:8000/health${NC}"
echo -e ""
echo -e "${YELLOW}Next Steps:${NC}"
echo -e "1. Configure your domain DNS to point to $EXTERNAL_IP"
echo -e "2. Set up SSL certificate (Let's Encrypt recommended)"
echo -e "3. Update FRONTEND_URL in .env.production"
echo -e "4. Monitor logs: ${YELLOW}gcloud compute ssh $VM_NAME --zone=$ZONE --command='docker-compose logs -f'${NC}"
echo -e "${GREEN}========================================${NC}"
