# Database Deployment - Themison

Your PostgreSQL database is now running on a secured Google Cloud VM.

## 🟢 System Status (Verified)

- **Project**: `braided-visitor-484216-i0` (Themison)
- **VM Name**: `themison-db-vm` (Zone: `us-central1-a`)
- **Database**: `postgres-db` (PostgreSQL 16 + pgvector)
- **Redis**: `themison-redis` (Redis 7)
- **Security**: Port 5432 is **NOT** exposed to the internet. Access is via SSH tunnel only.

## 🔑 How to Connect (Securely)

To connect using pgAdmin, DBeaver, or code from your local machine, creating a secure tunnel is the best practice.Actually, user asked "to where the backend is connected to production or ?". I answered. Then "why you connected to production ? is was still on trails"

### Step 1: Open SSH Tunnel
Run this command in your terminal:

```powershell
gcloud compute ssh themison-db-vm --project=braided-visitor-484216-i0 --zone=us-central1-a --ssh-flag="-L 5432:localhost:5432"
```

Keep this terminal window open.

### Step 2: Connect
Use these settings in your database client:
- **Host**: `localhost`
- **Port**: `5432`
- **User**: `postgres`
- **Password**: `postgres`
- **Database**: `postgres`

## 🛠 Management Commands

### Check Status
```powershell
gcloud compute ssh themison-db-vm --project=braided-visitor-484216-i0 --command="docker ps"
```

### View Logs
```powershell
gcloud compute ssh themison-db-vm --project=braided-visitor-484216-i0 --command="docker logs postgres-db --tail 50"
```

### Restart Database
```powershell
gcloud compute ssh themison-db-vm --project=braided-visitor-484216-i0 --command="docker restart postgres-db"
```

## 🔄 Re-Importing Schema

If you need to update the schema later:

1. Copy file to VM:
   ```powershell
   gcloud compute scp schema.sql themison-db-vm:init.sql --project=braided-visitor-484216-i0 --zone=us-central1-a
   ```

2. Execute inside container:
   ```powershell
   gcloud compute ssh themison-db-vm --project=braided-visitor-484216-i0 --command="docker exec -i postgres-db psql -U postgres -d postgres -f /init.sql"
   ```

## 📝 Notes on Supabase Roles
Your schema relies on Supabase-specific roles (`service_role`, `anon`, etc.). These have been **manually created** for you on this instance to ensure compatibility with your schema dump.
