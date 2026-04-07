#!/bin/bash
set -e

echo "Copying backup into container..."
docker cp /tmp/database_backup.dump postgres-db:/tmp/database_backup.dump

echo "Restoring database..."
docker exec postgres-db pg_restore -U postgres -d postgres --clean --if-exists -F c /tmp/database_backup.dump

echo "Verifying tables..."
docker exec postgres-db psql -U postgres -d postgres -c "\dt"

echo "âœ… Database restored successfully!"
