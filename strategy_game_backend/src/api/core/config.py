import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables."""

    database_url: str
    jwt_secret: str
    jwt_issuer: str
    jwt_audience: str
    jwt_exp_minutes: int
    cors_origins: list[str]
    turn_time_limit_seconds: int


def _split_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


# PUBLIC_INTERFACE
def get_settings() -> Settings:
    """Load and return application settings from environment variables.

    Required environment variables:
    - POSTGRES_URL: PostgreSQL DSN/URL
    - JWT_SECRET: secret used to sign JWTs

    Optional:
    - JWT_ISSUER, JWT_AUDIENCE, JWT_EXP_MINUTES
    - CORS_ORIGINS: comma-separated list of allowed origins (default allows localhost dev)
    - TURN_TIME_LIMIT_SECONDS: default 30
    """
    database_url = os.getenv("POSTGRES_URL", "").strip()
    if not database_url:
        # Keep message explicit so deploy/orchestrator can set it in .env.
        raise RuntimeError("Missing required env var POSTGRES_URL")

    jwt_secret = os.getenv("JWT_SECRET", "").strip()
    if not jwt_secret:
        raise RuntimeError("Missing required env var JWT_SECRET")

    jwt_issuer = os.getenv("JWT_ISSUER", "strategy-game-backend")
    jwt_audience = os.getenv("JWT_AUDIENCE", "strategy-game-frontend")
    jwt_exp_minutes = int(os.getenv("JWT_EXP_MINUTES", "1440"))  # 24h

    # Align CORS to React dev + deployed. Can be overridden.
    cors_origins_raw = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    )
    cors_origins = _split_csv(cors_origins_raw)

    turn_time_limit_seconds = int(os.getenv("TURN_TIME_LIMIT_SECONDS", "30"))

    return Settings(
        database_url=database_url,
        jwt_secret=jwt_secret,
        jwt_issuer=jwt_issuer,
        jwt_audience=jwt_audience,
        jwt_exp_minutes=jwt_exp_minutes,
        cors_origins=cors_origins,
        turn_time_limit_seconds=turn_time_limit_seconds,
    )
