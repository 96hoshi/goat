#!/bin/bash
# Sync goatlib tools and tasks to Windmill
# This script reads the token from file and syncs all tools/tasks

set -e

TOKEN_FILE="${WINDMILL_TOKEN_FILE:-/app/data/windmill/.token}"
WINDMILL_URL="${WINDMILL_URL:-http://windmill-server:8000}"
WINDMILL_WORKSPACE="${WINDMILL_WORKSPACE:-goat}"

echo "============================================================"
echo "Windmill Tools Sync"
echo "============================================================"
echo "  URL: $WINDMILL_URL"
echo "  Workspace: $WINDMILL_WORKSPACE"
echo "  Token file: $TOKEN_FILE"
echo ""

# Wait for token file to exist (in case of race condition)
MAX_WAIT=30
WAITED=0
while [ ! -f "$TOKEN_FILE" ] && [ $WAITED -lt $MAX_WAIT ]; do
    echo "Waiting for token file..."
    sleep 1
    WAITED=$((WAITED + 1))
done

if [ ! -f "$TOKEN_FILE" ]; then
    echo "ERROR: Token file not found: $TOKEN_FILE"
    exit 1
fi

# Read token from file
WINDMILL_TOKEN=$(cat "$TOKEN_FILE")
export WINDMILL_TOKEN

if [ -z "$WINDMILL_TOKEN" ]; then
    echo "ERROR: Token file is empty"
    exit 1
fi

echo "Token loaded successfully"
echo ""

# Sync tools
echo "Syncing analytics tools..."
python -m goatlib.tools.sync_windmill \
    --url "$WINDMILL_URL" \
    --workspace "$WINDMILL_WORKSPACE" \
    --token "$WINDMILL_TOKEN"

echo ""

# Sync tasks
echo "Syncing scheduled tasks..."
python -m goatlib.tasks.sync_windmill \
    --url "$WINDMILL_URL" \
    --workspace "$WINDMILL_WORKSPACE" \
    --token "$WINDMILL_TOKEN"

echo ""
echo "============================================================"
echo "Windmill sync complete!"
echo "============================================================"
