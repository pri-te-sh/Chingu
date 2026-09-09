#!/bin/sh
# Run Pixel's brain locally against the local Ollama daemon.
cd "$(dirname "$0")"
exec uv run --extra laptop uvicorn pixel.server:app --host 0.0.0.0 --port 8765 --log-level warning
