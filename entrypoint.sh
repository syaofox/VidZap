#!/bin/sh
set -e

mkdir -p /app/downloads /app/data
chown -R nicevid:nicevid /app/downloads /app/data

exec gosu nicevid "$@"
