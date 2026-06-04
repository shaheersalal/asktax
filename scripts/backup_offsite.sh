#!/bin/bash
# Uploads last night's backup to Backblaze B2.
# Requires: apt install rclone, then: rclone config (add B2 remote named "b2")
DATE=$(date +%Y-%m-%d)
BACKUP_DIR=/opt/fbrbot/backups

rclone copy $BACKUP_DIR/postgres_$DATE.sql.gz b2:asktax-backups/postgres/
rclone copy $BACKUP_DIR/minio_$DATE         b2:asktax-backups/minio/$DATE/ --transfers=10

echo "Offsite backup uploaded: $DATE"
