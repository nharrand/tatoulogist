FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron tar \
    && rm -rf /var/lib/apt/lists/*

COPY backup.py /opt/backup/backup.py
COPY backup.cron /etc/cron.d/backup
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY flag_2 /opt/backup/flag_2.template

RUN chmod 0644 /etc/cron.d/backup \
    && chmod 0755 /usr/local/bin/entrypoint.sh \
    && touch /var/log/backup.log

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
