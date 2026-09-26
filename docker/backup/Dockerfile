# Nightly encrypted backups of PostgreSQL and the S3 bucket (AES-256, key from BACKUP_PASSPHRASE).
FROM alpine:3.22
RUN apk upgrade --no-cache && apk add --no-cache postgresql16-client openssl bash tini tar gzip coreutils rclone
COPY backup.sh restore.sh entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/*.sh && mkdir -p /backups
ENV RCLONE_CONFIG_S3_TYPE=s3 RCLONE_CONFIG_S3_PROVIDER=SeaweedFS RCLONE_CONFIG_S3_ENDPOINT=http://s3:8333 \
    RCLONE_CONFIG_S3_REGION=us-east-1
ENTRYPOINT ["/sbin/tini", "--", "/usr/local/bin/entrypoint.sh"]
