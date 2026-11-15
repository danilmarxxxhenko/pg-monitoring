#!/usr/bin/env bash
# Creates .env from .env.example, replacing every "change_me" value with a
# random secret. Existing .env is never overwritten.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  echo ".env already exists, leaving it untouched"
  exit 0
fi

while IFS= read -r line || [[ -n "$line" ]]; do
  if [[ "$line" == *=change_me ]]; then
    echo "${line%change_me}$(openssl rand -hex 16)"
  else
    echo "$line"
  fi
done < .env.example > .env

chmod 600 .env
echo ".env created with generated secrets"
