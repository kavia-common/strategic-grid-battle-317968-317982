from __future__ import annotations

import random
import string
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from src.api.core.auth import get_current_user_claims
from src.api.core.db import execute, fetch_all, fetch_one, get_db
from src.api.models.schemas import (
    LobbyCreateRequest,
    LobbyJoinRequest,
    LobbyPlayer,
    LobbyPublic,
    MatchmakeRequest,
    MessageResponse,
    UserPublic,
)

router = APIRouter(prefix="/lobbies", tags=["lobbies"])


def _generate_code() -> str:
    return "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(6))


def _get_lobby_with_players(conn, lobby_id: str) -> LobbyPublic:
    lobby = fetch_one(
        conn,
        """
        SELECT id, code, name, status, max_players, is_private, host_user_id, created_at
        FROM lobbies
        WHERE id=%s
        """,
        (lobby_id,),
    )
    if not lobby:
        raise HTTPException(status_code=404, detail="Lobby not found")

    players = fetch_all(
        conn,
        """
        SELECT lp.user_id, lp.slot, lp.status, lp.joined_at, lp.ready_at,
               u.email, u.username, u.display_name
        FROM lobby_players lp
        JOIN users u ON u.id = lp.user_id
        WHERE lp.lobby_id=%s
        ORDER BY lp.slot ASC
        """,
        (lobby_id,),
    )

    lobby_players: list[LobbyPlayer] = []
    for p in players:
        lobby_players.append(
            LobbyPlayer(
                user=UserPublic(
                    id=str(p["user_id"]),
                    email=p["email"],
                    username=p["username"],
                    display_name=p["display_name"],
                ),
                slot=p["slot"],
                status=p["status"],
                joined_at=p["joined_at"],
                ready_at=p["ready_at"],
            )
        )

    return LobbyPublic(
        id=str(lobby["id"]),
        code=lobby["code"],
        name=lobby["name"],
        status=lobby["status"],
        max_players=lobby["max_players"],
        is_private=lobby["is_private"],
        host_user_id=str(lobby["host_user_id"]),
        created_at=lobby["created_at"],
        players=lobby_players,
    )


@router.get(
    "",
    response_model=list[LobbyPublic],
    summary="List open lobbies",
    description="Lists public lobbies in 'open' state.",
    operation_id="lobbies_list",
)
# PUBLIC_INTERFACE
def list_lobbies():
    """List public open lobbies."""
    with get_db() as conn:
        lobbies = fetch_all(
            conn,
            """
            SELECT id
            FROM lobbies
            WHERE status='open' AND is_private=false
            ORDER BY created_at DESC
            LIMIT 50
            """,
        )
        return [_get_lobby_with_players(conn, str(l["id"])) for l in lobbies]


