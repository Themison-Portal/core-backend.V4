#!/bin/bash
DATE=$(date +"%Y-%m-%d_%H-%M")
BACKUP_DIR="/tmp"
FILE_NAME="db_backup_$DATE.sql.gz"
CONTAINER_NAME="themison-postgres"
BUCKET_URL="gs://themison-documents-europe/backups"

echo "Starting backup for $DATE..."

# Dump from Docker Container (PostgreSQL)
sudo docker exec $CONTAINER_NAME pg_dump -U postgres postgres | gzip > "$BACKUP_DIR/$FILE_NAME"

if [ $? -eq 0 ]; then
  echo "Database dump successful: $FILE_NAME"
  
  # Upload to GCS
  gcloud storage cp "$BACKUP_DIR/$FILE_NAME" "$BUCKET_URL/$FILE_NAME"
  
  if [ $? -eq 0 ]; then
    echo "Upload to Google Cloud Storage successful!"
    rm "$BACKUP_DIR/$FILE_NAME" # Cleanup local file
  else
    echo "Error: Upload failed."
  fi
else
  echo "Error: Database dump failed."
fi
