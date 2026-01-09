"""
FastAPI backend for the Tic Tac Toe application.

This service provides:
- Health check endpoint
- Basic game state persistence endpoints (SQLite-backed)

The React frontend may call this API using REACT_APP_BACKEND_URL (or REACT_APP_API_BASE).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, List, Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


def _utc_now_iso() -> str:
    """Return current UTC time as ISO string."""
    return datetime.now(timezone.utc).isoformat()


def _get_db_path() -> str:
    """
    Resolve the SQLite DB path.

    By default we use the database container's default file name (myapp.db),
    assuming containers share a workspace volume or the file is mounted into
    the backend container at runtime.
    """
    # Allow override if an orchestrator mounts the db elsewhere.
    return os.getenv("SQLITE_DB_PATH", "myapp.db")


def _get_conn() -> sqlite3.Connection:
    """Create a sqlite3 connection with row factory enabled."""
    conn = sqlite3.connect(_get_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    """Ensure required tables exist."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ttt_games (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              board TEXT NOT NULL,
              next_player TEXT NOT NULL,
              winner TEXT NULL,
              is_draw INTEGER NOT NULL DEFAULT 0,
              moves INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


openapi_tags = [
    {"name": "Health", "description": "Service health and basic diagnostics."},
    {"name": "TicTacToe", "description": "Tic Tac Toe game state persistence endpoints."},
]

app = FastAPI(
    title="Tic Tac Toe Backend API",
    description="Minimal backend API for saving and retrieving Tic Tac Toe game states.",
    version="0.1.0",
    openapi_tags=openapi_tags,
)

# CORS: allow configuration via env, fallback to wildcard (template-friendly).
allowed_origins_raw = os.getenv("ALLOWED_ORIGINS", "*")
allowed_origins = ["*"] if allowed_origins_raw.strip() == "*" else [o.strip() for o in allowed_origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_init_db()


# PUBLIC_INTERFACE
@app.get(
    "/",
    tags=["Health"],
    summary="Health check",
    description="Simple health check to verify the service is running.",
    operation_id="healthCheckRoot",
)
def health_check() -> dict:
    """Health check endpoint. Returns a simple JSON payload."""
    return {"message": "Healthy"}


class GameStateIn(BaseModel):
    """Incoming Tic Tac Toe game state (from frontend)."""

    board: List[Optional[Literal["X", "O"]]] = Field(..., description="Board values as 9-element array (X/O/null).")
    next_player: Literal["X", "O"] = Field(..., description="Player who should play next.")
    winner: Optional[Literal["X", "O"]] = Field(None, description="Winner if game is finished.")
    is_draw: bool = Field(False, description="Whether the game ended in a draw.")
    moves: int = Field(0, ge=0, le=9, description="Number of moves made.")


class GameStateOut(BaseModel):
    """Persisted Tic Tac Toe game state."""

    id: int = Field(..., description="Database id for this saved game state.")
    board: List[Optional[Literal["X", "O"]]] = Field(..., description="Board values as 9-element array (X/O/null).")
    next_player: Literal["X", "O"] = Field(..., description="Player who should play next.")
    winner: Optional[Literal["X", "O"]] = Field(None, description="Winner if game is finished.")
    is_draw: bool = Field(False, description="Whether the game ended in a draw.")
    moves: int = Field(0, ge=0, le=9, description="Number of moves made.")
    created_at: str = Field(..., description="ISO timestamp (UTC) when record was created.")
    updated_at: str = Field(..., description="ISO timestamp (UTC) when record was last updated.")


def _encode_board(board: List[Optional[str]]) -> str:
    """Encode board as compact string of 9 chars using '.' for null."""
    if len(board) != 9:
        raise ValueError("board must have length 9")
    return "".join([v if v in ("X", "O") else "." for v in board])


def _decode_board(s: str) -> List[Optional[Literal["X", "O"]]]:
    """Decode compact string representation back into board array."""
    if len(s) != 9:
        raise ValueError("board string must have length 9")
    out: List[Optional[Literal["X", "O"]]] = []
    for ch in s:
        if ch == ".":
            out.append(None)
        elif ch in ("X", "O"):
            out.append(ch)  # type: ignore[assignment]
        else:
            raise ValueError("invalid board encoding")
    return out


# PUBLIC_INTERFACE
@app.post(
    "/api/games",
    response_model=GameStateOut,
    tags=["TicTacToe"],
    summary="Save a game state",
    description="Stores the provided Tic Tac Toe state in SQLite and returns the saved record.",
    operation_id="saveGameState",
)
def save_game_state(payload: GameStateIn) -> GameStateOut:
    """Save a new game state snapshot to SQLite and return the persisted record."""
    if len(payload.board) != 9:
        raise HTTPException(status_code=400, detail="board must be a 9-element array")

    now = _utc_now_iso()
    board_encoded = _encode_board(payload.board)

    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO ttt_games (board, next_player, winner, is_draw, moves, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                board_encoded,
                payload.next_player,
                payload.winner,
                1 if payload.is_draw else 0,
                payload.moves,
                now,
                now,
            ),
        )
        conn.commit()

        game_id = int(cur.lastrowid)
        return GameStateOut(
            id=game_id,
            board=_decode_board(board_encoded),
            next_player=payload.next_player,
            winner=payload.winner,
            is_draw=payload.is_draw,
            moves=payload.moves,
            created_at=now,
            updated_at=now,
        )
    finally:
        conn.close()


# PUBLIC_INTERFACE
@app.get(
    "/api/games/latest",
    response_model=GameStateOut,
    tags=["TicTacToe"],
    summary="Get latest game state",
    description="Returns the most recently saved Tic Tac Toe game state.",
    operation_id="getLatestGameState",
)
def get_latest_game_state() -> GameStateOut:
    """Fetch the most recently saved game state; 404 if none exist."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM ttt_games ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No saved game state")

        try:
            board = _decode_board(row["board"])
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Corrupt board data: {e}") from e

        return GameStateOut(
            id=int(row["id"]),
            board=board,
            next_player=row["next_player"],
            winner=row["winner"],
            is_draw=bool(row["is_draw"]),
            moves=int(row["moves"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    finally:
        conn.close()


# PUBLIC_INTERFACE
@app.get(
    "/api/debug/db",
    tags=["Health"],
    summary="DB diagnostics",
    description="Returns basic information about the SQLite DB path used by the backend.",
    operation_id="dbDiagnostics",
)
def db_diagnostics() -> dict[str, Any]:
    """Return basic DB diagnostic info (file path, existence)."""
    db_path = _get_db_path()
    return {
        "sqlite_db_path": db_path,
        "exists": os.path.exists(db_path),
    }
