from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext

from src.api.core.config import get_settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer = HTTPBearer(auto_error=False)


# PUBLIC_INTERFACE
def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return _pwd_context.hash(password)


# PUBLIC_INTERFACE
def verify_password(password: str, password_hash: str) -> bool:
    """Verify plaintext password against stored hash."""
    return _pwd_context.verify(password, password_hash)


# PUBLIC_INTERFACE
def create_access_token(*, user_id: str, email: str, username: str) -> str:
    """Create a signed JWT access token."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=settings.jwt_exp_minutes)
    payload: dict[str, Any] = {
        "sub": user_id,
        "email": email,
        "username": username,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


# PUBLIC_INTERFACE
def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT token, raising HTTP 401 on failure."""
    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}") from e


# PUBLIC_INTERFACE
def get_current_user_claims(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, Any]:
    """FastAPI dependency returning JWT claims for the current user."""
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=401, detail="Missing Authorization bearer token")
    return decode_token(creds.credentials)
