$ProjectId = "braided-visitor-484216-i0"
$Region = "europe-west1"
$ServiceName = "core-backend-eu"

$envList = @(
    "ENVIRONMENT=production",
    "DATABASE_URL=postgresql+asyncpg://postgres:postgres@10.132.0.2:5432/postgres",
    "database_url=postgresql+asyncpg://postgres:postgres@10.132.0.2:5432/postgres",
    "REDIS_URL=redis://10.132.0.2:6379",
    "SUPABASE_URL=https://nidpneaqxghqueniodus.supabase.co",
    "supabase_url=https://nidpneaqxghqueniodus.supabase.co",
    "SUPABASE_SERVICE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im5pZHBuZWFxeGdocXVlbmlvZHVzIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2ODQ4Njg5MCwiZXhwIjoyMDg0MDYyODkwfQ.x_w0qmmAfLz12WmA_y7prqCoNreub_2BfS29jjx89p8",
    "supabase_service_key=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im5pZHBuZWFxeGdocXVlbmlvZHVzIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2ODQ4Njg5MCwiZXhwIjoyMDg0MDYyODkwfQ.x_w0qmmAfLz12WmA_y7prqCoNreub_2BfS29jjx89p8",
    "SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im5pZHBuZWFxeGdocXVlbmlvZHVzIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Njg0ODY4OTAsImV4cCI6MjA4NDA2Mjg5MH0.1MfRWL8mPm025Dq2LcdJ0-4VJJemClzreckiuFe_PJA",
    "FRONTEND_URL=*",
    "OPENAI_API_KEY=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",
    "openai_api_key=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",
    "ANTHROPIC_API_KEY=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",
    "anthropic_api_key=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",
    "AUTH_DISABLED=true",
    "auth_disabled=true",
    "UPLOAD_API_KEY=themison-test-key-2026",
    "USE_GRPC_RAG=true",
    "RAG_SERVICE_ADDRESS=rag-service-eu-573lfhdaza-ew.a.run.app:443"
)

$envString = $envList -join ","

Write-Host "Reseting service $ServiceName with fresh config..."
gcloud run services update $ServiceName `
    --project=$ProjectId `
    --region=$Region `
    --image="europe-west1-docker.pkg.dev/braided-visitor-484216-i0/themison-repo-eu/core-backend:latest" `
    --clear-secrets `
    --set-env-vars=$envString `
    --vpc-connector="themison-connector"
