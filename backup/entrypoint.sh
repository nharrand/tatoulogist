#!/bin/sh
set -e

# Materialize the flag from the environment into a root-only file, then drop it
# from the environment so it is not visible in the process list or to children.
if [ -n "$FLAG_2" ]; then
  sed "s|__FLAG__|$FLAG_2|g" /opt/backup/flag_2.template > /root/flag_2
  chmod 600 /root/flag_2
  unset FLAG_2
fi

echo "backup service started; running cron in the foreground"
exec cron -f
