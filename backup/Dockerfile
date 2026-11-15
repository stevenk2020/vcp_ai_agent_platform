FROM alpine:3.20

# postgresql16-client: pg_dump, pg_dumpall, pg_restore, psql (matches the pg16 server)
# age: encryption with the operator's public key; rclone: upload to the off-site remote
RUN apk add --no-cache bash tzdata postgresql16-client rclone age

COPY entrypoint.sh backup.sh healthcheck.sh restore.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/backup.sh \
             /usr/local/bin/healthcheck.sh /usr/local/bin/restore.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
