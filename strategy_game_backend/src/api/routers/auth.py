from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.core.auth import create_access_token, get_current_user_claims, hash_password, verify_password
from src.api.core.db import execute, fetch_one, get_db
from src.api.models.schemas import LoginRequest, RegisterRequest, TokenResponse, UserPublic

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=201,
    summary="Register a new user",
    description="Creates a new user account and returns a JWT access token.",
    operation_id="auth_register",
)
# PUBLIC_INTERFACE
def register(payload: RegisterRequest):
    """Register a new user, storing password hash in PostgreSQL."""
    with get_db() as conn:
        existing = fetch_one(conn, "SELECT id FROM users WHERE email=%s OR username=%s", (payload.email, payload.username))
        if existing:
            raise HTTPException(status_code=409, detail="Email or username already registered")

        password_hash = hash_password(payload.password)
        row = fetch_one(
            conn,
            """
            INSERT INTO users(email, username, password_hash, display_name, last_login_at)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, email, username, display_name
            """,
            (payload.email, payload.username, password_hash, payload.display_name, datetime.now(timezone.utc)),
        )
        assert row is not None

        token = create_access_token(user_id=str(row["id"]), email=row["email"], username=row["username"])
        user = UserPublic(id=str(row["id"]), email=row["email"], username=row["username"], display_name=row["display_name"])
        return TokenResponse(access_token=token, user=user)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login",
    description="Authenticates by username/email and password and returns a JWT access token.",
    operation_id="auth_login",
)
# PUBLIC_INTERFACE
def login(payload: LoginRequest):
    """Login existing user and return a JWT."""
    with get_db() as conn:
        user = fetch_one(
            conn,
            "SELECT id, email, username, display_name, password_hash FROM users WHERE email=%s OR username=%s",
            (payload.username_or_email, payload.username_or_email),
        )
        if not user or not verify_password(payload.password, user["password_hash"]):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

        execute(conn, "UPDATE users SET last_login_at=%s WHERE id=%s", (datetime.now(timezone.utc), user["id"]))
        token = create_access_token(user_id=str(user["id"]), email=user["email"], username=user["username"])
        return TokenResponse(
            access_token=token,
            user=UserPublic(
                id=str(user["id"]),
                email=user["email"],
                username=user["username"],
                display_name=user["display_name"],
            ),
        )


@router.get(
    "/me",
    response_model=UserPublic,
    summary="Get current user",
    description="Returns the authenticated user's public profile derived from the token and DB.",
    operation_id="auth_me",
)
# PUBLIC_INTERFACE
def me(claims=Depends(get_current_user_claims)):
    """Return current user from DB."""
    user_id = claims["sub"]
    with get_db() as conn:
        row = fetch_one(conn, "SELECT id, email, username, display_name FROM users WHERE id=%s", (user_id,))
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        return UserPublic(id=str(row["id"]), email=row["email"], username=row["username"], display_name=row["display_name"])
