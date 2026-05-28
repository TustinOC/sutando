#!/bin/bash
# Rename SUTANDO_PRIVATE_DIR → SUTANDO_MEMORY_DIR in $WORKSPACE/.env (#870)
set -e
WORKSPACE="${1:-}"
ENV_FILE="$WORKSPACE/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "  0001: no .env found at $ENV_FILE — skipping (clean install)"
  exit 0
fi

if ! grep -q "^SUTANDO_PRIVATE_DIR=" "$ENV_FILE" 2>/dev/null; then
  echo "  0001: SUTANDO_PRIVATE_DIR not found in .env — already migrated"
  exit 0
fi

if sed --version 2>/dev/null | grep -q 'GNU'; then
  sed -i 's/^SUTANDO_PRIVATE_DIR=/SUTANDO_MEMORY_DIR=/' "$ENV_FILE"
else
  sed -i '' 's/^SUTANDO_PRIVATE_DIR=/SUTANDO_MEMORY_DIR=/' "$ENV_FILE"
fi

echo "  0001: renamed SUTANDO_PRIVATE_DIR → SUTANDO_MEMORY_DIR in $ENV_FILE"
