$secrets = @{
    "openai-api-key"       = "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"
    "anthropic-api-key"    = "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"
    "supabase-url"         = "https://nidpneaqxghqueniodus.supabase.co"
    "supabase-service-key" = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im5pZHBuZWFxeGdocXVlbmlvZHVzIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2ODQ4Njg5MCwiZXhwIjoyMDg0MDYyODkwfQ.x_w0qmmAfLz12WmA_y7prqCoNreub_2BfS29jjx89p8"
    "supabase-anon-key"    = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im5pZHBuZWFxeGdocXVlbmlvZHVzIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Njg0ODY4OTAsImV4cCI6MjA4NDA2Mjg5MH0.1MfRWL8mPm025Dq2LcdJ0-4VJJemClzreckiuFe_PJA"
    "database-url"         = "postgresql+asyncpg://postgres:postgres@10.132.0.2:5432/postgres"
    "redis-url"            = "redis://10.132.0.2:6379"
}

$ProjectId = "braided-visitor-484216-i0"

foreach ($name in $secrets.Keys) {
    Write-Host "Processing secret $name..."
    $check = gcloud secrets list --filter="name:$name" --format="value(name)" --project=$ProjectId
    if (-not $check) {
        Write-Host "Creating secret $name..."
        gcloud secrets create $name --replication-policy="automatic" --project=$ProjectId
    }
    
    $val = $secrets[$name]
    $val | gcloud secrets versions add $name --data-file=- --project=$ProjectId
}
