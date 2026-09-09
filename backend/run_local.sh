#!/bin/sh
# Run Pixel's brain locally against the local Ollama daemon.
cd "$(dirname "$0")"
# the local Ollama daemon is signed in to Ollama Cloud, so cloud tags (gemma4:cloud, deepseek-v4-flash:cloud) route through it
exec uv run --extra laptop uvicorn pixel.server:app --host 0.0.0.0 --port 8765 --log-level warning
