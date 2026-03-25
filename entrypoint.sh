#!/bin/sh
set -e

mkdir -p /app/downloads /app/cookies /app/data
chown -R nicevid:nicevid /app/downloads /app/cookies /app/data

exec gosu nicevid "$@"
