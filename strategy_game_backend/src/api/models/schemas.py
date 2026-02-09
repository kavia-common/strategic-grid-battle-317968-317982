from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class MessageResponse(BaseModel):
    message: str = Field(..., description="Human-readable status message")


class TokenResponse(BaseModel):
    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field("bearer", description="Token type (always 'bearer')")
    user: "UserPublic" = Field(..., description="Authenticated user")


class RegisterRequest(BaseModel):
    email: str = Field(..., description="User email address")
    username: str = Field(..., min_length=3, max_length=32, description="Unique username")
    password: str = Field(..., min_length=6, max_length=128, description="Account password")
    display_name: str | None = Field(None, description="Optional display name")


class LoginRequest(BaseModel):
    username_or_email: str = Field(..., description="Username or email")
    password: str = Field(..., description="Account password")


class UserPublic(BaseModel):
    id: str = Field(..., description="User UUID")
    email: str = Field(..., description="User email")
    username: str = Field(..., description="User username")
    display_name: str | None = Field(None, description="Display name")


class LobbyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, description="Lobby name")
    max_players: int = Field(2, ge=2, le=4, description="Max players (2-4 supported)")
    is_private: bool = Field(False, description="Private lobby not shown in public list")


class LobbyJoinRequest(BaseModel):
    code: str = Field(..., min_length=4, max_length=16, description="Lobby join code")


class LobbyPlayer(BaseModel):
    user: UserPublic = Field(..., description="Player user")
    slot: int = Field(..., ge=0, description="Slot index")
    status: str = Field(..., description="Player status in lobby")
    joined_at: datetime = Field(..., description="Join timestamp")
    ready_at: datetime | None = Field(None, description="Ready timestamp")


class LobbyPublic(BaseModel):
    id: str = Field(..., description="Lobby UUID")
    code: str = Field(..., description="Short lobby code used to join")
    name: str = Field(..., description="Lobby name")
    status: str = Field(..., description="Lobby status")
    max_players: int = Field(..., description="Maximum players")
    is_private: bool = Field(..., description="Whether lobby is private")
    host_user_id: str = Field(..., description="Host user UUID")
    created_at: datetime = Field(..., description="Created timestamp")
    players: list[LobbyPlayer] = Field(default_factory=list, description="Players in lobby")


class MatchmakeRequest(BaseModel):
    max_players: int = Field(2, ge=2, le=4, description="Desired match size")
    allow_private: bool = Field(False, description="Whether to allow joining private lobbies (generally false)")


class GameCreateFromLobbyResponse(BaseModel):
    game_id: str = Field(..., description="Created game UUID")
    lobby_id: str = Field(..., description="Lobby UUID")
    status: str = Field(..., description="Game status")


class UnitState(BaseModel):
    id: str
    owner_user_id: str
    unit_type: str
    name: str | None = None
    x: int
    y: int
    hp: int
    max_hp: int
    attack: int
    defense: int
    movement: int
    range: int
    is_alive: bool


class GameStateResponse(BaseModel):
    game_id: str = Field(..., description="Game UUID")
    status: str = Field(..., description="Game status")
    map_width: int = Field(..., description="Map width")
    map_height: int = Field(..., description="Map height")
    current_turn: int = Field(..., description="Turn number")
    current_player_index: int = Field(..., description="Index of player whose turn it is")
    current_player_user_id: str = Field(..., description="User UUID of current player")
    turn_expires_at: datetime | None = Field(None, description="When current turn expires")
    you_are_player_index: int = Field(..., description="Current user's player index")
    units: list[UnitState] = Field(default_factory=list, description="Units on board")


ActionType = Literal["move", "attack", "end_turn", "surrender"]


class PlayerActionRequest(BaseModel):
    action_type: ActionType = Field(..., description="Type of action")
    actor_unit_id: str | None = Field(None, description="Unit performing the action (if applicable)")
    target_x: int | None = Field(None, description="Target X coordinate (if applicable)")
    target_y: int | None = Field(None, description="Target Y coordinate (if applicable)")
    target_unit_id: str | None = Field(None, description="Target unit (for attack)")
    payload: dict[str, Any] = Field(default_factory=dict, description="Additional action data")


class PlayerActionResponse(BaseModel):
    accepted: bool = Field(..., description="Whether action was applied")
    message: str = Field(..., description="Result message")
    state: GameStateResponse = Field(..., description="Updated game state")
    game_finished: bool = Field(False, description="True if game finished due to this action")
    winner_user_id: str | None = Field(None, description="Winner user id, if finished")


TokenResponse.model_rebuild()
LobbyPlayer.model_rebuild()
LobbyPublic.model_rebuild()
