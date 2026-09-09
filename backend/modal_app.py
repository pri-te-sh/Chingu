"""Deploy Pixel's brain to Modal:   modal deploy modal_app.py
Models are baked into the image at build time so cold starts only pay for container boot + model load.
Secrets: `pixel-token` (PIXEL_TOKEN, generated) and `ollama-api-key` (OLLAMA_API_KEY, created by the user):
    modal secret create ollama-api-key OLLAMA_API_KEY=<key>
"""
import modal

APP_NAME = "pixel-brain"
MODELS_DIR = "/models"
DATA_DIR = "/data"


def _bake_models():
    import os
    os.environ["PIXEL_MODELS_DIR"] = MODELS_DIR
    from pixel import stt, tts
    stt.load(); tts.load()


image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi>=0.115", "uvicorn[standard]>=0.30", "websockets>=12", "numpy>=1.26",
                 "httpx>=0.27", "faster-whisper>=1.1", "piper-tts>=1.2")
    .env({"PIXEL_MODELS_DIR": MODELS_DIR, "PIXEL_DATA_DIR": DATA_DIR, "PIXEL_STORE": "modal",
          "OLLAMA_HOST": "https://ollama.com", "OLLAMA_MODEL": "gemma4:cloud"})
    .add_local_python_source("pixel", copy=True)
    .add_local_dir("pixel/portal", remote_path="/root/pixel/portal", copy=True)
    .run_function(_bake_models)
)

app = modal.App(APP_NAME, image=image)
memory_volume = modal.Volume.from_name("pixel-memory", create_if_missing=True)


@app.function(
    cpu=2.0,
    memory=2048,
    scaledown_window=600,                 # stay warm 10 min after the last exchange -> instant follow-ups
    timeout=3600,                         # device WS sessions live up to an hour (board reconnects seamlessly)
    secrets=[modal.Secret.from_name("pixel-token"), modal.Secret.from_name("ollama-api-key")],
    volumes={DATA_DIR: memory_volume},
)
@modal.concurrent(max_inputs=20)
@modal.asgi_app()
def web():
    from pixel.server import app as fastapi_app   # state lives in modal.Dict("pixel-store"), shared by all containers
    return fastapi_app