@router.post(
    "",
    response_model=LobbyPublic,
    summary="Create a lobby",
    description="Creates a lobby hosted by the current user and joins them to slot 0.",
    operation_id="lobbies_create",
)
# PUBLIC_INTERFACE
def create_lobby(payload: LobbyCreateRequest, claims=Depends(get_current_user_claims)):
    """Create a lobby and join host."""
    user_id = claims["sub"]
    with get_db() as conn:
        # Ensure user exists
        u = fetch_one(conn, "SELECT id FROM users WHERE id=%s", (user_id,))
        if not u:
            raise HTTPException(status_code=401, detail="User not found")

        # Generate unique code
        code = _generate_code()
        for _ in range(10):
            existing = fetch_one(conn, "SELECT id FROM lobbies WHERE code=%s", (code,))
            if not existing:
                break
            code = _generate_code()
        else:
            raise HTTPException(status_code=500, detail="Failed to generate lobby code")

        lobby = fetch_one(
            conn,
            """
            INSERT INTO lobbies(code, name, host_user_id, max_players, is_private)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (code, payload.name, user_id, payload.max_players, payload.is_private),
        )
        assert lobby is not None
        lobby_id = str(lobby["id"])

        execute(
            conn,
            "INSERT INTO lobby_players(lobby_id, user_id, slot, status) VALUES (%s, %s, %s, 'joined')",
            (lobby_id, user_id, 0),
        )

        return _get_lobby_with_players(conn, lobby_id)


@router.post(
    "/join",
    response_model=LobbyPublic,
    summary="Join a lobby",
    description="Joins the lobby by code into the lowest available slot.",
    operation_id="lobbies_join",
)
# PUBLIC_INTERFACE
def join_lobby(payload: LobbyJoinRequest, claims=Depends(get_current_user_claims)):
    """Join a lobby by code."""
    user_id = claims["sub"]
    with get_db() as conn:
        lobby = fetch_one(
            conn,
            "SELECT id, status, max_players FROM lobbies WHERE code=%s",
            (payload.code.upper(),),
        )
        if not lobby:
            raise HTTPException(status_code=404, detail="Lobby not found")
        if lobby["status"] != "open":
            raise HTTPException(status_code=409, detail="Lobby is not open")

        already = fetch_one(conn, "SELECT 1 FROM lobby_players WHERE lobby_id=%s AND user_id=%s", (lobby["id"], user_id))
        if already:
            return _get_lobby_with_players(conn, str(lobby["id"]))

        taken = fetch_all(conn, "SELECT slot FROM lobby_players WHERE lobby_id=%s ORDER BY slot", (lobby["id"],))
        taken_slots = {int(r["slot"]) for r in taken}
        slot = next((s for s in range(int(lobby["max_players"])) if s not in taken_slots), None)
        if slot is None:
            raise HTTPException(status_code=409, detail="Lobby is full")

        execute(
            conn,
            "INSERT INTO lobby_players(lobby_id, user_id, slot, status) VALUES (%s, %s, %s, 'joined')",
            (str(lobby["id"]), user_id, slot),
        )
        return _get_lobby_with_players(conn, str(lobby["id"]))


@router.post(
    "/{lobby_id}/leave",
    response_model=MessageResponse,
    summary="Leave a lobby",
    description="Leaves a lobby. If the host leaves, lobby is closed.",
    operation_id="lobbies_leave",
)
# PUBLIC_INTERFACE
def leave_lobby(lobby_id: str, claims=Depends(get_current_user_claims)):
    """Leave lobby; close if host."""
    user_id = claims["sub"]
    with get_db() as conn:
        lobby = fetch_one(conn, "SELECT host_user_id, status FROM lobbies WHERE id=%s", (lobby_id,))
        if not lobby:
            raise HTTPException(status_code=404, detail="Lobby not found")
        if lobby["status"] != "open":
            raise HTTPException(status_code=409, detail="Lobby not open")

        execute(
            conn,
            "UPDATE lobby_players SET status='left', left_at=%s WHERE lobby_id=%s AND user_id=%s",
            (datetime.now(timezone.utc), lobby_id, user_id),
        )
        execute(conn, "DELETE FROM lobby_players WHERE lobby_id=%s AND user_id=%s", (lobby_id, user_id))

        if str(lobby["host_user_id"]) == user_id:
            execute(conn, "UPDATE lobbies SET status='closed' WHERE id=%s", (lobby_id,))
            execute(conn, "DELETE FROM lobby_players WHERE lobby_id=%s", (lobby_id,))
            return MessageResponse(message="Lobby closed (host left)")

        return MessageResponse(message="Left lobby")


@router.post(
    "/{lobby_id}/ready",
    response_model=LobbyPublic,
    summary="Set ready status",
    description="Marks current player ready; when all players ready and lobby is full, game can be started.",
    operation_id="lobbies_ready",
)
# PUBLIC_INTERFACE
def ready_up(lobby_id: str, claims=Depends(get_current_user_claims)):
    """Mark current user as ready in lobby."""
    user_id = claims["sub"]
    with get_db() as conn:
        lobby = fetch_one(conn, "SELECT id, status FROM lobbies WHERE id=%s", (lobby_id,))
        if not lobby:
            raise HTTPException(status_code=404, detail="Lobby not found")
        if lobby["status"] != "open":
            raise HTTPException(status_code=409, detail="Lobby not open")

        execute(
            conn,
            "UPDATE lobby_players SET status='ready', ready_at=%s WHERE lobby_id=%s AND user_id=%s",
            (datetime.now(timezone.utc), lobby_id, user_id),
        )
        return _get_lobby_with_players(conn, lobby_id)


@router.post(
    "/matchmake",
    response_model=LobbyPublic,
    summary="Matchmake into an open lobby",
    description="Finds an existing public open lobby with an open slot and joins it, otherwise creates a new lobby.",
    operation_id="lobbies_matchmake",
)
# PUBLIC_INTERFACE
def matchmake(payload: MatchmakeRequest, claims=Depends(get_current_user_claims)):
    """Very simple matchmaking: join newest available lobby or create one."""
    user_id = claims["sub"]
    with get_db() as conn:
        # If user already in an open lobby, return it.
        existing = fetch_one(
            conn,
            """
            SELECT l.id
            FROM lobbies l
            JOIN lobby_players lp ON lp.lobby_id = l.id
            WHERE lp.user_id=%s AND l.status='open'
            ORDER BY l.created_at DESC
            LIMIT 1
            """,
            (user_id,),
        )
        if existing:
            return _get_lobby_with_players(conn, str(existing["id"]))

        candidates = fetch_all(
            conn,
            """
            SELECT l.id, l.max_players
            FROM lobbies l
            WHERE l.status='open' AND l.is_private=false AND l.max_players=%s
            ORDER BY l.created_at DESC
            LIMIT 25
            """,
            (payload.max_players,),
        )
        for c in candidates:
            taken = fetch_all(conn, "SELECT slot FROM lobby_players WHERE lobby_id=%s", (c["id"],))
            if len(taken) < int(c["max_players"]):
                # join it
                taken_slots = {int(r["slot"]) for r in taken}
                slot = next((s for s in range(int(c["max_players"])) if s not in taken_slots), None)
                if slot is None:
                    continue
                execute(
                    conn,
                    "INSERT INTO lobby_players(lobby_id, user_id, slot, status) VALUES (%s, %s, %s, 'joined')",
                    (str(c["id"]), user_id, slot),
                )
                return _get_lobby_with_players(conn, str(c["id"]))

        # Create a new lobby
        # (use default name; frontend can rename later)
        code = _generate_code()
        for _ in range(10):
            if not fetch_one(conn, "SELECT 1 FROM lobbies WHERE code=%s", (code,)):
                break
            code = _generate_code()
        lobby = fetch_one(
            conn,
            """
            INSERT INTO lobbies(code, name, host_user_id, max_players, is_private)
            VALUES (%s, %s, %s, %s, false)
            RETURNING id
            """,
            (code, f"Match {code}", user_id, payload.max_players),
        )
        assert lobby is not None
        lobby_id = str(lobby["id"])
        execute(conn, "INSERT INTO lobby_players(lobby_id, user_id, slot, status) VALUES (%s, %s, 0, 'joined')", (lobby_id, user_id))
        return _get_lobby_with_players(conn, lobby_id)
