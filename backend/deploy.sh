#!/bin/sh
# Deploy the brain cleanly: ask the running brain to release device sessions (boards back off 60 s),
# deploy, then wait for the new container to answer.
set -e
cd "$(dirname "$0")"
URL=https://bpritesh1--pixel-brain-web.modal.run
TOKEN=$(grep BACKEND_TOKEN ../include/secrets.h | sed -E 's/.*"([^"]*)".*/\1/')
echo "draining device sessions..."
curl -s -m 20 -X POST -H "Authorization: Bearer $TOKEN" "$URL/api/admin/drain" || echo "(drain unavailable - old brain)"
echo
uv run --extra modal modal deploy modal_app.py | grep -E "deployed|error" || true
until curl -s -m 20 "$URL/health" | grep -q chat_model; do sleep 3; done
echo "brain healthy: $(curl -s -m 20 $URL/health)"
