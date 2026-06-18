"""CO2 data server.

Serves /data/latest/sensor.json as GET /sensor.json.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

DATA_DIR = Path(os.environ.get("CO2_DATA_DIR", "/data/latest"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("co2-server")

app = FastAPI(title="co2-server", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/sensor.json")
def sensor():
    path = DATA_DIR / "sensor.json"
    if not path.exists():
        raise HTTPException(503, "No sensor data yet")
    return Response(content=path.read_bytes(), media_type="application/json")


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host=host, port=port)
