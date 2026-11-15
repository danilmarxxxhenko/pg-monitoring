#!/usr/bin/env bash
# Creates .env from .env.example, replacing every "change_me" value with a
# random secret. If .env already exists, only keys missing from it are
# appended (existing values are never changed).
set -euo pipefail
cd "$(dirname "$0")/.."

render() {
  local line=$1
  if [[ "$line" == *=change_me ]]; then
    echo "${line%change_me}$(openssl rand -hex 16)"
  else
    echo "$line"
  fi
}

if [[ ! -f .env ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do render "$line"; done < .env.example > .env
  chmod 600 .env
  echo ".env created with generated secrets"
  exit 0
fi

added=0
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ "$line" =~ ^([A-Z0-9_]+)= ]] || continue
  key=${BASH_REMATCH[1]}
  if ! grep -q "^${key}=" .env; then
    render "$line" >> .env
    added=$((added + 1))
  fi
done < .env.example
echo ".env exists; added $added missing key(s)"
