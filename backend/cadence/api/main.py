"""Main FastAPI entrypoint for the Cadence Railway Possession Planning Engine.

To run locally for development:
    uvicorn cadence.api.main:app --reload --port 8000
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from cadence.api.routes import router

app = FastAPI(
    title="Cadence API",
    description="Railway possession planning & constraint reasoning engine",
    version="0.1.0",
)

# Allow requests from Vite frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


if __name__ == "__main__":
    uvicorn.run("cadence.api.main:app", host="0.0.0.0", port=8000, reload=True)
