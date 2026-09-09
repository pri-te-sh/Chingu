#!/bin/sh
# Run Pixel's brain locally against the local Ollama daemon.
cd "$(dirname "$0")"
# local Ollama has no cloud tags; point both roles at a local model
export PIXEL_DEFAULT_CHAT_MODEL=${PIXEL_DEFAULT_CHAT_MODEL:-gemma4:e2b}
export PIXEL_DEFAULT_MEMORY_MODEL=${PIXEL_DEFAULT_MEMORY_MODEL:-gemma4:e2b}
exec uv run --extra laptop uvicorn pixel.server:app --host 0.0.0.0 --port 8765 --log-level warning
