#!/bin/sh
set -e
if [ -n "$FLAG_1" ]; then
  sed "s|__FLAG__|$FLAG_1|g" /app/flag_1.template > /flag_1
  chmod 644 /flag_1
fi

exec "$@"
