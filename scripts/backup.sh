#!/bin/bash
# Runs nightly via cron. Keeps 7 days of backups.
BACKUP_DIR=/opt/fbrbot/backups
DATE=$(date +%Y-%m-%d)

mkdir -p $BACKUP_DIR

# PostgreSQL
docker exec fbrbot_postgres pg_dump -U fbrbot fbrbot_db \
  | gzip > $BACKUP_DIR/postgres_$DATE.sql.gz

# MinIO (FBR PDFs) — sync to local backup dir
docker run --rm \
  --network fbrbot_internal \
  -v $BACKUP_DIR:/backup \
  minio/mc:latest \
  sh -c "mc alias set local http://minio:9000 fbrbot_minio fbrbot_minio_pass && \
         mc mirror local/fbr-documents /backup/minio_$DATE"

# Delete backups older than 7 days
find $BACKUP_DIR -name "*.sql.gz" -mtime +7 -delete
find $BACKUP_DIR -maxdepth 1 -name "minio_*" -mtime +7 -exec rm -rf {} +

echo "Backup complete: $DATE"
