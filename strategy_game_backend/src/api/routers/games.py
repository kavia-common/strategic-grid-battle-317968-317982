from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException

from src.api.core.auth import get_current_user_claims
from src.api.core.config import get_settings
from src.api.core.db import execute, fetch_all, fetch_one, get_db
from src.api.models.schemas import (
    GameCreateFromLobbyResponse,
    GameStateResponse,
    MessageResponse,
    PlayerActionRequest,
    PlayerActionResponse,
    UnitState,
)

router = APIRouter(prefix="/games", tags=["games"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_game_players(conn, game_id: str) -> list[dict]:
    return fetch_all(
        conn,
        """
        SELECT user_id, player_index, team
        FROM game_players
        WHERE game_id=%s
        ORDER BY player_index ASC
        """,
        (game_id,),
    )


def _get_current_player_user_id(conn, game_id: str) -> str:
    game = fetch_one(conn, "SELECT current_player_index FROM games WHERE id=%s", (game_id,))
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    idx = int(game["current_player_index"])
    players = _load_game_players(conn, game_id)
    if idx < 0 or idx >= len(players):
        raise HTTPException(status_code=500, detail="Invalid current player index")
    return str(players[idx]["user_id"])


def _get_you_player_index(conn, game_id: str, user_id: str) -> int:
    row = fetch_one(conn, "SELECT player_index FROM game_players WHERE game_id=%s AND user_id=%s", (game_id, user_id))
    if not row:
        raise HTTPException(status_code=403, detail="You are not a player in this game")
    return int(row["player_index"])


def _ensure_turn_timer(conn, game_id: str) -> None:
    """If current turn expired, auto-end the turn."""
    game = fetch_one(conn, "SELECT status, turn_expires_at FROM games WHERE id=%s", (game_id,))
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    if game["status"] != "active":
        return
    if game["turn_expires_at"] is None:
        return
    if game["turn_expires_at"] <= _now():
        _end_turn(conn, game_id, reason="timeout")


def _start_game_if_ready(conn, lobby_id: str) -> str:
    """Create a game from a lobby if lobby is full and all ready."""
    lobby = fetch_one(conn, "SELECT id, status, max_players FROM lobbies WHERE id=%s", (lobby_id,))
    if not lobby:
        raise HTTPException(status_code=404, detail="Lobby not found")
    if lobby["status"] != "open":
        raise HTTPException(status_code=409, detail="Lobby not open")

    players = fetch_all(
        conn,
        "SELECT user_id, slot, status FROM lobby_players WHERE lobby_id=%s ORDER BY slot ASC",
        (lobby_id,),
    )
    if len(players) < int(lobby["max_players"]):
        raise HTTPException(status_code=409, detail="Lobby not full")
    if any(p["status"] != "ready" for p in players):
        raise HTTPException(status_code=409, detail="Not all players are ready")

    existing = fetch_one(conn, "SELECT id FROM games WHERE lobby_id=%s AND status IN ('waiting','active')", (lobby_id,))
    if existing:
        return str(existing["id"])

    settings = get_settings()
    # Create game
    game = fetch_one(
        conn,
        """
        INSERT INTO games(lobby_id, status, map_width, map_height, started_at, current_turn, current_player_index, turn_expires_at)
        VALUES (%s, 'active', 10, 10, %s, 1, 0, %s)
        RETURNING id
        """,
        (lobby_id, _now(), _now() + timedelta(seconds=settings.turn_time_limit_seconds)),
    )
    assert game is not None
    game_id = str(game["id"])

    # Create game_players with player_index based on lobby slot
    for p in players:
        execute(
            conn,
            "INSERT INTO game_players(game_id, user_id, player_index, team) VALUES (%s, %s, %s, %s)",
            (game_id, str(p["user_id"]), int(p["slot"]), int(p["slot"])),
        )

    # Spawn simple initial units: 1 unit per player
    # Player 0 at (0,0), Player 1 at (9,9), Player 2 at (0,9), Player 3 at (9,0)
    spawns = [(0, 0), (9, 9), (0, 9), (9, 0)]
    for p in players:
        idx = int(p["slot"])
        x, y = spawns[idx]
        execute(
            conn,
            """
            INSERT INTO units(game_id, owner_user_id, unit_type, name, x, y, hp, max_hp, attack, defense, movement, range)
            VALUES (%s, %s, 'soldier', %s, %s, %s, 10, 10, 3, 1, 3, 1)
            """,
            (game_id, str(p["user_id"]), f"Soldier {idx+1}", x, y),
        )

    # Create a turn record
    execute(
        conn,
        """
        INSERT INTO turns(game_id, turn_number, player_index, started_at, time_limit_seconds)
        VALUES (%s, 1, 0, %s, %s)
        """,
        (game_id, _now(), settings.turn_time_limit_seconds),
    )

    # Mark lobby as in_game
    execute(conn, "UPDATE lobbies SET status='in_game' WHERE id=%s", (lobby_id,))
    return game_id


def _serialize_state(conn, game_id: str, user_id: str) -> GameStateResponse:
    game = fetch_one(
        conn,
        """
        SELECT id, status, map_width, map_height, current_turn, current_player_index, turn_expires_at
        FROM games
        WHERE id=%s
        """,
        (game_id,),
    )
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    players = _load_game_players(conn, game_id)
    if not players:
        raise HTTPException(status_code=500, detail="Game has no players")

    you_idx = _get_you_player_index(conn, game_id, user_id)
    current_idx = int(game["current_player_index"])
    if current_idx < 0 or current_idx >= len(players):
        raise HTTPException(status_code=500, detail="Invalid current player index")

    units_rows = fetch_all(
        conn,
        """
        SELECT id, owner_user_id, unit_type, name, x, y, hp, max_hp, attack, defense, movement, range, is_alive
        FROM units
        WHERE game_id=%s
        ORDER BY created_at ASC
        """,
        (game_id,),
    )
    units = [
        UnitState(
            id=str(u["id"]),
            owner_user_id=str(u["owner_user_id"]),
            unit_type=u["unit_type"],
            name=u["name"],
            x=int(u["x"]),
            y=int(u["y"]),
            hp=int(u["hp"]),
            max_hp=int(u["max_hp"]),
            attack=int(u["attack"]),
            defense=int(u["defense"]),
            movement=int(u["movement"]),
            range=int(u["range"]),
            is_alive=bool(u["is_alive"]),
        )
        for u in units_rows
    ]

    return GameStateResponse(
        game_id=str(game["id"]),
        status=game["status"],
        map_width=int(game["map_width"]),
        map_height=int(game["map_height"]),
        current_turn=int(game["current_turn"]),
        current_player_index=current_idx,
        current_player_user_id=str(players[current_idx]["user_id"]),
        turn_expires_at=game["turn_expires_at"],
        you_are_player_index=you_idx,
        units=units,
    )


def _end_turn(conn, game_id: str, reason: str) -> None:
    settings = get_settings()
    game = fetch_one(conn, "SELECT current_turn, current_player_index FROM games WHERE id=%s", (game_id,))
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    cur_turn = int(game["current_turn"])
    cur_idx = int(game["current_player_index"])
    players = _load_game_players(conn, game_id)
    if not players:
        raise HTTPException(status_code=500, detail="No players")

    # Close current turn record if exists
    execute(
        conn,
        """
        UPDATE turns
        SET ended_at=%s, ended_reason=%s
        WHERE game_id=%s AND turn_number=%s AND player_index=%s AND ended_at IS NULL
        """,
        (_now(), reason, game_id, cur_turn, cur_idx),
    )

    next_idx = (cur_idx + 1) % len(players)
    next_turn = cur_turn + 1 if next_idx == 0 else cur_turn

    # Advance game
    execute(
        conn,
        """
        UPDATE games
        SET current_player_index=%s,
            current_turn=%s,
            turn_expires_at=%s
        WHERE id=%s
        """,
        (next_idx, next_turn, _now() + timedelta(seconds=settings.turn_time_limit_seconds), game_id),
    )

    # Create next turn record if starting new player segment
    execute(
        conn,
        """
        INSERT INTO turns(game_id, turn_number, player_index, started_at, time_limit_seconds)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (game_id, turn_number, player_index) DO NOTHING
        """,
        (game_id, next_turn, next_idx, _now(), settings.turn_time_limit_seconds),
    )


def _check_win_condition(conn, game_id: str) -> str | None:
    alive_by_owner = fetch_all(
        conn,
        """
        SELECT owner_user_id, COUNT(*) AS alive
        FROM units
        WHERE game_id=%s AND is_alive=true
        GROUP BY owner_user_id
        """,
        (game_id,),
    )
    owners = [str(r["owner_user_id"]) for r in alive_by_owner if int(r["alive"]) > 0]
    if len(owners) == 1:
        return owners[0]
    return None


@router.post(
    "/from-lobby/{lobby_id}",
    response_model=GameCreateFromLobbyResponse,
    summary="Start game from lobby",
    description="If lobby is full and all ready, creates a game and spawns initial units.",
    operation_id="games_start_from_lobby",
)
# PUBLIC_INTERFACE
def start_from_lobby(lobby_id: str, claims=Depends(get_current_user_claims)):
    """Start a game from a lobby when ready."""
    _ = claims["sub"]
    with get_db() as conn:
        game_id = _start_game_if_ready(conn, lobby_id)
        return GameCreateFromLobbyResponse(game_id=game_id, lobby_id=lobby_id, status="active")


@router.get(
    "/{game_id}/state",
    response_model=GameStateResponse,
    summary="Get game state",
    description="Returns current game state; auto-advances turn if timer expired.",
    operation_id="games_get_state",
)
# PUBLIC_INTERFACE
def get_state(game_id: str, claims=Depends(get_current_user_claims)):
    """Get current state snapshot."""
    user_id = claims["sub"]
    with get_db() as conn:
        _ensure_turn_timer(conn, game_id)
        return _serialize_state(conn, game_id, user_id)


@router.post(
    "/{game_id}/leave",
    response_model=MessageResponse,
    summary="Leave game",
    description="Marks game as abandoned if a player leaves while active.",
    operation_id="games_leave",
)
# PUBLIC_INTERFACE
def leave_game(game_id: str, claims=Depends(get_current_user_claims)):
    """Leave game (simplified)."""
    user_id = claims["sub"]
    with get_db() as conn:
        _ = _get_you_player_index(conn, game_id, user_id)
        game = fetch_one(conn, "SELECT status FROM games WHERE id=%s", (game_id,))
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")

        if game["status"] in ("finished", "abandoned"):
            return MessageResponse(message="Game already ended")

        execute(conn, "UPDATE games SET status='abandoned', finished_at=%s, result_reason=%s WHERE id=%s", (_now(), "player_left", game_id))
        return MessageResponse(message="Game abandoned")


@router.post(
    "/{game_id}/action",
    response_model=PlayerActionResponse,
    summary="Submit a player action",
    description="Validates and applies a player action (move, attack, end_turn, surrender). Enforces turn timer and turn ownership.",
    operation_id="games_action",
)
# PUBLIC_INTERFACE
def submit_action(game_id: str, payload: PlayerActionRequest, claims=Depends(get_current_user_claims)):
    """Validate and apply a player action to the game."""
    user_id = claims["sub"]
    with get_db() as conn:
        _ensure_turn_timer(conn, game_id)

        game = fetch_one(conn, "SELECT status FROM games WHERE id=%s", (game_id,))
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")
        if game["status"] != "active":
            raise HTTPException(status_code=409, detail=f"Game not active (status={game['status']})")

        current_user_id = _get_current_player_user_id(conn, game_id)
        if current_user_id != user_id:
            raise HTTPException(status_code=409, detail="Not your turn")

        # Validate actions
        if payload.action_type in ("move", "attack") and not payload.actor_unit_id:
            raise HTTPException(status_code=422, detail="actor_unit_id required")

        # Load actor unit, ensure ownership & alive
        actor_unit = None
        if payload.actor_unit_id:
            actor_unit = fetch_one(
                conn,
                """
                SELECT id, owner_user_id, x, y, movement, range, attack, defense, hp, is_alive
                FROM units
                WHERE id=%s AND game_id=%s
                """,
                (payload.actor_unit_id, game_id),
            )
            if not actor_unit:
                raise HTTPException(status_code=404, detail="Actor unit not found")
            if str(actor_unit["owner_user_id"]) != user_id:
                raise HTTPException(status_code=403, detail="You do not control this unit")
            if not actor_unit["is_alive"]:
                raise HTTPException(status_code=409, detail="Unit is dead")

        accepted = False
        message = "No-op"

        if payload.action_type == "end_turn":
            accepted = True
            message = "Turn ended"
            execute(
                conn,
                "INSERT INTO actions(game_id, action_type, actor_user_id, actor_unit_id, payload) VALUES (%s, 'end_turn', %s, NULL, %s::jsonb)",
                (game_id, user_id, "{}"),
            )
            _end_turn(conn, game_id, reason="end_turn")

        elif payload.action_type == "surrender":
            accepted = True
            message = "Surrendered"
            execute(
                conn,
                "INSERT INTO actions(game_id, action_type, actor_user_id, actor_unit_id, payload) VALUES (%s, 'surrender', %s, NULL, %s::jsonb)",
                (game_id, user_id, "{}"),
            )
            # Other player wins if any
            players = _load_game_players(conn, game_id)
            winner = next((str(p["user_id"]) for p in players if str(p["user_id"]) != user_id), None)
            execute(
                conn,
                "UPDATE games SET status='finished', finished_at=%s, winning_user_id=%s, result_reason=%s WHERE id=%s",
                (_now(), winner, "surrender", game_id),
            )

        elif payload.action_type == "move":
            if payload.target_x is None or payload.target_y is None:
                raise HTTPException(status_code=422, detail="target_x and target_y required for move")
            dx = abs(int(payload.target_x) - int(actor_unit["x"]))
            dy = abs(int(payload.target_y) - int(actor_unit["y"]))
            dist = dx + dy
            if dist <= 0:
                raise HTTPException(status_code=409, detail="Move must change position")
            if dist > int(actor_unit["movement"]):
                raise HTTPException(status_code=409, detail="Move exceeds unit movement")

            # Ensure tile empty (alive unit)
            occupied = fetch_one(
                conn,
                "SELECT id FROM units WHERE game_id=%s AND x=%s AND y=%s AND is_alive=true",
                (game_id, int(payload.target_x), int(payload.target_y)),
            )
            if occupied:
                raise HTTPException(status_code=409, detail="Tile occupied")

            execute(
                conn,
                "UPDATE units SET x=%s, y=%s WHERE id=%s",
                (int(payload.target_x), int(payload.target_y), str(actor_unit["id"])),
            )
            accepted = True
            message = "Moved"
            execute(
                conn,
                "INSERT INTO actions(game_id, action_type, actor_user_id, actor_unit_id, payload) VALUES (%s, 'move', %s, %s, %s::jsonb)",
                (game_id, user_id, str(actor_unit["id"]), payload.model_dump_json()),
            )

        elif payload.action_type == "attack":
            if not payload.target_unit_id and (payload.target_x is None or payload.target_y is None):
                raise HTTPException(status_code=422, detail="target_unit_id or (target_x,target_y) required for attack")

            target_unit = None
            if payload.target_unit_id:
                target_unit = fetch_one(
                    conn,
                    "SELECT id, owner_user_id, x, y, hp, defense, is_alive FROM units WHERE id=%s AND game_id=%s",
                    (payload.target_unit_id, game_id),
                )
            else:
                target_unit = fetch_one(
                    conn,
                    "SELECT id, owner_user_id, x, y, hp, defense, is_alive FROM units WHERE game_id=%s AND x=%s AND y=%s AND is_alive=true",
                    (game_id, int(payload.target_x), int(payload.target_y)),
                )

            if not target_unit or not target_unit["is_alive"]:
                raise HTTPException(status_code=404, detail="Target not found")
            if str(target_unit["owner_user_id"]) == user_id:
                raise HTTPException(status_code=409, detail="Cannot attack your own unit")

            dx = abs(int(target_unit["x"]) - int(actor_unit["x"]))
            dy = abs(int(target_unit["y"]) - int(actor_unit["y"]))
            dist = dx + dy
            if dist > int(actor_unit["range"]):
                raise HTTPException(status_code=409, detail="Target out of range")

            damage = max(1, int(actor_unit["attack"]) - int(target_unit["defense"]))
            new_hp = int(target_unit["hp"]) - damage
            if new_hp <= 0:
                execute(conn, "UPDATE units SET hp=0, is_alive=false WHERE id=%s", (str(target_unit["id"]),))
            else:
                execute(conn, "UPDATE units SET hp=%s WHERE id=%s", (new_hp, str(target_unit["id"])))

            accepted = True
            message = f"Attacked for {damage}"
            execute(
                conn,
                "INSERT INTO actions(game_id, action_type, actor_user_id, actor_unit_id, payload) VALUES (%s, 'attack', %s, %s, %s::jsonb)",
                (game_id, user_id, str(actor_unit["id"]), payload.model_dump_json()),
            )

        # Check win condition after action
        winner = _check_win_condition(conn, game_id)
        game_finished = False
        if winner:
            game_finished = True
            execute(
                conn,
                "UPDATE games SET status='finished', finished_at=%s, winning_user_id=%s, result_reason=%s WHERE id=%s",
                (_now(), winner, "all_units_destroyed", game_id),
            )

        state = _serialize_state(conn, game_id, user_id)
        return PlayerActionResponse(
            accepted=accepted,
            message=message,
            state=state,
            game_finished=game_finished or state.status == "finished",
            winner_user_id=winner,
        )
