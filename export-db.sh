# Export database
docker exec postgres-db pg_dump -U postgres -d postgres -F c -f /tmp/database_backup.dump

# Copy to host
docker cp postgres-db:/tmp/database_backup.dump /tmp/database_backup.dump

# Also export as SQL for verification
docker exec postgres-db pg_dump -U postgres -d postgres -f /tmp/database_backup.sql
docker cp postgres-db:/tmp/database_backup.sql /tmp/database_backup.sql
