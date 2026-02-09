from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.core.config import get_settings
from src.api.routers import auth, games, lobbies

openapi_tags = [
    {"name": "system", "description": "Health/system endpoints."},
    {"name": "auth", "description": "Authentication and user identity."},
    {"name": "lobbies", "description": "Lobby creation/join/leave and matchmaking."},
    {"name": "games", "description": "Game lifecycle, state sync, and validated player actions."},
]

app = FastAPI(
    title="Strategic Grid Battle API",
    description=(
        "Backend REST API for a turn-based grid strategy game. "
        "Includes JWT auth, lobby matchmaking, game creation, state sync, "
        "validated actions, and turn timer enforcement."
    ),
    version="0.1.0",
    openapi_tags=openapi_tags,
)

settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get(
    "/",
    tags=["system"],
    summary="Health check",
    description="Simple health check endpoint.",
    operation_id="health_check",
)
# PUBLIC_INTERFACE
def health_check():
    """Health check endpoint.

    Returns:
        JSON object with a simple message indicating service is running.
    """
    return {"message": "Healthy"}


app.include_router(auth.router)
app.include_router(lobbies.router)
app.include_router(games.router)
