"""
Club MMO: FastAPI + sessions + SQLite users + WebSockets + sprite uploads.
World background: static/bg/world.png or world.jpg (see /config).

Demo only: passwords are stored in SQLite as plain text. Never use that on a real site.
"""

from __future__ import annotations

import json
import asyncio
import io
import math
import random
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket
from starlette.websockets import WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from app.sprite_trim import trim_sprite_empty_space_to_png, trim_sprite_white_margin_to_png

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
ARCHIVE_WORLD = DATA_DIR / "archives" / "world"
ARCHIVE_SPRITES = DATA_DIR / "archives" / "sprites"
DB_PATH = DATA_DIR / "game.db"
STATIC_DIR = BASE_DIR / "static"
WORLD_BG_PNG = STATIC_DIR / "bg" / "world.png"
WORLD_BG_JPG = STATIC_DIR / "bg" / "world.jpg"
SFX_DIR = STATIC_DIR / "sfx"
MUSIC_DIR = STATIC_DIR / "music"

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_AUDIO_UPLOAD_BYTES = 15 * 1024 * 1024
SFX_ALLOWED_SUFFIXES = frozenset({".wav", ".ogg", ".mp3", ".m4a", ".webm", ".flac"})
SPRITE_NAME_RE = re.compile(r"^[a-f0-9]{32}\.(png|jpg|jpeg|gif|webp)$")

NPCS: list[dict] = []
pets_runtime: dict[int, dict] = {}
coin_piles: dict[str, dict] = {}
pet_loop_task: asyncio.Task | None = None

players: dict[str, dict] = {}
world_obstacles: dict[int, list[dict]] = {}

PET_MAX_HEALTH = 100
PLAYER_MAX_HEALTH = 100
PLAYER_MAX_HEALTH_CAP = 500
MAX_HP_BOOST_AMOUNT = 25
MAGICOIN_COST_MAX_HP_BOOST = 50
PLAYER_ATTACK_DAMAGE = 20
PLAYER_ATTACK_REACH_PX = 110.0
PLAYER_ATTACK_VERTICAL_PX = 70.0
PET_ATTACK_DAMAGE = 15
PLAYER_RESPAWN_SECONDS = 5.0
PET_RESPAWN_MIN_SECONDS = 3.0
PET_RESPAWN_MAX_SECONDS = 7.0
PET_OFFSCREEN_GRACE_SECONDS = 4.0

MAGICOIN_DEFAULT = 100
MAGICOIN_COST_ENTER_WORLD = 10
MAGICOIN_COST_CREATE_WORLD = 250
MAGICOIN_COST_TRANSFORM = 10
MAGICOIN_COST_PET_TRANSFORM = 250
MAGICOIN_COST_HEALTH_POTION = 20
CAPTURE_RELEASE_MIN_MAGICOIN = 10
CAPTURE_RELEASE_MAX_MAGICOIN = 150
CAPTURE_RELEASE_SHINY_MULT = 5
MAGICOIN_QUIZ_REWARD_MIN = 5
MAGICOIN_QUIZ_REWARD_MAX = 25

COIN_PILE_SIZE_PX = 130
COIN_PILE_MIN_AMOUNT = 10
COIN_PILE_MAX_AMOUNT = 100
COIN_PILE_PICKUP_DELAY_S = 0.7
COIN_PILE_LIFETIME_S = 60.0

SESSION_COOKIE = "mmo_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30


def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_users_password_column(conn: sqlite3.Connection) -> None:
    """Rename legacy password_hash column to password (demo plaintext)."""
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "password" in cols:
        return
    if "password_hash" in cols:
        conn.execute("ALTER TABLE users RENAME COLUMN password_hash TO password")


def _migrate_users_sprite_flipped(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "sprite_flipped" in cols:
        return
    conn.execute(
        "ALTER TABLE users ADD COLUMN sprite_flipped INTEGER NOT NULL DEFAULT 0"
    )


def _migrate_users_shout_greeting(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "shout_greeting" in cols:
        return
    conn.execute(
        "ALTER TABLE users ADD COLUMN shout_greeting TEXT NOT NULL DEFAULT 'Hello!'"
    )


def _migrate_users_sprite_width_px(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "sprite_width_px" in cols:
        return
    conn.execute("ALTER TABLE users ADD COLUMN sprite_width_px INTEGER NOT NULL DEFAULT 48")


def _migrate_users_pos_and_facing(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "pos_x" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN pos_x REAL NOT NULL DEFAULT 400")
    if "pos_y" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN pos_y REAL NOT NULL DEFAULT 300")
    if "facing_h" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN facing_h INTEGER NOT NULL DEFAULT 1")


def _migrate_users_attack_sprite(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "attack_sprite_file" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN attack_sprite_file TEXT NOT NULL DEFAULT ''")
    if "attack_sprite_flipped" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN attack_sprite_flipped INTEGER NOT NULL DEFAULT 0"
        )


def _migrate_captures_attack_sprite(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='captures'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(captures)").fetchall()}
    if "attack_sprite_file" not in cols:
        conn.execute(
            "ALTER TABLE captures ADD COLUMN attack_sprite_file TEXT NOT NULL DEFAULT ''"
        )


def _migrate_users_correct_answers(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "correct_answers" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN correct_answers INTEGER NOT NULL DEFAULT 0"
        )


def _migrate_pets_size_speed(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pets'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(pets)").fetchall()}
    if "size_px" not in cols:
        conn.execute("ALTER TABLE pets ADD COLUMN size_px INTEGER NOT NULL DEFAULT 52")
    if "speed_px" not in cols:
        conn.execute("ALTER TABLE pets ADD COLUMN speed_px REAL NOT NULL DEFAULT 1.0")


def _migrate_pets_code_mode(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pets'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(pets)").fetchall()}
    if "code_mode" not in cols:
        conn.execute("ALTER TABLE pets ADD COLUMN code_mode TEXT NOT NULL DEFAULT 'default'")


def _migrate_pets_world_id(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pets'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(pets)").fetchall()}
    if "world_id" not in cols:
        conn.execute("ALTER TABLE pets ADD COLUMN world_id INTEGER NOT NULL DEFAULT 1")


def _migrate_pets_attack_sfx(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pets'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(pets)").fetchall()}
    if "attack_sfx_file" not in cols:
        conn.execute(
            "ALTER TABLE pets ADD COLUMN attack_sfx_file TEXT NOT NULL DEFAULT ''"
        )
    if "attack_sfx_volume_pct" not in cols:
        conn.execute(
            "ALTER TABLE pets ADD COLUMN attack_sfx_volume_pct INTEGER NOT NULL DEFAULT 100"
        )


def _migrate_pets_instance_name(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pets'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(pets)").fetchall()}
    if "instance_name" not in cols:
        conn.execute(
            "ALTER TABLE pets ADD COLUMN instance_name TEXT NOT NULL DEFAULT ''"
        )
    if "shiny" not in cols:
        conn.execute(
            "ALTER TABLE pets ADD COLUMN shiny INTEGER NOT NULL DEFAULT 0"
        )


def _migrate_captures_instance_name(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='captures'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(captures)").fetchall()}
    if "instance_name" not in cols:
        conn.execute(
            "ALTER TABLE captures ADD COLUMN instance_name TEXT NOT NULL DEFAULT ''"
        )
    if "shiny" not in cols:
        conn.execute(
            "ALTER TABLE captures ADD COLUMN shiny INTEGER NOT NULL DEFAULT 0"
        )
    if "release_base_magicoin" not in cols:
        conn.execute(
            "ALTER TABLE captures ADD COLUMN release_base_magicoin INTEGER NOT NULL DEFAULT 0"
        )
    conn.execute(
        """
        UPDATE captures
        SET release_base_magicoin = (ABS(RANDOM()) % (? - ? + 1)) + ?
        WHERE COALESCE(release_base_magicoin, 0) <= 0
        """,
        (
            CAPTURE_RELEASE_MAX_MAGICOIN,
            CAPTURE_RELEASE_MIN_MAGICOIN,
            CAPTURE_RELEASE_MIN_MAGICOIN,
        ),
    )


def _seed_missing_world_pet_types(conn: sqlite3.Connection) -> None:
    worlds = conn.execute("SELECT id FROM worlds ORDER BY id").fetchall()
    now = time.time()
    for wr in worlds:
        wid = int(wr[0])
        exists = conn.execute(
            "SELECT 1 FROM world_pet_types WHERE world_id = ? LIMIT 1",
            (wid,),
        ).fetchone()
        if exists:
            continue
        rows = conn.execute(
            """
            SELECT id
            FROM pets
            WHERE world_id = ? AND COALESCE(world_screen, 1) = 1
            ORDER BY id
            LIMIT 5
            """,
            (wid,),
        ).fetchall()
        for r in rows:
            conn.execute(
                """
                INSERT INTO world_pet_types (world_id, template_pet_id, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(world_id, template_pet_id) DO NOTHING
                """,
                (wid, int(r[0]), now),
            )


def _migrate_users_current_world(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "current_world_id" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN current_world_id INTEGER NOT NULL DEFAULT 1"
        )
    if "current_world_screen" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN current_world_screen INTEGER NOT NULL DEFAULT 1"
        )


def _migrate_pets_world_screen(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pets'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(pets)").fetchall()}
    if "world_screen" not in cols:
        conn.execute("ALTER TABLE pets ADD COLUMN world_screen INTEGER NOT NULL DEFAULT 1")


def _migrate_world_obstacles_world_screen(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='world_obstacles'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(world_obstacles)").fetchall()}
    if "world_screen" not in cols:
        conn.execute(
            "ALTER TABLE world_obstacles ADD COLUMN world_screen INTEGER NOT NULL DEFAULT 1"
        )


def _migrate_users_magicoin(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "magicoin" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN magicoin INTEGER NOT NULL DEFAULT 100"
        )


def _migrate_users_health(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "health" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN health INTEGER NOT NULL DEFAULT 100")
    if "max_health" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN max_health INTEGER NOT NULL DEFAULT 100"
        )


def _migrate_users_attack_sfx(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "attack_sfx_file" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN attack_sfx_file TEXT NOT NULL DEFAULT ''"
        )
    if "attack_sfx_volume_pct" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN attack_sfx_volume_pct INTEGER NOT NULL DEFAULT 100"
        )


def _migrate_users_transformed_shiny(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "transformed_shiny" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN transformed_shiny INTEGER NOT NULL DEFAULT 0"
        )


def _migrate_worlds_music_file(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='worlds'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(worlds)").fetchall()}
    if "music_file" not in cols:
        conn.execute(
            "ALTER TABLE worlds ADD COLUMN music_file TEXT NOT NULL DEFAULT ''"
        )


def _migrate_worlds_music_volume(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='worlds'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(worlds)").fetchall()}
    if "music_volume_pct" not in cols:
        conn.execute(
            "ALTER TABLE worlds ADD COLUMN music_volume_pct INTEGER NOT NULL DEFAULT 100"
        )


def _migrate_worlds_created_by_user_id(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='worlds'"
    ).fetchone():
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(worlds)").fetchall()}
    if "created_by_user_id" not in cols:
        conn.execute(
            "ALTER TABLE worlds ADD COLUMN created_by_user_id INTEGER NOT NULL DEFAULT 0"
        )


def _migrate_world_obstacle_configs(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS world_obstacle_configs (
            world_id INTEGER PRIMARY KEY,
            sprite_file TEXT NOT NULL DEFAULT '',
            sprite_flipped INTEGER NOT NULL DEFAULT 0,
            min_width_px INTEGER NOT NULL DEFAULT 80,
            max_width_px INTEGER NOT NULL DEFAULT 180,
            obstacle_count INTEGER NOT NULL DEFAULT 6
        );
        """
    )


def _migrate_world_obstacles(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS world_obstacles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            world_id INTEGER NOT NULL,
            sprite_file TEXT NOT NULL DEFAULT '',
            sprite_flipped INTEGER NOT NULL DEFAULT 0,
            x REAL NOT NULL DEFAULT 680,
            y REAL NOT NULL DEFAULT 380,
            width_px INTEGER NOT NULL DEFAULT 120,
            created_at REAL NOT NULL DEFAULT 0
        );
        """
    )


def _seed_default_world(conn: sqlite3.Connection) -> None:
    """Ensure world #1 exists and inherits any legacy single-file background."""
    row = conn.execute("SELECT id FROM worlds WHERE id = 1").fetchone()
    if row is not None:
        return
    bg_filename = ""
    bg_dir = STATIC_DIR / "bg"
    bg_dir.mkdir(parents=True, exist_ok=True)
    if WORLD_BG_PNG.is_file():
        bg_filename = "world_1.png"
        try:
            WORLD_BG_PNG.replace(bg_dir / bg_filename)
        except OSError:
            bg_filename = ""
    elif WORLD_BG_JPG.is_file():
        bg_filename = "world_1.jpg"
        try:
            WORLD_BG_JPG.replace(bg_dir / bg_filename)
        except OSError:
            bg_filename = ""
    conn.execute(
        """
        INSERT INTO worlds (id, name, background_file, created_at)
        VALUES (1, 'Default', ?, ?)
        """,
        (bg_filename, time.time()),
    )


def normalize_shout_greeting(raw: str | None) -> str:
    text = (raw or "").strip() or "Hello!"
    return text[:64]


def normalize_sprite_width_px(raw: int | float | str | None) -> int:
    try:
        w = int(float(raw if raw is not None else 48))
    except (TypeError, ValueError):
        w = 48
    return max(16, min(512, w))


def normalize_facing_h(raw: int | float | str | None) -> int:
    try:
        n = float(raw if raw is not None else 1)
    except (TypeError, ValueError):
        return 1
    return -1 if n < 0 else 1


def normalize_attack_sprite_file(raw: str | None) -> str:
    name = (raw or "").strip()
    if not name:
        return ""
    return name if SPRITE_NAME_RE.match(name) else ""


def normalize_pet_size_px(raw: int | float | str | None) -> int:
    try:
        n = int(float(raw if raw is not None else 52))
    except (TypeError, ValueError):
        n = 52
    return max(24, min(256, n))


def normalize_pet_speed_px(raw: int | float | str | None) -> float:
    try:
        n = float(raw if raw is not None else 1.0)
    except (TypeError, ValueError):
        n = 1.0
    return max(0.2, min(4.0, n))


def normalize_pet_ball_size_px(raw: int | float | str | None) -> int:
    try:
        n = int(float(raw if raw is not None else 28))
    except (TypeError, ValueError):
        n = 28
    return max(8, min(128, n))


def normalize_volume(v: int | float | str | None, default: int = 35) -> int:
    try:
        n = int(float(v if v is not None else default))
    except (TypeError, ValueError):
        n = default
    return max(0, min(100, n))


def normalize_obstacle_width_px(raw: int | float | str | None, default: int = 120) -> int:
    try:
        n = int(float(raw if raw is not None else default))
    except (TypeError, ValueError):
        n = default
    return max(24, min(640, n))


def normalize_obstacle_count(raw: int | float | str | None, default: int = 6) -> int:
    try:
        n = int(float(raw if raw is not None else default))
    except (TypeError, ValueError):
        n = default
    return max(0, min(64, n))


def normalize_world_pet_target_count(raw: int | float | str | None, default: int = 5) -> int:
    try:
        n = int(float(raw if raw is not None else default))
    except (TypeError, ValueError):
        n = default
    return max(0, min(5, n))


EVENT_SFX_KEYS = (
    "world_enter",
    "shop",
    "magicoin",
    "pet_defeat",
    "player_death",
    "quiz_correct",
    "quiz_wrong",
    "world_change",
)

SCREEN_MUSIC_KEYS = ("backpack", "worlds", "config", "login")


def normalize_screen_music_filename(raw: str | None) -> str:
    name = (raw or "").strip()
    if not name:
        return ""
    return name if name in _list_world_music_filenames() else ""


def get_screen_music_file(key: str) -> str:
    return normalize_screen_music_filename(get_meta_text(f"screen_music_{key}_file", ""))


def get_screen_music_volume_pct(key: str) -> int:
    return get_meta_int(f"screen_music_{key}_vol", 100, 0, 100)


def get_screen_music_config() -> dict[str, dict[str, str | int]]:
    out: dict[str, dict[str, str | int]] = {}
    for k in SCREEN_MUSIC_KEYS:
        f = get_screen_music_file(k)
        out[k] = {
            "file": f,
            "url": f"/music/{f}" if f else "",
            "vol": get_screen_music_volume_pct(k),
        }
    return out


def set_screen_music_pair(
    key: str,
    filename: str | None,
    vol_pct: int | float | str | None,
) -> None:
    if key not in SCREEN_MUSIC_KEYS:
        raise ValueError("bad_screen_key")
    raw_name = normalize_screen_music_filename(filename or "")
    set_meta_value(f"screen_music_{key}_file", raw_name)
    try:
        v = int(float(vol_pct if vol_pct is not None else 100))
    except (TypeError, ValueError):
        v = 100
    set_meta_value(f"screen_music_{key}_vol", str(max(0, min(100, v))))


def normalize_event_sfx_filename(raw: str | None) -> str:
    name = (raw or "").strip()
    if not name:
        return ""
    return name if name in _list_sfx_filenames() else ""


def get_event_sfx_file(key: str) -> str:
    name = get_meta_text(f"sfx_evt_{key}_file", "")
    return normalize_event_sfx_filename(name)


def get_event_sfx_volume_pct(key: str) -> int:
    return get_meta_int(f"sfx_evt_{key}_vol", 100, 0, 100)


def get_event_sfx_config() -> dict[str, dict[str, str | int]]:
    return {
        k: {"file": get_event_sfx_file(k), "vol": get_event_sfx_volume_pct(k)}
        for k in EVENT_SFX_KEYS
    }


def set_event_sfx_pair(key: str, filename: str | None, vol_pct: int | float | str | None) -> None:
    if key not in EVENT_SFX_KEYS:
        raise ValueError("bad_event_key")
    raw_name = normalize_event_sfx_filename(filename or "")
    set_meta_value(f"sfx_evt_{key}_file", raw_name)
    try:
        v = int(float(vol_pct if vol_pct is not None else 100))
    except (TypeError, ValueError):
        v = 100
    set_meta_value(f"sfx_evt_{key}_vol", str(max(0, min(100, v))))


def get_meta_int(key: str, default: int, lo: int, hi: int) -> int:
    conn = _connect_db()
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    finally:
        conn.close()
    if not row:
        return default
    try:
        n = int(row["value"])
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def set_meta_value(key: str, value: str) -> None:
    conn = _connect_db()
    try:
        conn.execute(
            """
            INSERT INTO meta (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def get_meta_text(key: str, default: str = "") -> str:
    conn = _connect_db()
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    finally:
        conn.close()
    if not row:
        return default
    return str(row["value"] or default)


def get_defeat_ball_file() -> str:
    return normalize_attack_sprite_file(get_meta_text("defeat_ball_sprite_file", ""))


def get_defeat_ball_url() -> str:
    name = get_defeat_ball_file()
    return f"/sprites/{name}" if name else ""


def get_defeat_ball_size_px() -> int:
    return get_meta_int("defeat_ball_size_px", 28, 8, 128)


def set_defeat_ball_size_px(size_px: int | float | str | None) -> None:
    set_meta_value("defeat_ball_size_px", str(normalize_pet_ball_size_px(size_px)))


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_WORLD.mkdir(parents=True, exist_ok=True)
    ARCHIVE_SPRITES.mkdir(parents=True, exist_ok=True)
    (STATIC_DIR / "bg").mkdir(parents=True, exist_ok=True)
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password TEXT NOT NULL,
                sprite_file TEXT NOT NULL,
                sprite_flipped INTEGER NOT NULL DEFAULT 0,
                shout_greeting TEXT NOT NULL DEFAULT 'Hello!',
                sprite_width_px INTEGER NOT NULL DEFAULT 48,
                pos_x REAL NOT NULL DEFAULT 400,
                pos_y REAL NOT NULL DEFAULT 300,
                facing_h INTEGER NOT NULL DEFAULT 1,
                attack_sprite_file TEXT NOT NULL DEFAULT '',
                attack_sprite_flipped INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        _migrate_users_password_column(conn)
        _migrate_users_sprite_flipped(conn)
        _migrate_users_shout_greeting(conn)
        _migrate_users_sprite_width_px(conn)
        _migrate_users_pos_and_facing(conn)
        _migrate_users_attack_sprite(conn)
        _migrate_users_correct_answers(conn)
        _migrate_users_current_world(conn)
        _migrate_users_magicoin(conn)
        _migrate_users_health(conn)
        _migrate_users_attack_sfx(conn)
        _migrate_users_transformed_shiny(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT 'Pet',
                sprite_file TEXT NOT NULL DEFAULT '',
                sprite_flipped INTEGER NOT NULL DEFAULT 0,
                attack_sprite_file TEXT NOT NULL DEFAULT '',
                attack_sprite_flipped INTEGER NOT NULL DEFAULT 0,
                x REAL NOT NULL DEFAULT 420,
                y REAL NOT NULL DEFAULT 320,
                facing_h INTEGER NOT NULL DEFAULT 1,
                size_px INTEGER NOT NULL DEFAULT 52,
                speed_px REAL NOT NULL DEFAULT 1.0,
                code_mode TEXT NOT NULL DEFAULT 'default'
            );
            """
        )
        _migrate_pets_size_speed(conn)
        _migrate_pets_code_mode(conn)
        _migrate_pets_world_id(conn)
        _migrate_pets_world_screen(conn)
        _migrate_pets_attack_sfx(conn)
        _migrate_pets_instance_name(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS worlds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT 'World',
                background_file TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL DEFAULT 0
            );
            """
        )
        _seed_default_world(conn)
        _migrate_worlds_music_file(conn)
        _migrate_worlds_music_volume(conn)
        _migrate_worlds_created_by_user_id(conn)
        _migrate_world_obstacle_configs(conn)
        _migrate_world_obstacles(conn)
        _migrate_world_obstacles_world_screen(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS world_pet_types (
                world_id INTEGER NOT NULL,
                template_pet_id INTEGER NOT NULL,
                created_at REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (world_id, template_pet_id)
            );
            """
        )
        _seed_missing_world_pet_types(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pet_code (
                pet_id INTEGER PRIMARY KEY,
                code TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS captures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                pet_id INTEGER NOT NULL,
                pet_name TEXT NOT NULL DEFAULT '',
                sprite_file TEXT NOT NULL DEFAULT '',
                attack_sprite_file TEXT NOT NULL DEFAULT '',
                size_px INTEGER NOT NULL DEFAULT 52,
                captured_at REAL NOT NULL DEFAULT 0
            );
            """
        )
        _migrate_captures_attack_sprite(conn)
        _migrate_captures_instance_name(conn)
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_captures_user ON captures(user_id, id);
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_captures_user_pet ON captures(user_id, pet_id);
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO meta (key, value)
            VALUES ('schema_version', '9');
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO meta (key, value)
            VALUES ('speech_font_px', '20');
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO meta (key, value)
            VALUES ('sfx_volume', '35');
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO meta (key, value)
            VALUES ('music_volume', '25');
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO meta (key, value)
            VALUES ('music_file', '');
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO meta (key, value)
            VALUES ('shadow_darkness_pct', '28');
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO meta (key, value)
            VALUES ('shadow_height_pct', '92');
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO meta (key, value)
            VALUES ('shadow_blur_px', '0');
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO meta (key, value)
            VALUES ('defeat_ball_sprite_file', '');
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO meta (key, value)
            VALUES ('defeat_ball_sprite_flipped', '0');
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO meta (key, value)
            VALUES ('defeat_ball_size_px', '28');
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO meta (key, value)
            VALUES ('buy_pet_cost', '150');
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO meta (key, value)
            VALUES ('pet_size_min', '25');
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO meta (key, value)
            VALUES ('pet_size_max', '175');
            """
        )
        conn.commit()
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pet_loop_task
    init_db()
    load_pets_runtime()
    load_world_obstacles()
    pet_loop_task = asyncio.create_task(pet_runtime_loop())
    yield
    if pet_loop_task:
        pet_loop_task.cancel()


app = FastAPI(lifespan=lifespan)

app.mount("/sfx", StaticFiles(directory=str(SFX_DIR)), name="sfx")
app.mount("/music", StaticFiles(directory=str(MUSIC_DIR)), name="music")
app.mount(
    "/static-sprites",
    StaticFiles(directory=str(STATIC_DIR / "sprites")),
    name="static-sprites",
)


def _archive_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def archive_world_background_files() -> None:
    """Move existing world.png / world.jpg into data/archives/world/."""
    stamp = _archive_stamp()
    if WORLD_BG_PNG.is_file():
        WORLD_BG_PNG.rename(ARCHIVE_WORLD / f"{stamp}_world.png")
    if WORLD_BG_JPG.is_file():
        WORLD_BG_JPG.rename(ARCHIVE_WORLD / f"{stamp}_world.jpg")


def archive_user_sprite_file(sprite_filename: str, username_label: str) -> None:
    """Move an uploads/ sprite into data/archives/sprites/."""
    safe = re.sub(r"[^\w\-]+", "_", username_label)[:40]
    src = UPLOAD_DIR / sprite_filename
    if not src.is_file():
        return
    stamp = _archive_stamp()
    dest = ARCHIVE_SPRITES / f"{stamp}_{safe}_{sprite_filename}"
    src.rename(dest)


def get_sprite_width() -> int:
    conn = _connect_db()
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'sprite_width'",
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return 48
    try:
        w = int(row["value"])
    except ValueError:
        return 48
    return max(16, min(512, w))


def set_sprite_width(width: int) -> None:
    w = max(16, min(512, int(width)))
    conn = _connect_db()
    try:
        conn.execute(
            """
            INSERT INTO meta (key, value) VALUES ('sprite_width', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(w),),
        )
        conn.commit()
    finally:
        conn.close()


def get_speech_font_px() -> int:
    conn = _connect_db()
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'speech_font_px'",
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return 20
    try:
        n = int(row["value"])
    except ValueError:
        return 20
    return max(10, min(48, n))


def set_speech_font_px(px: int) -> None:
    n = max(10, min(48, int(px)))
    conn = _connect_db()
    try:
        conn.execute(
            """
            INSERT INTO meta (key, value) VALUES ('speech_font_px', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(n),),
        )
        conn.commit()
    finally:
        conn.close()


def get_sfx_volume() -> int:
    return get_meta_int("sfx_volume", 35, 0, 100)


def set_sfx_volume(v: int) -> None:
    set_meta_value("sfx_volume", str(normalize_volume(v, 35)))


def get_music_volume() -> int:
    return get_meta_int("music_volume", 25, 0, 100)


def set_music_volume(v: int) -> None:
    set_meta_value("music_volume", str(normalize_volume(v, 25)))


def get_music_file() -> str:
    conn = _connect_db()
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'music_file'").fetchone()
    finally:
        conn.close()
    if not row:
        return ""
    name = str(row["value"] or "").strip()
    return name if name and name.endswith(tuple(SFX_ALLOWED_SUFFIXES)) else ""


def set_music_file(name: str) -> None:
    set_meta_value("music_file", name or "")


def get_shadow_darkness_pct() -> int:
    return get_meta_int("shadow_darkness_pct", 28, 0, 100)


def set_shadow_darkness_pct(v: int) -> None:
    set_meta_value("shadow_darkness_pct", str(max(0, min(100, int(v)))))


def get_shadow_height_pct() -> int:
    return get_meta_int("shadow_height_pct", 92, 50, 160)


def set_shadow_height_pct(v: int) -> None:
    set_meta_value("shadow_height_pct", str(max(50, min(160, int(v)))))


def get_shadow_blur_px() -> int:
    return get_meta_int("shadow_blur_px", 0, 0, 40)


def set_shadow_blur_px(v: int) -> None:
    set_meta_value("shadow_blur_px", str(max(0, min(40, int(v)))))


def get_quiz_max_number() -> int:
    return get_meta_int("quiz_max_number", 30, 5, 999)


def set_quiz_max_number(v: int) -> None:
    set_meta_value("quiz_max_number", str(max(5, min(999, int(v)))))


QUIZ_MODE_KEYS = (
    "algebra_add_sub",
    "plain_add_sub_2digit",
    "plain_add_sub_chain",
)


def get_quiz_mode() -> str:
    conn = _connect_db()
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", ("quiz_mode",)
        ).fetchone()
    finally:
        conn.close()
    raw = (row["value"] if row else "") or ""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    raw = str(raw).strip()
    if raw in QUIZ_MODE_KEYS:
        return raw
    return "algebra_add_sub"


def set_quiz_mode(mode: str) -> None:
    m = str(mode or "").strip()
    if m not in QUIZ_MODE_KEYS:
        m = "algebra_add_sub"
    set_meta_value("quiz_mode", m)


def _user_attack_sfx_triple(user: sqlite3.Row) -> tuple[str, int, str]:
    try:
        keys = user.keys()
    except Exception:
        keys = []
    raw = ""
    if "attack_sfx_file" in keys:
        raw = str(user["attack_sfx_file"] or "").strip()
    try:
        v = int(user["attack_sfx_volume_pct"] if "attack_sfx_volume_pct" in keys else 100)
    except (TypeError, ValueError):
        v = 100
    v = max(0, min(100, v))
    url = f"/sfx/{raw}" if raw else ""
    return raw, v, url


def get_buy_pet_cost() -> int:
    return get_meta_int("buy_pet_cost", 150, 0, 100000)


def set_buy_pet_cost(v: int) -> None:
    set_meta_value("buy_pet_cost", str(max(0, min(100000, int(v)))))


def get_pet_size_min() -> int:
    return get_meta_int("pet_size_min", 25, 8, 1024)


def set_pet_size_min(v: int) -> None:
    set_meta_value("pet_size_min", str(max(8, min(1024, int(v)))))


def get_pet_size_max() -> int:
    return get_meta_int("pet_size_max", 175, 8, 1024)


def set_pet_size_max(v: int) -> None:
    set_meta_value("pet_size_max", str(max(8, min(1024, int(v)))))


def _session_token(request: Request) -> str | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw or len(raw) > 128:
        return None
    return raw


def user_id_from_session_token(token: str | None) -> int | None:
    if not token:
        return None
    conn = _connect_db()
    try:
        row = conn.execute(
            "SELECT user_id FROM sessions WHERE token = ?",
            (token,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return int(row["user_id"])


def _redirect_with_session_cookie(url: str, user_id: int) -> RedirectResponse:
    token = secrets.token_hex(32)
    conn = _connect_db()
    try:
        conn.execute(
            "INSERT INTO sessions (token, user_id) VALUES (?, ?)",
            (token, user_id),
        )
        conn.commit()
    finally:
        conn.close()
    r = RedirectResponse(url, status_code=303)
    r.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return r


def _redirect_clear_session(request: Request, url: str) -> RedirectResponse:
    tok = _session_token(request)
    if tok:
        conn = _connect_db()
        try:
            conn.execute("DELETE FROM sessions WHERE token = ?", (tok,))
            conn.commit()
        finally:
            conn.close()
    r = RedirectResponse(url, status_code=303)
    r.delete_cookie(SESSION_COOKIE, path="/")
    return r


def sniff_image_kind(head: bytes) -> str | None:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "gif"
    if len(head) >= 12 and head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    return None


def extension_for_kind(kind: str) -> str:
    return ".jpg" if kind == "jpg" else f".{kind}"


WORLD_BG_MAX_WIDTH_PX = 1600
WORLD_BG_LARGE_BYTES = 800 * 1024  # >800 KB triggers re-encode


def maybe_shrink_world_background(body: bytes, kind: str) -> tuple[bytes, str]:
    """Convert oversized world backgrounds to JPG and cap them at 1600px wide.

    Returns (possibly-rewritten bytes, final kind). If neither the byte size
    nor the image width is above the thresholds, the original bytes are
    returned unchanged.
    """
    try:
        with Image.open(io.BytesIO(body)) as im:
            im.load()
            width, _ = im.size
            needs_resize = width > WORLD_BG_MAX_WIDTH_PX
            needs_recode = len(body) > WORLD_BG_LARGE_BYTES
            if not needs_resize and not needs_recode:
                return body, kind
            target = im
            if needs_resize:
                ratio = WORLD_BG_MAX_WIDTH_PX / float(width)
                new_size = (
                    WORLD_BG_MAX_WIDTH_PX,
                    max(1, int(round(im.height * ratio))),
                )
                target = im.resize(new_size, Image.LANCZOS)
            if target.mode not in ("RGB", "L"):
                target = target.convert("RGB")
            buf = io.BytesIO()
            target.save(buf, format="JPEG", quality=82, optimize=True, progressive=True)
            return buf.getvalue(), "jpg"
    except (UnidentifiedImageError, OSError, ValueError):
        return body, kind


def flip_sprite_file_on_disk(path: Path) -> None:
    """Rewrite the image file with pixels mirrored left–right (Pillow)."""
    with Image.open(path) as im:
        im.load()
        if getattr(im, "n_frames", 1) > 1:
            im.seek(0)
        fmt = (im.format or "PNG").upper()
        flipped = im.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        save_kw: dict = {}
        if fmt == "JPEG":
            flipped = flipped.convert("RGB")
            save_kw["quality"] = 92
        elif fmt == "WEBP":
            save_kw["quality"] = 90
        elif fmt == "PNG":
            pass
        elif fmt == "GIF":
            flipped = flipped.convert("P", palette=Image.ADAPTIVE, colors=256)
        flipped.save(path, format=fmt, **save_kw)


async def broadcast(message: dict, exclude_id: str | None = None) -> None:
    payload = json.dumps(message)
    dead: list[str] = []
    for pid, info in list(players.items()):
        if pid == exclude_id:
            continue
        ws = info.get("ws")
        if ws is None:
            dead.append(pid)
            continue
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(pid)
    for pid in dead:
        players.pop(pid, None)


async def broadcast_all(message: dict) -> None:
    payload = json.dumps(message)
    dead: list[str] = []
    for pid, info in list(players.items()):
        ws = info.get("ws")
        if ws is None:
            dead.append(pid)
            continue
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(pid)
    for pid in dead:
        players.pop(pid, None)


async def broadcast_world(
    world_id: int,
    message: dict,
    exclude_id: str | None = None,
    world_screen: int | None = None,
) -> None:
    payload = json.dumps(message)
    dead: list[str] = []
    for pid, info in list(players.items()):
        if pid == exclude_id:
            continue
        if int(info.get("world_id", 1) or 1) != int(world_id):
            continue
        if world_screen is not None and _player_world_screen(info) != normalize_world_screen(world_screen):
            continue
        ws = info.get("ws")
        if ws is None:
            dead.append(pid)
            continue
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(pid)
    for pid in dead:
        players.pop(pid, None)


async def broadcast_all_world(world_id: int, message: dict) -> None:
    await broadcast_world(world_id, message, exclude_id=None, world_screen=None)


async def notify_world_background_changed(world_id: int | None = None) -> None:
    msg = {
        "type": "reload_world_bg",
        "t": int(time.time() * 1000),
        "world_id": int(world_id) if world_id is not None else None,
    }
    if world_id is None:
        await broadcast_all(msg)
    else:
        await broadcast_all_world(int(world_id), msg)


async def notify_sprite_settings_changed() -> None:
    music_file = get_music_file()
    await broadcast_all(
        {
            "type": "settings",
            "speech_font_px": get_speech_font_px(),
            "sfx_volume": get_sfx_volume(),
            "music_volume": get_music_volume(),
            "music_url": f"/sfx/{music_file}" if music_file else "",
            "shadow_darkness_pct": get_shadow_darkness_pct(),
            "shadow_height_pct": get_shadow_height_pct(),
            "shadow_blur_px": get_shadow_blur_px(),
            "defeat_ball_url": get_defeat_ball_url(),
            "defeat_ball_size_px": get_defeat_ball_size_px(),
            "quiz_max_number": get_quiz_max_number(),
            "quiz_mode": get_quiz_mode(),
            "buy_pet_cost": get_buy_pet_cost(),
            "pet_size_min": get_pet_size_min(),
            "pet_size_max": get_pet_size_max(),
            "event_sfx": get_event_sfx_config(),
        }
    )


async def notify_user_sprite_updated(
    account_id: int, sprite_filename: str, bust_cache: bool = False
) -> None:
    url = f"/sprites/{sprite_filename}"
    if bust_cache:
        url += f"?v={int(time.time() * 1000)}"
    for info in players.values():
        if info.get("user_id") == account_id:
            info["sprite"] = url
    await broadcast_all({"type": "user_sprite", "account_id": account_id, "sprite": url})


async def notify_user_attack_sprite_updated(
    account_id: int, attack_sprite_filename: str, bust_cache: bool = False
) -> None:
    url = ""
    if attack_sprite_filename:
        url = f"/sprites/{attack_sprite_filename}"
        if bust_cache:
            url += f"?v={int(time.time() * 1000)}"
    for info in players.values():
        if info.get("user_id") == account_id:
            info["attack_sprite"] = url
    await broadcast_all(
        {"type": "user_attack_sprite", "account_id": account_id, "attack_sprite": url}
    )


def _coin_pile_id_for_pet(pet_id: int, spawn_seq: int) -> str:
    return f"cp:{int(pet_id)}:{int(spawn_seq)}"


def _spawn_coin_pile_for_pet(pet_id: int, pet: dict) -> None:
    """Spawn a coin pile at the pet's location when the pet has just been defeated."""
    seq = int(pet.get("defeat_seq", 0)) + 1
    pet["defeat_seq"] = seq
    cid = _coin_pile_id_for_pet(pet_id, seq)
    base = random.randint(COIN_PILE_MIN_AMOUNT, COIN_PILE_MAX_AMOUNT)
    if bool(pet.get("shiny", False)):
        base *= 10
    coin_piles[cid] = {
        "id": cid,
        "world_id": int(pet.get("world_id", 1)),
        "world_screen": normalize_world_screen(pet.get("world_screen", 1)),
        "x": float(pet.get("x", 0.0)),
        "y": float(pet.get("y", 0.0)),
        "amount": int(base),
        "spawn_t": time.time(),
        "pickupable": False,
        "pickupable_after": 0.0,
        "pet_id": int(pet_id),
        "pet_defeat_seq": seq,
        "collected": False,
        "shiny": bool(pet.get("shiny", False)),
    }


def _make_pet_coin_pile_pickupable(pet_id: int) -> None:
    """When a pet's ball is collected, allow its coin pile to be picked up next swing."""
    now = time.time()
    for c in coin_piles.values():
        if int(c.get("pet_id", -1)) != int(pet_id):
            continue
        if bool(c.get("collected", False)):
            continue
        if c.get("pickupable"):
            continue
        c["pickupable"] = True
        c["pickupable_after"] = now + COIN_PILE_PICKUP_DELAY_S


def _coin_piles_snapshot(
    world_id: int | None = None, world_screen: int | None = None
) -> list[dict]:
    out: list[dict] = []
    for cid in sorted(coin_piles.keys()):
        c = coin_piles[cid]
        if bool(c.get("collected", False)):
            continue
        if world_id is not None and int(c.get("world_id", 1)) != int(world_id):
            continue
        if (
            world_screen is not None
            and normalize_world_screen(c.get("world_screen", 1))
            != normalize_world_screen(world_screen)
        ):
            continue
        out.append(
            {
                "id": cid,
                "world_id": int(c.get("world_id", 1)),
                "world_screen": normalize_world_screen(c.get("world_screen", 1)),
                "x": float(c.get("x", 0.0)),
                "y": float(c.get("y", 0.0)),
                "amount": int(c.get("amount", 0)),
                "spawn_t": float(c.get("spawn_t", 0.0)),
                "pickupable": bool(c.get("pickupable", False)),
                "size_px": COIN_PILE_SIZE_PX,
                "shiny": bool(c.get("shiny", False)),
            }
        )
    return out


def _expire_old_coin_piles() -> bool:
    """Remove coin piles older than COIN_PILE_LIFETIME_S. Returns True if any expired."""
    now = time.time()
    expired = [
        cid for cid, c in coin_piles.items()
        if now - float(c.get("spawn_t", now)) > COIN_PILE_LIFETIME_S
        or bool(c.get("collected", False))
    ]
    for cid in expired:
        coin_piles.pop(cid, None)
    return bool(expired)


def _attack_hits_coin_pile(player: dict, c: dict, radius: float) -> bool:
    px = float(player.get("x", 0.0))
    py = float(player.get("y", 0.0))
    cx = float(c.get("x", 0.0))
    cy = float(c.get("y", 0.0))
    facing = 1 if int(player.get("facing_h", 1)) >= 0 else -1
    sprite_width = normalize_sprite_width_px(player.get("sprite_width_px"))
    forward = max(PLAYER_ATTACK_REACH_PX, sprite_width * 0.9)
    behind = sprite_width * 0.4
    if facing >= 0:
        if cx < px - behind:
            return False
        if cx > px + forward + radius:
            return False
    else:
        if cx > px + behind:
            return False
        if cx < px - forward - radius:
            return False
    if abs(cy - py) > (PLAYER_ATTACK_VERTICAL_PX + radius):
        return False
    return math.hypot(cx - px, cy - py) <= (forward + radius)


def _collect_coin_piles_for_attack(player: dict) -> tuple[bool, int]:
    """Collect any pickupable coin piles in attack range. Returns (changed, total_magicoin)."""
    now = time.time()
    pworld = _player_world_id(player)
    pscreen = _player_world_screen(player)
    radius = COIN_PILE_SIZE_PX / 2.0
    total = 0
    changed = False
    to_remove: list[str] = []
    for cid, c in list(coin_piles.items()):
        if int(c.get("world_id", 1)) != pworld:
            continue
        if normalize_world_screen(c.get("world_screen", 1)) != pscreen:
            continue
        if bool(c.get("collected", False)):
            to_remove.append(cid)
            continue
        if not bool(c.get("pickupable", False)):
            continue
        if now < float(c.get("pickupable_after", 0.0)):
            continue
        if not _attack_hits_coin_pile(player, c, radius):
            continue
        total += int(c.get("amount", 0))
        to_remove.append(cid)
        changed = True
    for cid in to_remove:
        coin_piles.pop(cid, None)
    return changed, total


def pets_snapshot(
    world_id: int | None = None, world_screen: int | None = None
) -> list[dict]:
    out: list[dict] = []
    now = time.time()
    for pid in sorted(pets_runtime.keys()):
        p = pets_runtime[pid]
        if world_id is not None and int(p.get("world_id", 1)) != int(world_id):
            continue
        if (
            world_screen is not None
            and normalize_world_screen(p.get("world_screen", 1))
            != normalize_world_screen(world_screen)
        ):
            continue
        sleeping = now < float(p.get("sleep_until", 0.0))
        collected = bool(p.get("collected", False))
        out.append(
            {
                "id": pid,
                "name": p["name"],
                "instance_name": (p.get("instance_name") or ""),
                "shiny": bool(p.get("shiny", False)),
                "x": p["x"],
                "y": p["y"],
                "facing_h": p["facing_h"],
                "sprite": p.get("sprite", ""),
                "attack_sprite": p.get("attack_sprite", ""),
                "attacking": bool(p.get("attacking", False)),
                "sleeping": sleeping,
                "size_px": normalize_pet_size_px(p.get("size_px")),
                "speed_px": normalize_pet_speed_px(p.get("speed_px")),
                "health": max(0, min(PET_MAX_HEALTH, int(p.get("health", PET_MAX_HEALTH)))),
                "max_health": PET_MAX_HEALTH,
                "defeated": int(p.get("health", PET_MAX_HEALTH)) <= 0,
                "collected": collected,
                "spawn_seq": int(p.get("spawn_seq", 0)),
                "world_id": int(p.get("world_id", 1)),
                "world_screen": normalize_world_screen(p.get("world_screen", 1)),
                "attack_sfx_url": p.get("attack_sfx_url", ""),
                "attack_sfx_volume_pct": int(p.get("attack_sfx_volume_pct", 100)),
            }
        )
    return out


def load_pets_runtime() -> None:
    pets_runtime.clear()
    conn = _connect_db()
    try:
        rows = conn.execute(
            """
            SELECT
                id, name, sprite_file, attack_sprite_file,
                x, y, facing_h, size_px, speed_px, code_mode, world_id,
                COALESCE(world_screen, 1) AS world_screen,
                COALESCE(attack_sfx_file, '') AS attack_sfx_file,
                COALESCE(attack_sfx_volume_pct, 100) AS attack_sfx_volume_pct,
                COALESCE(instance_name, '') AS instance_name,
                COALESCE(shiny, 0) AS shiny
            FROM pets ORDER BY id
            """
        ).fetchall()
    finally:
        conn.close()
    for r in rows:
        sid = normalize_attack_sprite_file(r["sprite_file"])
        aid = normalize_attack_sprite_file(r["attack_sprite_file"])
        sfx_name = (r["attack_sfx_file"] or "").strip()
        sfx_url = f"/sfx/{sfx_name}" if sfx_name else ""
        try:
            sfx_vol = int(r["attack_sfx_volume_pct"])
        except (TypeError, ValueError):
            sfx_vol = 100
        sfx_vol = max(0, min(100, sfx_vol))
        inst_name = (r["instance_name"] or "").strip()
        if not inst_name:
            inst_name = generate_pet_instance_name()
            try:
                _set_pet_instance_name(int(r["id"]), inst_name)
            except Exception:
                pass
        pets_runtime[int(r["id"])] = {
            "name": (r["name"] or "Pet")[:24],
            "instance_name": inst_name,
            "shiny": bool(int(r["shiny"] or 0)),
            "x": float(r["x"]),
            "y": float(r["y"]),
            "facing_h": normalize_facing_h(r["facing_h"]),
            "sprite": f"/sprites/{sid}" if sid else "",
            "attack_sprite": f"/sprites/{aid}" if aid else "",
            "vx": random.uniform(-0.8, 0.8),
            "vy": random.uniform(-0.8, 0.8),
            "next_turn_at": time.time() + random.uniform(0.6, 2.0),
            "attack_until": 0.0,
            "attacking": False,
            "pause_until": 0.0,
            "size_px": normalize_pet_size_px(r["size_px"]),
            "speed_px": normalize_pet_speed_px(r["speed_px"]),
            "code_mode": (r["code_mode"] or "default"),
            "sleep_until": 0.0,
            "await_move": False,
            "target_x": None,
            "target_y": None,
            "program": None,
            "pc": 0,
            "health": PET_MAX_HEALTH,
            "collected": False,
            "respawn_at": 0.0,
            "offscreen_since": 0.0,
            "spawn_seq": 0,
            "world_id": int(r["world_id"] or 1),
            "world_screen": normalize_world_screen(r["world_screen"]),
            "attack_sfx_file": sfx_name,
            "attack_sfx_url": sfx_url,
            "attack_sfx_volume_pct": sfx_vol,
        }


def _player_world_id(info: dict) -> int:
    return int(info.get("world_id", 1) or 1)


def _player_world_screen(info: dict) -> int:
    return normalize_world_screen(info.get("world_screen", 1))


def _world_exists(world_id: int) -> bool:
    conn = _connect_db()
    try:
        row = conn.execute(
            "SELECT 1 FROM worlds WHERE id = ?", (int(world_id),)
        ).fetchone()
    finally:
        conn.close()
    return row is not None


def _ensure_default_world_id() -> int:
    conn = _connect_db()
    try:
        row = conn.execute("SELECT id FROM worlds ORDER BY id LIMIT 1").fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO worlds (name, background_file, created_at) VALUES (?, ?, ?)",
                ("Default", "", time.time()),
            )
            conn.commit()
            return int(cur.lastrowid)
        return int(row["id"])
    finally:
        conn.close()


def _set_user_current_world(user_id: int, world_id: int) -> None:
    conn = _connect_db()
    try:
        conn.execute(
            "UPDATE users SET current_world_id = ? WHERE id = ?",
            (int(world_id), int(user_id)),
        )
        conn.commit()
    finally:
        conn.close()


def normalize_world_screen(raw: int | float | str | None) -> int:
    try:
        n = int(float(raw if raw is not None else 1))
    except (TypeError, ValueError):
        n = 1
    return max(0, min(2, n))


def _set_user_current_world_screen(user_id: int, world_screen: int) -> None:
    ws = normalize_world_screen(world_screen)
    conn = _connect_db()
    try:
        conn.execute(
            "UPDATE users SET current_world_screen = ? WHERE id = ?",
            (ws, int(user_id)),
        )
        conn.commit()
    finally:
        conn.close()


def _get_user_magicoin(user_id: int) -> int:
    conn = _connect_db()
    try:
        row = conn.execute(
            "SELECT magicoin FROM users WHERE id = ?", (int(user_id),)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return 0
    return int(row["magicoin"] or 0)


def _set_user_magicoin(user_id: int, balance: int) -> None:
    conn = _connect_db()
    try:
        conn.execute(
            "UPDATE users SET magicoin = ? WHERE id = ?",
            (max(0, int(balance)), int(user_id)),
        )
        conn.commit()
    finally:
        conn.close()


def _spend_magicoin(user_id: int, amount: int) -> tuple[bool, int]:
    """Atomically deduct `amount` magicoin from user. Returns (ok, new_balance)."""
    conn = _connect_db()
    try:
        row = conn.execute(
            "SELECT magicoin FROM users WHERE id = ?", (int(user_id),)
        ).fetchone()
        if row is None:
            return False, 0
        current = int(row["magicoin"] or 0)
        if current < int(amount):
            return False, current
        new_bal = current - int(amount)
        conn.execute(
            "UPDATE users SET magicoin = ? WHERE id = ?",
            (new_bal, int(user_id)),
        )
        conn.commit()
        return True, new_bal
    finally:
        conn.close()


def _set_user_position(user_id: int, x: float, y: float) -> None:
    conn = _connect_db()
    try:
        conn.execute(
            "UPDATE users SET pos_x = ?, pos_y = ? WHERE id = ?",
            (float(x), float(y), int(user_id)),
        )
        conn.commit()
    finally:
        conn.close()


def _get_user_max_health(user_id: int) -> int:
    if not user_id:
        return PLAYER_MAX_HEALTH
    conn = _connect_db()
    try:
        row = conn.execute(
            "SELECT COALESCE(max_health, ?) AS max_health FROM users WHERE id = ?",
            (PLAYER_MAX_HEALTH, int(user_id)),
        ).fetchone()
        if not row:
            return PLAYER_MAX_HEALTH
        return max(1, min(PLAYER_MAX_HEALTH_CAP, int(row["max_health"])))
    finally:
        conn.close()


def _set_user_max_health(user_id: int, max_hp: int) -> int:
    cap = max(1, min(PLAYER_MAX_HEALTH_CAP, int(max_hp)))
    conn = _connect_db()
    try:
        conn.execute(
            "UPDATE users SET max_health = ? WHERE id = ?",
            (cap, int(user_id)),
        )
        conn.commit()
    finally:
        conn.close()
    return cap


def _set_user_health(user_id: int, health: int) -> None:
    cap = _get_user_max_health(int(user_id))
    conn = _connect_db()
    try:
        conn.execute(
            "UPDATE users SET health = ? WHERE id = ?",
            (max(0, min(cap, int(health))), int(user_id)),
        )
        conn.commit()
    finally:
        conn.close()


async def broadcast_user_magicoin(
    user_id: int,
    magicoin: int,
    delta: int = 0,
    reason: str | None = None,
) -> None:
    """Notify all sessions of a user that their magicoin balance changed."""
    msg: dict = {
        "type": "magicoin",
        "account_id": int(user_id),
        "magicoin": int(magicoin),
    }
    if delta:
        msg["delta"] = int(delta)
    if reason:
        msg["reason"] = reason
    payload = json.dumps(msg)
    dead: list[str] = []
    for pid, info in list(players.items()):
        if int(info.get("user_id") or 0) != int(user_id):
            continue
        ws = info.get("ws")
        if ws is None:
            dead.append(pid)
            continue
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(pid)
    for pid in dead:
        players.pop(pid, None)


def _world_background_url(world_id: int) -> str:
    conn = _connect_db()
    try:
        row = conn.execute(
            "SELECT background_file FROM worlds WHERE id = ?", (int(world_id),)
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row["background_file"]:
        return ""
    return f"/world-background/{int(world_id)}?v={int(time.time() * 1000)}"


def _world_music_filename(world_id: int) -> str:
    conn = _connect_db()
    try:
        row = conn.execute(
            "SELECT music_file FROM worlds WHERE id = ?", (int(world_id),)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return ""
    return str(row["music_file"] or "")


def _world_music_url(world_id: int) -> str:
    name = _world_music_filename(world_id)
    if not name:
        return ""
    return f"/music/{name}"


def _set_world_music_file(world_id: int, name: str) -> None:
    conn = _connect_db()
    try:
        conn.execute(
            "UPDATE worlds SET music_file = ? WHERE id = ?",
            (name or "", int(world_id)),
        )
        conn.commit()
    finally:
        conn.close()


def _world_music_volume_pct(world_id: int) -> int:
    conn = _connect_db()
    try:
        row = conn.execute(
            "SELECT music_volume_pct FROM worlds WHERE id = ?", (int(world_id),)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return 100
    try:
        v = int(row["music_volume_pct"])
    except (TypeError, ValueError):
        return 100
    return max(0, min(100, v))


def _set_world_music_volume_pct(world_id: int, pct: int | float | str | None) -> None:
    try:
        v = int(float(pct if pct is not None else 100))
    except (TypeError, ValueError):
        v = 100
    v = max(0, min(100, v))
    conn = _connect_db()
    try:
        conn.execute(
            "UPDATE worlds SET music_volume_pct = ? WHERE id = ?",
            (v, int(world_id)),
        )
        conn.commit()
    finally:
        conn.close()


def _list_world_music_filenames() -> list[str]:
    if not MUSIC_DIR.is_dir():
        return []
    out: list[str] = []
    for path in sorted(MUSIC_DIR.iterdir()):
        if path.is_file() and path.suffix.lower() in SFX_ALLOWED_SUFFIXES:
            out.append(path.name)
    return out


async def broadcast_pets_state(world_id: int | None = None) -> None:
    """If `world_id` is provided, only broadcast to players in that world."""
    if world_id is None:
        worlds: set[int] = set()
        for p in pets_runtime.values():
            worlds.add(int(p.get("world_id", 1)))
        for info in players.values():
            worlds.add(_player_world_id(info))
        if not worlds:
            return
        for wid in worlds:
            await _broadcast_pets_for_world(wid)
        return
    await _broadcast_pets_for_world(int(world_id))


async def _broadcast_pets_for_world(world_id: int) -> None:
    for wscr in (0, 1, 2):
        snap = pets_snapshot(world_id, wscr)
        cps = _coin_piles_snapshot(world_id, wscr)
        obs = obstacles_snapshot(world_id, wscr)
        msg = {
            "type": "pets",
            "pets": snap,
            "coin_piles": cps,
            "obstacles": obs,
            "world_id": int(world_id),
            "world_screen": int(wscr),
        }
        text = json.dumps(msg)
        targets = [
            info["ws"]
            for info in players.values()
            if _player_world_id(info) == int(world_id)
            and _player_world_screen(info) == int(wscr)
            and info.get("ws") is not None
        ]
        for ws in targets:
            try:
                await ws.send_text(text)
            except Exception:
                pass


def _attack_hits_pet(player: dict, p: dict, radius: float) -> bool:
    px = float(player.get("x", 0.0))
    py = float(player.get("y", 0.0))
    facing_h = normalize_facing_h(player.get("facing_h", 1))
    pet_x = float(p.get("x", 0.0))
    pet_y = float(p.get("y", 0.0))
    dx = pet_x - px
    dy = abs(pet_y - py)
    if dx * facing_h < -radius:
        return False
    if abs(dx) > PLAYER_ATTACK_REACH_PX + radius:
        return False
    return dy <= PLAYER_ATTACK_VERTICAL_PX + radius * 0.6


def _find_attackable_pet_id(player: dict) -> int | None:
    best_id: int | None = None
    best_dist = 10**9
    pworld = _player_world_id(player)
    for pid, p in pets_runtime.items():
        if int(p.get("world_id", 1)) != pworld:
            continue
        if bool(p.get("collected", False)):
            continue
        if int(p.get("health", PET_MAX_HEALTH)) <= 0:
            continue
        radius = normalize_pet_size_px(p.get("size_px")) / 2.0
        if not _attack_hits_pet(player, p, radius):
            continue
        dx = float(p.get("x", 0.0)) - float(player.get("x", 0.0))
        dy = float(p.get("y", 0.0)) - float(player.get("y", 0.0))
        dist = dx * dx + dy * dy
        if dist < best_dist:
            best_dist = dist
            best_id = pid
    return best_id


def _record_capture(player: dict, pet_id: int, pet: dict) -> None:
    user_id = player.get("user_id")
    if not user_id:
        return

    def _strip(url: str) -> str:
        if isinstance(url, str) and url.startswith("/sprites/"):
            return url[len("/sprites/"):].split("?", 1)[0]
        return ""

    sprite_file = _strip(pet.get("sprite") or "")
    attack_sprite_file = _strip(pet.get("attack_sprite") or "")
    name = str(pet.get("name") or "Pet")
    instance_name = str(pet.get("instance_name") or "")
    shiny = 1 if bool(pet.get("shiny", False)) else 0
    size_px = int(normalize_pet_size_px(pet.get("size_px")))
    release_base = random.randint(
        CAPTURE_RELEASE_MIN_MAGICOIN, CAPTURE_RELEASE_MAX_MAGICOIN
    )
    conn = _connect_db()
    try:
        conn.execute(
            """
            INSERT INTO captures (
                user_id, pet_id, pet_name, sprite_file, attack_sprite_file,
                size_px, captured_at, instance_name, shiny, release_base_magicoin
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id), int(pet_id), name, sprite_file, attack_sprite_file,
                size_px, time.time(), instance_name, shiny, int(release_base),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    player["capture_count"] = int(player.get("capture_count", 0)) + 1


def _get_capture_count(user_id: int) -> int:
    if not user_id:
        return 0
    conn = _connect_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM captures WHERE user_id = ?", (int(user_id),)
        ).fetchone()
        return int(row["c"]) if row else 0
    finally:
        conn.close()


_PET_NAME_STARTS: list[str] = []
_PET_NAME_ENDS: list[str] = []


def _load_pet_name_lists() -> None:
    global _PET_NAME_STARTS, _PET_NAME_ENDS
    if _PET_NAME_STARTS and _PET_NAME_ENDS:
        return
    starts_path = STATIC_DIR / "data" / "pet-name-starts.txt"
    ends_path = STATIC_DIR / "data" / "pet-name-ends.txt"
    starts: list[str] = []
    ends: list[str] = []
    try:
        with open(starts_path, "r", encoding="utf-8") as f:
            for line in f:
                t = line.strip()
                if t:
                    starts.append(t)
    except Exception:
        pass
    try:
        with open(ends_path, "r", encoding="utf-8") as f:
            for line in f:
                t = line.strip()
                if t:
                    ends.append(t)
    except Exception:
        pass
    if not starts:
        starts = ["Bubbly", "Fuzzy", "Sparky", "Pebble", "Sun"]
    if not ends:
        ends = ["paws", "tail", "puff", "boots", "boop"]
    _PET_NAME_STARTS = starts
    _PET_NAME_ENDS = ends


def generate_pet_instance_name() -> str:
    _load_pet_name_lists()
    if not _PET_NAME_STARTS or not _PET_NAME_ENDS:
        return "Pet"
    return random.choice(_PET_NAME_STARTS) + random.choice(_PET_NAME_ENDS)


SHINY_CHANCE = 0.10


def _set_pet_instance_name(pet_id: int, name: str) -> None:
    conn = _connect_db()
    try:
        conn.execute(
            "UPDATE pets SET instance_name = ? WHERE id = ?",
            (str(name)[:40], int(pet_id)),
        )
        conn.commit()
    finally:
        conn.close()


def _set_pet_shiny(pet_id: int, shiny: bool) -> None:
    conn = _connect_db()
    try:
        conn.execute(
            "UPDATE pets SET shiny = ? WHERE id = ?",
            (1 if shiny else 0, int(pet_id)),
        )
        conn.commit()
    finally:
        conn.close()


def _find_pet_id(target: dict) -> int | None:
    for pid, p in pets_runtime.items():
        if p is target:
            return pid
    return None


def damage_pets_for_player_attack(player: dict) -> tuple[bool, int]:
    changed = False
    score_delta = 0
    captured: list[tuple[int, dict]] = []
    pworld = _player_world_id(player)
    pscreen = _player_world_screen(player)
    for pid, p in pets_runtime.items():
        if int(p.get("world_id", 1)) != pworld:
            continue
        if normalize_world_screen(p.get("world_screen", 1)) != pscreen:
            continue
        if bool(p.get("collected", False)):
            continue
        health = max(0, min(PET_MAX_HEALTH, int(p.get("health", PET_MAX_HEALTH))))
        pet_radius = normalize_pet_size_px(p.get("size_px")) / 2.0
        if health <= 0:
            if _attack_hits_pet(player, p, max(pet_radius, get_defeat_ball_size_px() / 2.0)):
                p["collected"] = True
                p["respawn_at"] = time.time() + random.uniform(
                    PET_RESPAWN_MIN_SECONDS, PET_RESPAWN_MAX_SECONDS
                )
                p["attacking"] = False
                p["attack_until"] = 0.0
                _make_pet_coin_pile_pickupable(pid)
                score_delta += 1
                changed = True
                captured.append((pid, p))
            continue
        if not _attack_hits_pet(player, p, pet_radius):
            continue
        dmg = PLAYER_ATTACK_DAMAGE
        if bool(p.get("shiny", False)):
            dmg = max(1, int(round(PLAYER_ATTACK_DAMAGE * 0.5)))
        p["health"] = max(0, health - dmg)
        if p["health"] <= 0:
            p["vx"] = 0.0
            p["vy"] = 0.0
            p["target_x"] = None
            p["target_y"] = None
            p["await_move"] = False
            p["attacking"] = False
            p["attack_until"] = 0.0
            p["pause_until"] = 0.0
            p["sleep_until"] = 0.0
            _spawn_coin_pile_for_pet(pid, p)
        changed = True
    for pid, pet in captured:
        _record_capture(player, pid, pet)
    return changed, score_delta


def collect_defeated_targets_for_attack(player: dict) -> tuple[bool, int]:
    changed = False
    score_delta = 0
    now = time.time()
    ball_radius = get_defeat_ball_size_px() / 2.0
    captured: list[tuple[int, dict]] = []
    pworld = _player_world_id(player)
    pscreen = _player_world_screen(player)
    for pid, p in pets_runtime.items():
        if int(p.get("world_id", 1)) != pworld:
            continue
        if normalize_world_screen(p.get("world_screen", 1)) != pscreen:
            continue
        if bool(p.get("collected", False)):
            continue
        if int(p.get("health", PET_MAX_HEALTH)) > 0:
            continue
        if not _attack_hits_pet(player, p, max(normalize_pet_size_px(p.get("size_px")) / 2.0, ball_radius)):
            continue
        p["collected"] = True
        p["respawn_at"] = now + random.uniform(PET_RESPAWN_MIN_SECONDS, PET_RESPAWN_MAX_SECONDS)
        _make_pet_coin_pile_pickupable(pid)
        changed = True
        score_delta += 1
        captured.append((pid, p))
    for pid, pet in captured:
        _record_capture(player, pid, pet)
    return changed, score_delta


def _persist_pet_state(pet_id: int, p: dict) -> None:
    conn = _connect_db()
    try:
        conn.execute(
            """
            UPDATE pets
            SET x = ?, y = ?, facing_h = ?, code_mode = ?
            WHERE id = ?
            """,
            (
                float(p["x"]),
                float(p["y"]),
                normalize_facing_h(p["facing_h"]),
                (p.get("code_mode") or "default"),
                pet_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _get_pet_code(pet_id: int) -> str:
    conn = _connect_db()
    try:
        row = conn.execute("SELECT code FROM pet_code WHERE pet_id = ?", (pet_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return ""
    return str(row["code"] or "")


def _set_pet_code(pet_id: int, code: str) -> None:
    conn = _connect_db()
    try:
        conn.execute(
            """
            INSERT INTO pet_code (pet_id, code) VALUES (?, ?)
            ON CONFLICT(pet_id) DO UPDATE SET code = excluded.code
            """,
            (pet_id, code),
        )
        conn.commit()
    finally:
        conn.close()


DEFAULT_PET_CODE = (
    "# Pet behavior (python-like, parsed)\n"
    "# Available: sleep(seconds), attack(), move_up(px), move_down(px), move_left(px), move_right(px)\n"
    "\n"
    "while True:\n"
    "    sleep(1.0)\n"
    "    move_right(60)\n"
    "    sleep(0.6)\n"
    "    move_left(60)\n"
    "    sleep(0.6)\n"
    "    attack()\n"
    "    sleep(1.2)\n"
)

PET_CENTER_X = 420.0
PET_CENTER_Y = 320.0
PET_MOVE_EPS_PX = 1.0


def _respawn_pet(p: dict, now: float) -> None:
    w, h = _shared_viewport_size()
    margin = max(48.0, normalize_pet_size_px(p.get("size_px")) * 0.65)
    p["x"] = random.uniform(margin, max(margin, w - margin))
    p["y"] = random.uniform(margin, max(margin, h - margin))
    p["facing_h"] = random.choice([-1, 1])
    p["vx"] = random.uniform(-0.8, 0.8)
    p["vy"] = random.uniform(-0.8, 0.8)
    p["next_turn_at"] = now + random.uniform(0.8, 2.4)
    p["attack_until"] = 0.0
    p["attacking"] = False
    p["pause_until"] = now + 0.9
    p["sleep_until"] = 0.0
    p["await_move"] = False
    p["target_x"] = None
    p["target_y"] = None
    p["program"] = None
    p["pc"] = 0
    p["health"] = PET_MAX_HEALTH
    p["collected"] = False
    p["respawn_at"] = 0.0
    p["offscreen_since"] = 0.0
    p["spawn_seq"] = int(p.get("spawn_seq", 0)) + 1
    new_inst = generate_pet_instance_name()
    p["instance_name"] = new_inst
    new_shiny = random.random() < SHINY_CHANCE
    p["shiny"] = new_shiny
    pid = _find_pet_id(p)
    if pid is not None:
        try:
            _set_pet_instance_name(int(pid), new_inst)
            _set_pet_shiny(int(pid), bool(new_shiny))
        except Exception:
            pass


def _shared_viewport_size() -> tuple[float, float]:
    widths: list[float] = []
    heights: list[float] = []
    for info in players.values():
        try:
            w = float(info.get("viewport_w", 0.0))
            h = float(info.get("viewport_h", 0.0))
        except (TypeError, ValueError):
            continue
        if w >= 320 and h >= 240:
            widths.append(w)
            heights.append(h)
    if not widths or not heights:
        return 1360.0, 760.0
    return min(widths), min(heights)


def _clamp_pet_to_bounds(p: dict) -> bool:
    """Keep pet within the visible playfield. Returns True if position changed."""
    w, h = _shared_viewport_size()
    margin = max(24.0, normalize_pet_size_px(p.get("size_px")) * 0.5)
    x = float(p.get("x", 0.0))
    y = float(p.get("y", 0.0))
    nx = max(margin, min(w - margin, x))
    ny = max(margin, min(h - margin, y))
    changed = False
    if nx != x:
        p["x"] = nx
        if float(p.get("vx", 0.0)) != 0.0:
            p["vx"] = -float(p.get("vx", 0.0))
        changed = True
    if ny != y:
        p["y"] = ny
        if float(p.get("vy", 0.0)) != 0.0:
            p["vy"] = -float(p.get("vy", 0.0))
        changed = True
    return changed


def _obstacle_hitbox_rect(o: dict) -> tuple[float, float, float, float]:
    cx = float(o.get("x", 0.0))
    cy = float(o.get("y", 0.0))
    w = max(1.0, float(o.get("width_px", 1.0)))
    h = max(1.0, float(o.get("height_px", 1.0)))
    left = cx - w / 2.0
    right = cx + w / 2.0
    top = cy + h / 6.0
    bottom = cy + h / 2.0
    return left, top, right, bottom


def _circle_vs_rect_overlap(
    x: float, y: float, radius: float, rect: tuple[float, float, float, float]
) -> bool:
    left, top, right, bottom = rect
    nx = min(max(x, left), right)
    ny = min(max(y, top), bottom)
    dx = x - nx
    dy = y - ny
    return (dx * dx + dy * dy) <= (radius * radius)


def _rects_overlap(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> bool:
    al, at, ar, ab = a
    bl, bt, br, bb = b
    return not (ar < bl or al > br or ab < bt or at > bb)


def _actor_bottom_hitbox_rect(
    x: float, y: float, width_px: float, height_px: float
) -> tuple[float, float, float, float]:
    hw = max(4.0, float(width_px) / 2.0)
    hh = max(4.0, float(height_px) / 2.0)
    return (float(x) - hw, float(y) + hh / 3.0, float(x) + hw, float(y) + hh)


def _blocked_by_world_obstacles(
    world_id: int,
    world_screen: int,
    x: float,
    y: float,
    radius: float,
    width_px: float | None = None,
    height_px: float | None = None,
) -> bool:
    obs = world_obstacles.get(_obstacle_bucket_key(int(world_id), world_screen), [])
    if not obs:
        return False
    use_rect = width_px is not None and height_px is not None
    actor_rect = (
        _actor_bottom_hitbox_rect(float(x), float(y), float(width_px), float(height_px))
        if use_rect
        else (0.0, 0.0, 0.0, 0.0)
    )
    for o in obs:
        if use_rect:
            if _rects_overlap(actor_rect, _obstacle_hitbox_rect(o)):
                return True
        elif _circle_vs_rect_overlap(float(x), float(y), float(radius), _obstacle_hitbox_rect(o)):
            return True
    return False


def _resolve_obstacle_overlap_right(
    world_id: int,
    world_screen: int,
    x: float,
    y: float,
    radius: float,
    max_shift_px: float = 180.0,
    width_px: float | None = None,
    height_px: float | None = None,
) -> tuple[float, bool]:
    """If overlapping an obstacle hitbox, push right until clear or cap reached."""
    nx = float(x)
    if not _blocked_by_world_obstacles(
        world_id, world_screen, nx, y, radius, width_px=width_px, height_px=height_px
    ):
        return nx, False
    shifted = 0.0
    step = max(2.0, min(14.0, radius * 0.22))
    while shifted < max_shift_px:
        nx += step
        shifted += step
        if not _blocked_by_world_obstacles(
            world_id, world_screen, nx, y, radius, width_px=width_px, height_px=height_px
        ):
            return nx, True
    return nx, True


def _return_pet_to_screen_if_needed(p: dict, now: float) -> bool:
    w, h = _shared_viewport_size()
    margin = max(36.0, normalize_pet_size_px(p.get("size_px")) * 0.6)
    x = float(p.get("x", 0.0))
    y = float(p.get("y", 0.0))
    offscreen = x < -margin or x > w + margin or y < -margin or y > h + margin
    if not offscreen:
        p["offscreen_since"] = 0.0
        return False
    since = float(p.get("offscreen_since", 0.0))
    if since <= 0.0:
        p["offscreen_since"] = now
        return False
    if now - since < PET_OFFSCREEN_GRACE_SECONDS:
        return False
    p["x"] = random.uniform(margin, max(margin, w - margin))
    p["y"] = random.uniform(margin, max(margin, h - margin))
    p["vx"] = random.uniform(-0.8, 0.8)
    p["vy"] = random.uniform(-0.8, 0.8)
    p["target_x"] = None
    p["target_y"] = None
    p["await_move"] = False
    if (p.get("code_mode") or "default") == "code":
        p["pc"] = int(p.get("pc", 0)) + 1
    p["pause_until"] = now + 0.9
    p["offscreen_since"] = 0.0
    p["spawn_seq"] = int(p.get("spawn_seq", 0)) + 1
    return True


def _parse_pet_code(code: str) -> list[tuple[str, float, float]]:
    """
    Parse a tiny python-like script into a flat action list.

    Supported statements:
    - sleep(SECONDS)
    - attack()
    - move_up(PX), move_down(PX), move_left(PX), move_right(PX)
    - position(X, Y)  # 0,0 is the pet center
    - while True: <indented block>
    - for i in range(N): <indented block>

    Returns list of (op, v1, v2) where v1/v2 are seconds/pixels (v2 used by position).
    """

    def _strip_comment(s: str) -> str:
        return s.split("#", 1)[0].rstrip()

    lines = code.replace("\t", "    ").splitlines()

    def _extract_num(s: str) -> float:
        # Accept values like "200", "200px", "-1.5", etc.
        m = re.search(r"[-+]?\d*\.?\d+", s or "")
        if not m:
            return 0.0
        try:
            return float(m.group(0))
        except Exception:
            return 0.0

    def parse_block(i: int, indent: int) -> tuple[list, int]:
        out: list = []
        while i < len(lines):
            raw = lines[i]
            if not raw.strip() or raw.lstrip().startswith("#"):
                i += 1
                continue
            cur_indent = len(raw) - len(raw.lstrip(" "))
            if cur_indent < indent:
                break
            if cur_indent > indent:
                i += 1
                continue
            s = _strip_comment(raw.strip())
            if s.startswith("while True") and s.endswith(":"):
                inner, ni = parse_block(i + 1, indent + 4)
                out.append(("loop", inner))
                i = ni
                continue
            if s.startswith("for ") and " in range(" in s and s.endswith(":"):
                n = 0
                try:
                    n_str = s.split("range(", 1)[1].rsplit(")", 1)[0]
                    n = int(_extract_num(n_str))
                except Exception:
                    n = 0
                inner, ni = parse_block(i + 1, indent + 4)
                out.append(("repeat", n, inner))
                i = ni
                continue

            def call(name: str) -> tuple[str, float, float] | None:
                if not s.startswith(name + "(") or not s.endswith(")"):
                    return None
                arg = s[len(name) + 1 : -1].strip()
                if name == "attack":
                    return ("attack", 0.0, 0.0)
                if name == "sleep":
                    return ("sleep", _extract_num(arg), 0.0)
                if name == "position":
                    parts = arg.split(",", 1)
                    x = _extract_num(parts[0]) if parts else 0.0
                    y = _extract_num(parts[1]) if len(parts) > 1 else 0.0
                    return ("position", x, y)
                # move_* commands
                return (name, _extract_num(arg), 0.0)

            for fn in (
                "sleep",
                "attack",
                "position",
                "move_up",
                "move_down",
                "move_left",
                "move_right",
            ):
                c = call(fn)
                if c:
                    out.append(c)
                    break
            i += 1
        return out, i

    ast, _ = parse_block(0, 0)

    def flatten(node) -> list[tuple[str, float, float]]:
        out: list[tuple[str, float, float]] = []
        for item in node:
            if item[0] == "loop":
                inner = flatten(item[1])
                if inner:
                    out.extend(inner)
                    out.append(("__loop__", float(len(inner)), 0.0))
            elif item[0] == "repeat":
                n = max(0, int(item[1]))
                inner = flatten(item[2])
                for _ in range(n):
                    out.extend(inner)
            else:
                out.append((item[0], float(item[1]), float(item[2])))
        return out

    return flatten(ast)

def _pet_step_script(p: dict, now: float) -> bool:
    """Advance scripted behavior; returns True if state changed."""
    # When a move/position instruction is active, we must finish moving
    # before we continue to the next statement (so sleep stops movement).
    if bool(p.get("await_move")):
        tx = p.get("target_x")
        ty = p.get("target_y")
        if tx is None and ty is None:
            p["await_move"] = False
            p["target_x"] = None
            p["target_y"] = None
            p["pc"] = int(p.get("pc", 0)) + 1
            return True
        base_x = float(p["x"])
        base_y = float(p["y"])
        txv = float(tx) if tx is not None else base_x
        tyv = float(ty) if ty is not None else base_y
        dx = txv - base_x
        dy = tyv - base_y
        if math.hypot(dx, dy) <= PET_MOVE_EPS_PX:
            p["x"] = float(tx) if tx is not None else float(p["x"])
            p["y"] = float(ty) if ty is not None else float(p["y"])
            p["target_x"] = None
            p["target_y"] = None
            p["await_move"] = False
            p["pc"] = int(p.get("pc", 0)) + 1
            return True
        return False

    if p.get("sleep_until", 0.0) > now:
        return False
    prog = p.get("program")
    if not prog:
        return False
    pc = int(p.get("pc", 0))
    if pc < 0 or pc >= len(prog):
        p["pc"] = 0
        pc = 0
    op, v1, v2 = prog[pc]

    if op == "__loop__":
        back = int(v1)
        p["pc"] = max(0, pc - back)
        return True

    if op == "sleep":
        p["sleep_until"] = now + max(0.0, float(v1))
        p["target_x"] = None
        p["target_y"] = None
        p["await_move"] = False
        p["pc"] = pc + 1
        return True
    if op == "attack":
        p["attack_until"] = now + 0.38
        p["attack_seq"] = int(p.get("attack_seq", 0)) + 1
        p["target_x"] = None
        p["target_y"] = None
        p["await_move"] = False
        p["pc"] = pc + 1
        return True

    # Move-like ops: set target and block further parsing until reached.
    if op == "position":
        tx = PET_CENTER_X + float(v1)
        ty = PET_CENTER_Y + float(v2)
        p["target_x"] = float(tx)
        p["target_y"] = float(ty)
        p["await_move"] = True
        # face based on horizontal delta
        dx = tx - float(p["x"])
        if abs(dx) > 0.01:
            p["facing_h"] = -1 if dx < 0 else 1
        return True

    dist = float(v1)
    if op == "move_up":
        p["target_x"] = float(p["x"])
        p["target_y"] = float(p["y"]) - dist
        p["await_move"] = True
        return True
    if op == "move_down":
        p["target_x"] = float(p["x"])
        p["target_y"] = float(p["y"]) + dist
        p["await_move"] = True
        return True
    if op == "move_left":
        p["target_x"] = float(p["x"]) - dist
        p["target_y"] = float(p["y"])
        p["await_move"] = True
        p["facing_h"] = -1
        return True
    if op == "move_right":
        p["target_x"] = float(p["x"]) + dist
        p["target_y"] = float(p["y"])
        p["await_move"] = True
        p["facing_h"] = 1
        return True

    # Unknown op: skip to avoid getting stuck.
    p["pc"] = pc + 1
    return True

def _pet_attack_hits_pet(attacker: dict, target: dict) -> bool:
    if attacker is target:
        return False
    if bool(target.get("collected", False)):
        return False
    if int(target.get("health", PET_MAX_HEALTH)) <= 0:
        return False
    ax = float(attacker.get("x", 0.0))
    ay = float(attacker.get("y", 0.0))
    tx = float(target.get("x", 0.0))
    ty = float(target.get("y", 0.0))
    ar = normalize_pet_size_px(attacker.get("size_px")) * 0.55
    tr = normalize_pet_size_px(target.get("size_px")) * 0.55
    return math.hypot(ax - tx, ay - ty) <= (ar + tr)


def _damage_pet(pet: dict, damage: int) -> bool:
    health = max(0, min(PET_MAX_HEALTH, int(pet.get("health", PET_MAX_HEALTH))))
    if health <= 0:
        return False
    new_hp = max(0, health - max(0, int(damage)))
    if new_hp == health:
        return False
    pet["health"] = new_hp
    if new_hp <= 0:
        pet["vx"] = 0.0
        pet["vy"] = 0.0
        pet["target_x"] = None
        pet["target_y"] = None
        pet["await_move"] = False
        pet["attacking"] = False
        pet["attack_until"] = 0.0
        pet["pause_until"] = 0.0
        pet["sleep_until"] = 0.0
        pid = _find_pet_id(pet)
        if pid is not None:
            _spawn_coin_pile_for_pet(pid, pet)
    return True


async def pet_runtime_loop() -> None:
    while True:
        if pets_runtime:
            changed = False
            now = time.time()
            player_state_updates: list[dict] = []
            magicoin_updates: list[tuple[int, int, int]] = []
            if _expire_old_coin_piles():
                changed = True
            for pid, p in pets_runtime.items():
                if bool(p.get("collected", False)):
                    if now >= float(p.get("respawn_at", 0.0)):
                        _respawn_pet(p, now)
                        _persist_pet_state(pid, p)
                        changed = True
                    continue
                if int(p.get("health", PET_MAX_HEALTH)) <= 0:
                    pet_world = int(p.get("world_id", 1))
                    pet_screen = normalize_world_screen(p.get("world_screen", 1))
                    has_local_player = False
                    for info in players.values():
                        if _player_world_id(info) != pet_world:
                            continue
                        if _player_world_screen(info) != pet_screen:
                            continue
                        has_local_player = True
                        break
                    if not has_local_player:
                        # If a region has no active players, don't let dead pets remain
                        # permanently frozen there from unattended pet-vs-pet combat.
                        idle_since = float(p.get("dead_idle_since", 0.0))
                        if idle_since <= 0.0:
                            p["dead_idle_since"] = now
                        elif now - idle_since >= 6.0:
                            _respawn_pet(p, now)
                            p["dead_idle_since"] = 0.0
                            _persist_pet_state(pid, p)
                            changed = True
                            continue
                    else:
                        p["dead_idle_since"] = 0.0
                    _persist_pet_state(pid, p)
                    continue
                qh = float(p.get("quiz_hold_until", 0.0))
                if qh > now:
                    p["vx"] = 0.0
                    p["vy"] = 0.0
                    p["target_x"] = None
                    p["target_y"] = None
                    p["await_move"] = False
                    p["attack_until"] = 0.0
                    p["attacking"] = False
                    p["pause_until"] = max(float(p.get("pause_until", 0.0)), qh)
                    _persist_pet_state(pid, p)
                    changed = True
                    continue
                mode = p.get("code_mode") or "default"
                if mode == "code":
                    if p.get("program") is None:
                        code = _get_pet_code(pid) or DEFAULT_PET_CODE
                        p["program"] = _parse_pet_code(code)
                        p["pc"] = 0
                    if _pet_step_script(p, now):
                        changed = True
                else:
                    if now >= p.get("next_turn_at", 0):
                        p["vx"] = random.uniform(-1.2, 1.2) * normalize_pet_speed_px(p.get("speed_px"))
                        p["vy"] = random.uniform(-1.0, 1.0) * normalize_pet_speed_px(p.get("speed_px"))
                        p["next_turn_at"] = now + random.uniform(0.7, 2.0)
                        if random.random() < 0.22:
                            p["attack_until"] = now + 0.38
                            p["attack_seq"] = int(p.get("attack_seq", 0)) + 1
                        if random.random() < 0.55:
                            p["pause_until"] = now + random.uniform(0.8, 2.3)
                pet_world = int(p.get("world_id", 1))
                pet_screen = normalize_world_screen(p.get("world_screen", 1))
                attacking = now < p.get("attack_until", 0.0) and bool(p.get("attack_sprite"))
                if attacking and not bool(p.get("attacking", False)):
                    seq = int(p.get("attack_seq", 0))
                    pet_dmg = PET_ATTACK_DAMAGE * 2 if bool(p.get("shiny", False)) else PET_ATTACK_DAMAGE
                    last_hit_players = p.get("last_hit_seq_by_player") or {}
                    for plid, info in players.items():
                        if _player_world_id(info) != pet_world:
                            continue
                        if _player_world_screen(info) != pet_screen:
                            continue
                        if bool(info.get("dead", False)):
                            continue
                        if int(last_hit_players.get(plid, -1)) == seq:
                            continue
                        if _pet_attack_hits_player(p, info):
                            if _damage_player(info, pet_dmg, now):
                                if info.pop("died_just_now", False):
                                    new_balance, delta = _apply_death_penalty(info)
                                    if delta != 0:
                                        try:
                                            uid_loop = int(info.get("user_id") or 0)
                                            if uid_loop > 0:
                                                magicoin_updates.append((uid_loop, new_balance, delta))
                                        except Exception:
                                            pass
                                player_state_updates.append(
                                    {
                                        "world_id": pet_world,
                                        "world_screen": pet_screen,
                                        "msg": {
                                            "type": "player_state",
                                            "id": plid,
                                            "x": info["x"],
                                            "y": info["y"],
                                            "health": info["health"],
                                            "max_health": int(info.get("max_health", PLAYER_MAX_HEALTH)),
                                            "dead": bool(info.get("dead", False)),
                                            "spawn_seq": int(info.get("spawn_seq", 0)),
                                        },
                                    }
                                )
                            last_hit_players[plid] = seq
                    p["last_hit_seq_by_player"] = last_hit_players
                    last_hit_pets = p.get("last_hit_seq_by_pet") or {}
                    for opid, op in pets_runtime.items():
                        if opid == pid:
                            continue
                        if int(op.get("world_id", 1)) != pet_world:
                            continue
                        if normalize_world_screen(op.get("world_screen", 1)) != pet_screen:
                            continue
                        if int(last_hit_pets.get(opid, -1)) == seq:
                            continue
                        if _pet_attack_hits_pet(p, op):
                            if _damage_pet(op, pet_dmg):
                                changed = True
                            last_hit_pets[opid] = seq
                    p["last_hit_seq_by_pet"] = last_hit_pets
                p["attacking"] = attacking
                paused = now < float(p.get("pause_until", 0.0))
                prev_x = float(p.get("x", 0.0))
                prev_y = float(p.get("y", 0.0))
                if not attacking and not paused:
                    tx = p.get("target_x")
                    ty = p.get("target_y")
                    if tx is not None or ty is not None:
                        # move toward script target; do not clear targets here.
                        # clearing happens in _pet_step_script after we finish.
                        tx = float(p["x"]) if tx is None else float(tx)
                        ty = float(p["y"]) if ty is None else float(ty)
                        dx = tx - float(p["x"])
                        dy = ty - float(p["y"])
                        dist = math.hypot(dx, dy) or 1.0
                        step = normalize_pet_speed_px(p.get("speed_px")) * 4.0
                        if dist <= step:
                            p["x"] = tx
                            p["y"] = ty
                        else:
                            p["x"] = float(p["x"]) + dx / dist * step
                            p["y"] = float(p["y"]) + dy / dist * step
                        if abs(dx) > 0.02:
                            p["facing_h"] = -1 if dx < 0 else 1
                    elif mode != "code":
                        # default wander
                        w, h = _shared_viewport_size()
                        p["x"] = max(24, min(w - 24, float(p["x"]) + float(p.get("vx", 0.0))))
                        p["y"] = max(24, min(h - 24, float(p["y"]) + float(p.get("vy", 0.0))))
                        if abs(float(p.get("vx", 0.0))) > 0.02:
                            p["facing_h"] = -1 if float(p.get("vx", 0.0)) < 0 else 1
                    pet_radius = max(16.0, normalize_pet_size_px(p.get("size_px")) * 0.42)
                    pet_w = float(normalize_pet_size_px(p.get("size_px")))
                    if _blocked_by_world_obstacles(
                        pet_world,
                        pet_screen,
                        float(p["x"]),
                        float(p["y"]),
                        pet_radius,
                        width_px=pet_w,
                        height_px=pet_w,
                    ):
                        p["x"] = prev_x
                        p["y"] = prev_y
                        if mode != "code":
                            p["vx"] = -float(p.get("vx", 0.0))
                            p["vy"] = -float(p.get("vy", 0.0))
                if _clamp_pet_to_bounds(p):
                    changed = True
                pet_radius = max(16.0, normalize_pet_size_px(p.get("size_px")) * 0.42)
                resolved_x, moved_right = _resolve_obstacle_overlap_right(
                    pet_world,
                    pet_screen,
                    float(p["x"]),
                    float(p["y"]),
                    pet_radius,
                    width_px=pet_w,
                    height_px=pet_w,
                )
                if moved_right and resolved_x != float(p["x"]):
                    p["x"] = resolved_x
                    changed = True
                    if mode != "code":
                        p["vx"] = abs(float(p.get("vx", 0.0))) or 0.8
                moved_dist = math.hypot(float(p.get("x", 0.0)) - prev_x, float(p.get("y", 0.0)) - prev_y)
                if moved_dist > 0.25:
                    p["stuck_ticks"] = 0
                else:
                    p["stuck_ticks"] = int(p.get("stuck_ticks", 0)) + 1
                    # Recovery guard: if a pet stays effectively stationary for a few seconds,
                    # clear blocking movement intent and force a fresh wander vector.
                    if int(p.get("stuck_ticks", 0)) >= 12 and not bool(p.get("collected", False)):
                        if mode == "code" and (
                            p.get("target_x") is not None or p.get("target_y") is not None or bool(p.get("await_move"))
                        ):
                            p["target_x"] = None
                            p["target_y"] = None
                            p["await_move"] = False
                            p["pc"] = int(p.get("pc", 0)) + 1
                        p["sleep_until"] = min(float(p.get("sleep_until", 0.0)), now)
                        p["pause_until"] = min(float(p.get("pause_until", 0.0)), now)
                        p["next_turn_at"] = now
                        speed_px = normalize_pet_speed_px(p.get("speed_px"))
                        p["vx"] = random.uniform(-1.15, 1.15) * speed_px
                        p["vy"] = random.uniform(-1.0, 1.0) * speed_px
                        p["stuck_ticks"] = 0
                        changed = True
                if _return_pet_to_screen_if_needed(p, now):
                    changed = True
                _persist_pet_state(pid, p)
                changed = True
            if changed:
                await broadcast_pets_state()
            for entry in player_state_updates:
                await broadcast_world(
                    int(entry["world_id"]),
                    dict(entry["msg"]),
                    exclude_id=None,
                    world_screen=normalize_world_screen(entry.get("world_screen", 1)),
                )
            for uid_mc, bal_mc, delta_mc in magicoin_updates:
                await broadcast_user_magicoin(uid_mc, bal_mc, delta=delta_mc, reason="death_penalty")
        # Player respawn (only if scheduled; dead players otherwise stay dead)
        now = time.time()
        for plid, info in list(players.items()):
            if not bool(info.get("dead", False)):
                continue
            if float(info.get("respawn_at", 0.0)) <= 0.0:
                continue
            if now < float(info.get("respawn_at", 0.0)):
                continue
            w = float(info.get("viewport_w", 1360.0))
            h = float(info.get("viewport_h", 760.0))
            info["x"] = w / 2.0
            info["y"] = h / 2.0
            user_max_hp = int(info.get("max_health", PLAYER_MAX_HEALTH))
            info["health"] = user_max_hp
            info["dead"] = False
            info["respawn_at"] = 0.0
            try:
                uid_resp = info.get("user_id")
                if uid_resp:
                    _set_user_health(int(uid_resp), user_max_hp)
                    _set_user_position(int(uid_resp), info["x"], info["y"])
            except Exception:
                pass
            await broadcast_all_world(
                _player_world_id(info),
                {
                    "type": "player_state",
                    "id": plid,
                    "x": info["x"],
                    "y": info["y"],
                    "health": info["health"],
                    "max_health": user_max_hp,
                    "dead": False,
                    "spawn_seq": int(info.get("spawn_seq", 0)),
                },
            )
        await asyncio.sleep(0.22)


async def close_connections_for_user(user_db_id: int) -> None:
    for pid, info in list(players.items()):
        if info.get("user_id") != user_db_id:
            continue
        ws = info.get("ws")
        if ws is None:
            continue
        try:
            await ws.close(code=1008)
        except Exception:
            pass


def others_snapshot(
    except_id: str, world_id: int | None = None, world_screen: int | None = None
) -> list[dict]:
    out: list[dict] = []
    for pid, info in players.items():
        if pid == except_id:
            continue
        if world_id is not None and _player_world_id(info) != int(world_id):
            continue
        if world_screen is not None and _player_world_screen(info) != normalize_world_screen(world_screen):
            continue
        out.append(
            {
                "id": pid,
                "x": info["x"],
                "y": info["y"],
                "name": info.get("name", "?"),
                "sprite": info.get("sprite", ""),
                "account_id": info.get("user_id"),
                "shout_greeting": normalize_shout_greeting(info.get("shout_greeting")),
                "sprite_width_px": normalize_sprite_width_px(info.get("sprite_width_px")),
                "facing_h": normalize_facing_h(info.get("facing_h")),
                "attack_sprite": info.get("attack_sprite", ""),
                "defeat_count": int(info.get("defeat_count", 0)),
                "capture_count": int(info.get("capture_count", 0)),
                "transformed_shiny": bool(info.get("transformed_shiny", False)),
                "health": int(info.get("health", PLAYER_MAX_HEALTH)),
                "max_health": int(info.get("max_health", PLAYER_MAX_HEALTH)),
                "dead": bool(info.get("dead", False)),
                "spawn_seq": int(info.get("spawn_seq", 0)),
                "world_screen": _player_world_screen(info),
            }
        )
    return out


def _player_hitbox_radius(info: dict) -> float:
    return max(18.0, normalize_sprite_width_px(info.get("sprite_width_px")) * 0.45)


def _pet_attack_hits_player(pet: dict, player: dict) -> bool:
    # Pet attacks are radial/melee-ish; no facing requirement.
    px = float(player.get("x", 0.0))
    py = float(player.get("y", 0.0))
    pet_x = float(pet.get("x", 0.0))
    pet_y = float(pet.get("y", 0.0))
    pr = _player_hitbox_radius(player)
    rr = normalize_pet_size_px(pet.get("size_px")) * 0.55
    return math.hypot(pet_x - px, pet_y - py) <= (pr + rr)


def _damage_player(info: dict, damage: int, now: float) -> bool:
    if bool(info.get("dead", False)):
        return False
    cap = int(info.get("max_health", PLAYER_MAX_HEALTH))
    hp = max(0, min(cap, int(info.get("health", cap))))
    nhp = max(0, hp - max(0, int(damage)))
    info["health"] = nhp
    if nhp <= 0:
        info["dead"] = True
        info["respawn_at"] = 0.0
        info["spawn_seq"] = int(info.get("spawn_seq", 0)) + 1
        info["died_just_now"] = True
    if nhp != hp:
        try:
            uid = info.get("user_id")
            if uid:
                _set_user_health(int(uid), nhp)
        except Exception:
            pass
    return nhp != hp


def _apply_death_penalty(info: dict) -> tuple[int, int]:
    """Halve magicoin on death, persist, return (new_balance, delta_negative)."""
    try:
        uid = int(info.get("user_id") or 0)
    except Exception:
        uid = 0
    if uid <= 0:
        return int(info.get("magicoin", 0) or 0), 0
    cur = _get_user_magicoin(uid)
    new_balance = max(0, cur // 2)
    delta = new_balance - cur
    if delta != 0:
        _set_user_magicoin(uid, new_balance)
    info["magicoin"] = new_balance
    return new_balance, delta


def require_login(request: Request) -> sqlite3.Row:
    uid = user_id_from_session_token(_session_token(request))
    if not uid:
        raise HTTPException(status_code=401, detail="login required")
    conn = _connect_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=401, detail="login required")
    return row


@app.get("/")
async def root(request: Request):
    if user_id_from_session_token(_session_token(request)):
        return RedirectResponse("/worlds", status_code=303)
    return RedirectResponse("/login", status_code=303)


@app.get("/login")
async def login_page():
    return FileResponse(STATIC_DIR / "login.html")


def get_login_bg_world_id() -> int:
    raw = get_meta_text("login_bg_world_id", "").strip()
    try:
        wid = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, wid)


def set_login_bg_world_id(wid: int) -> None:
    try:
        v = max(0, int(wid))
    except (TypeError, ValueError):
        v = 0
    set_meta_value("login_bg_world_id", str(v))


def _login_background_url() -> str:
    """Pick configured login background world bg, or a random world bg, or fall
    back to the legacy `static/bg/world.{png,jpg}` files. Returns "" if no
    image is available at all."""
    cache_bust = int(time.time() * 1000)
    wid = get_login_bg_world_id()
    conn = _connect_db()
    try:
        if wid:
            row = conn.execute(
                "SELECT id, background_file FROM worlds WHERE id = ? AND background_file != ''",
                (wid,),
            ).fetchone()
            if row and row["background_file"]:
                return f"/world-background/{int(row['id'])}?v={cache_bust}"
        rows = conn.execute(
            "SELECT id FROM worlds WHERE background_file != '' ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    if rows:
        choice = random.choice(rows)
        return f"/world-background/{int(choice['id'])}?v={cache_bust}"
    if WORLD_BG_PNG.is_file() or WORLD_BG_JPG.is_file():
        return f"/world-background?v={cache_bust}"
    return ""


def _login_music_url_and_volume() -> tuple[str, int]:
    """Return (music_url, volume_pct). Falls back to a random track from
    `static/sfx/music/` if no specific track is configured for the login
    screen."""
    file = get_screen_music_file("login")
    vol = get_screen_music_volume_pct("login")
    if file:
        return f"/music/{file}", vol
    files = _list_world_music_filenames()
    if not files:
        return "", vol
    pick = random.choice(files)
    return f"/music/{pick}", vol


@app.get("/api/login-config")
async def api_login_config():
    bg_url = _login_background_url()
    music_url, music_vol = _login_music_url_and_volume()
    return {
        "background_url": bg_url,
        "music_url": music_url,
        "music_volume_pct": int(music_vol),
    }


@app.get("/play")
async def play_page(request: Request):
    if not user_id_from_session_token(_session_token(request)):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(STATIC_DIR / "play.html")


@app.get("/config")
async def config_page(request: Request):
    if not user_id_from_session_token(_session_token(request)):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(STATIC_DIR / "config.html")


def _filename_from_sprite_url(url: str) -> str:
    if not url:
        return ""
    if url.startswith("/sprites/"):
        return url[len("/sprites/"):].split("?", 1)[0]
    return ""


@app.post("/api/recycle")
async def api_recycle(user: sqlite3.Row = Depends(require_login)):
    candidates: list[dict] = []
    for pid, info in players.items():
        if info.get("user_id") == user["id"]:
            continue
        sf = _filename_from_sprite_url(info.get("sprite", ""))
        if not sf:
            continue
        candidates.append(
            {
                "kind": "player",
                "account_id": int(info.get("user_id") or 0),
                "sprite_file": sf,
                "attack_sprite_file": _filename_from_sprite_url(info.get("attack_sprite", "")),
                "size": int(normalize_sprite_width_px(info.get("sprite_width_px", 48))),
                "shiny": bool(info.get("transformed_shiny", False)),
            }
        )
    for pid, p in pets_runtime.items():
        sf = _filename_from_sprite_url(p.get("sprite", ""))
        if not sf:
            continue
        candidates.append(
            {
                "kind": "pet",
                "pet_id": int(pid),
                "sprite_file": sf,
                "attack_sprite_file": _filename_from_sprite_url(p.get("attack_sprite", "")),
                "size": int(normalize_pet_size_px(p.get("size_px", 52))),
                "shiny": bool(p.get("shiny", False)),
            }
        )
    if not candidates:
        return {"ok": False, "error": "no_targets"}

    ok, balance = _spend_magicoin(int(user["id"]), MAGICOIN_COST_TRANSFORM)
    if not ok:
        return {"ok": False, "error": "insufficient_magicoin", "magicoin": balance}

    target = random.choice(candidates)
    new_size = int(max(16, min(512, target["size"] + 35)))
    sfx_file = ""
    sfx_vol = 100
    conn = _connect_db()
    try:
        if target["kind"] == "pet":
            pid_lookup = int(target.get("pet_id") or 0)
            if pid_lookup:
                row = conn.execute(
                    """
                    SELECT COALESCE(attack_sfx_file, '') AS f,
                           COALESCE(attack_sfx_volume_pct, 100) AS v
                    FROM pets WHERE id = ?
                    """,
                    (pid_lookup,),
                ).fetchone()
                if row:
                    sfx_file = str(row["f"] or "").strip()
                    try:
                        sfx_vol = int(row["v"] or 100)
                    except (TypeError, ValueError):
                        sfx_vol = 100
        elif target["kind"] == "player":
            aid = int(target.get("account_id") or 0)
            if aid:
                row = conn.execute(
                    """
                    SELECT COALESCE(attack_sfx_file, '') AS f,
                           COALESCE(attack_sfx_volume_pct, 100) AS v
                    FROM users WHERE id = ?
                    """,
                    (aid,),
                ).fetchone()
                if row:
                    sfx_file = str(row["f"] or "").strip()
                    try:
                        sfx_vol = int(row["v"] or 100)
                    except (TypeError, ValueError):
                        sfx_vol = 100
        sfx_vol = max(0, min(100, sfx_vol))
        conn.execute(
            """
            UPDATE users
            SET sprite_file = ?, sprite_flipped = 0,
                attack_sprite_file = ?, attack_sprite_flipped = 0,
                sprite_width_px = ?,
                attack_sfx_file = ?, attack_sfx_volume_pct = ?,
                transformed_shiny = ?
            WHERE id = ?
            """,
            (
                target["sprite_file"],
                target["attack_sprite_file"],
                new_size,
                sfx_file,
                sfx_vol,
                1 if bool(target.get("shiny", False)) else 0,
                user["id"],
            ),
        )
        conn.commit()
    finally:
        conn.close()
    new_sprite_url = f"/sprites/{target['sprite_file']}"
    new_attack_url = (
        f"/sprites/{target['attack_sprite_file']}" if target["attack_sprite_file"] else ""
    )
    sfx_url = f"/sfx/{sfx_file}" if sfx_file else ""
    for info in players.values():
        if info.get("user_id") == user["id"]:
            info["sprite"] = new_sprite_url
            info["attack_sprite"] = new_attack_url
            info["sprite_width_px"] = new_size
            info["attack_sfx_url"] = sfx_url
            info["attack_sfx_volume_pct"] = sfx_vol
            info["transformed_shiny"] = bool(target.get("shiny", False))
    await broadcast_all(
        {"type": "user_sprite", "account_id": user["id"], "sprite": new_sprite_url}
    )
    await broadcast_all(
        {
            "type": "user_attack_sprite",
            "account_id": user["id"],
            "attack_sprite": new_attack_url,
        }
    )
    await broadcast_all(
        {
            "type": "user_profile",
            "account_id": user["id"],
            "sprite_width_px": new_size,
            "transformed_shiny": bool(target.get("shiny", False)),
        }
    )
    await broadcast_all(
        {
            "type": "user_attack_sfx",
            "account_id": user["id"],
            "attack_sfx_url": sfx_url,
            "attack_sfx_volume_pct": sfx_vol,
        }
    )
    await broadcast_user_magicoin(int(user["id"]), balance)
    return {"ok": True, "size_px": new_size, "sprite_url": new_sprite_url, "magicoin": balance}


@app.post("/api/recycle/pet")
async def api_recycle_pet(
    user: sqlite3.Row = Depends(require_login),
    pet_id: int = Form(...),
    shiny: int | None = Form(None),
):
    conn = _connect_db()
    try:
        if shiny is None:
            rows = conn.execute(
                """
                SELECT sprite_file, attack_sprite_file, size_px, COALESCE(shiny, 0) AS shiny
                FROM captures
                WHERE user_id = ? AND pet_id = ?
                ORDER BY id DESC
                """,
                (user["id"], int(pet_id)),
            ).fetchall()
        else:
            shiny_i = 1 if int(shiny) else 0
            rows = conn.execute(
                """
                SELECT sprite_file, attack_sprite_file, size_px, COALESCE(shiny, 0) AS shiny
                FROM captures
                WHERE user_id = ? AND pet_id = ? AND COALESCE(shiny, 0) = ?
                ORDER BY id DESC
                """,
                (user["id"], int(pet_id), shiny_i),
            ).fetchall()
    finally:
        conn.close()
    if len(rows) < 1:
        return {"ok": False, "error": "not_enough_captures", "captured": len(rows)}
    pick = rows[0]
    sprite_file = pick["sprite_file"] or ""
    attack_sprite_file = pick["attack_sprite_file"] or ""
    size_px = int(normalize_pet_size_px(pick["size_px"]))
    transformed_shiny = bool(int(pick["shiny"] or 0))
    if not sprite_file:
        return {"ok": False, "error": "no_sprite"}

    ok, balance = _spend_magicoin(int(user["id"]), MAGICOIN_COST_PET_TRANSFORM)
    if not ok:
        return {"ok": False, "error": "insufficient_magicoin", "magicoin": balance}

    sfx_file = ""
    sfx_vol = 100
    conn = _connect_db()
    try:
        row = conn.execute(
            """
            SELECT COALESCE(attack_sfx_file, '') AS f,
                   COALESCE(attack_sfx_volume_pct, 100) AS v
            FROM pets WHERE id = ?
            """,
            (int(pet_id),),
        ).fetchone()
        if row:
            sfx_file = str(row["f"] or "").strip()
            try:
                sfx_vol = int(row["v"] or 100)
            except (TypeError, ValueError):
                sfx_vol = 100
        sfx_vol = max(0, min(100, sfx_vol))
        new_size = int(max(16, min(512, size_px + 35)))
        conn.execute(
            """
            UPDATE users
            SET sprite_file = ?, sprite_flipped = 0,
                attack_sprite_file = ?, attack_sprite_flipped = 0,
                sprite_width_px = ?,
                attack_sfx_file = ?, attack_sfx_volume_pct = ?,
                transformed_shiny = ?
            WHERE id = ?
            """,
            (
                sprite_file,
                attack_sprite_file,
                new_size,
                sfx_file,
                sfx_vol,
                1 if transformed_shiny else 0,
                user["id"],
            ),
        )
        conn.commit()
    finally:
        conn.close()
    new_sprite_url = f"/sprites/{sprite_file}"
    new_attack_url = f"/sprites/{attack_sprite_file}" if attack_sprite_file else ""
    sfx_url = f"/sfx/{sfx_file}" if sfx_file else ""
    for info in players.values():
        if info.get("user_id") == user["id"]:
            info["sprite"] = new_sprite_url
            info["attack_sprite"] = new_attack_url
            info["sprite_width_px"] = new_size
            info["attack_sfx_url"] = sfx_url
            info["attack_sfx_volume_pct"] = sfx_vol
            info["transformed_shiny"] = transformed_shiny
    await broadcast_all(
        {"type": "user_sprite", "account_id": user["id"], "sprite": new_sprite_url}
    )
    await broadcast_all(
        {
            "type": "user_attack_sprite",
            "account_id": user["id"],
            "attack_sprite": new_attack_url,
        }
    )
    await broadcast_all(
        {
            "type": "user_profile",
            "account_id": user["id"],
            "sprite_width_px": new_size,
            "transformed_shiny": transformed_shiny,
        }
    )
    await broadcast_all(
        {
            "type": "user_attack_sfx",
            "account_id": user["id"],
            "attack_sfx_url": sfx_url,
            "attack_sfx_volume_pct": sfx_vol,
        }
    )
    await broadcast_user_magicoin(int(user["id"]), balance)
    return {"ok": True, "size_px": new_size, "sprite_url": new_sprite_url, "magicoin": balance}


def _persist_pet_world(pet_id: int, world_id: int) -> None:
    conn = _connect_db()
    try:
        conn.execute(
            "UPDATE pets SET world_id = ? WHERE id = ?",
            (int(world_id), int(pet_id)),
        )
        conn.commit()
    finally:
        conn.close()


def _spawn_pets_in_world(world_id: int, count: int) -> list[int]:
    """Clone existing pets into the new world, or create blank pets if none exist."""
    new_ids: list[int] = []
    conn = _connect_db()
    try:
        templates = conn.execute(
            """
            SELECT name, sprite_file, sprite_flipped, attack_sprite_file, attack_sprite_flipped,
                   size_px, speed_px, code_mode
            FROM pets
            WHERE world_id != ?
            ORDER BY id
            """,
            (int(world_id),),
        ).fetchall()
    finally:
        conn.close()

    if templates:
        picks = [random.choice(templates) for _ in range(count)]
    else:
        picks = [
            {
                "name": "Pet",
                "sprite_file": "",
                "sprite_flipped": 0,
                "attack_sprite_file": "",
                "attack_sprite_flipped": 0,
                "size_px": 52,
                "speed_px": 1.0,
                "code_mode": "default",
            }
            for _ in range(count)
        ]

    bounds_w, bounds_h = 1360.0, 760.0
    conn = _connect_db()
    try:
        for tpl in picks:
            x = random.uniform(120, bounds_w - 120)
            y = random.uniform(120, bounds_h - 120)
            cur = conn.execute(
                """
                INSERT INTO pets
                    (name, sprite_file, sprite_flipped, attack_sprite_file, attack_sprite_flipped,
                     x, y, facing_h, size_px, speed_px, code_mode, world_id, world_screen,
                     instance_name, shiny)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (tpl["name"] or "Pet"),
                    (tpl["sprite_file"] or ""),
                    int(tpl["sprite_flipped"] or 0),
                    (tpl["attack_sprite_file"] or ""),
                    int(tpl["attack_sprite_flipped"] or 0),
                    x,
                    y,
                    random.choice([-1, 1]),
                    int(tpl["size_px"] or 52),
                    float(tpl["speed_px"] or 1.0),
                    (tpl["code_mode"] or "default"),
                    int(world_id),
                    1,
                    generate_pet_instance_name(),
                    1 if random.random() < SHINY_CHANCE else 0,
                ),
            )
            new_ids.append(int(cur.lastrowid))
        conn.commit()
    finally:
        conn.close()
    return new_ids


def _spawn_pet_from_template(
    conn: sqlite3.Connection, world_id: int, world_screen: int, tpl: sqlite3.Row
) -> int:
    x = random.uniform(120.0, 1240.0)
    y = random.uniform(120.0, 640.0)
    cur = conn.execute(
        """
        INSERT INTO pets
            (name, sprite_file, sprite_flipped, attack_sprite_file, attack_sprite_flipped,
             x, y, facing_h, size_px, speed_px, code_mode, world_id, world_screen,
             attack_sfx_file, attack_sfx_volume_pct, instance_name, shiny)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (tpl["name"] or "Pet")[:24],
            (tpl["sprite_file"] or ""),
            int(tpl["sprite_flipped"] or 0),
            (tpl["attack_sprite_file"] or ""),
            int(tpl["attack_sprite_flipped"] or 0),
            float(x),
            float(y),
            random.choice([-1, 1]),
            normalize_pet_size_px(tpl["size_px"]),
            normalize_pet_speed_px(tpl["speed_px"]),
            (tpl["code_mode"] or "default"),
            int(world_id),
            normalize_world_screen(world_screen),
            (tpl["attack_sfx_file"] or ""),
            max(0, min(100, int(tpl["attack_sfx_volume_pct"] or 100))),
            generate_pet_instance_name(),
            1 if random.random() < SHINY_CHANCE else 0,
        ),
    )
    return int(cur.lastrowid)


def _list_worlds_with_thumbs() -> list[dict]:
    conn = _connect_db()
    try:
        rows = conn.execute(
            """
            SELECT
                w.id,
                w.name,
                w.background_file,
                w.music_file,
                w.music_volume_pct,
                w.created_at,
                COALESCE(w.created_by_user_id, 0) AS created_by_user_id,
                COALESCE(u.username, '') AS created_by_username
            FROM worlds w
            LEFT JOIN users u ON u.id = w.created_by_user_id
            ORDER BY w.id
            """
        ).fetchall()
        pet_rows = conn.execute(
            """
            SELECT
                world_id,
                COUNT(*) AS pet_count,
                GROUP_CONCAT(DISTINCT COALESCE(sprite_file, '')) AS sprite_files
            FROM pets
            GROUP BY world_id
            """
        ).fetchall()
    finally:
        conn.close()
    pet_meta: dict[int, dict] = {}
    for pr in pet_rows:
        wid = int(pr["world_id"] or 1)
        raw = str(pr["sprite_files"] or "")
        urls = []
        if raw:
            for sf in raw.split(","):
                sf = (sf or "").strip()
                if sf:
                    urls.append(f"/sprites/{sf}")
        pet_meta[wid] = {
            "pet_count": int(pr["pet_count"] or 0),
            "pet_sprite_urls": urls[:10],
        }
    out = []
    for r in rows:
        wid = int(r["id"])
        bg = r["background_file"] or ""
        music = r["music_file"] or "" if "music_file" in r.keys() else ""
        vol_pct = int(r["music_volume_pct"]) if "music_volume_pct" in r.keys() and r["music_volume_pct"] is not None else 100
        vol_pct = max(0, min(100, vol_pct))
        url = (
            f"/world-background/{wid}?v={int(time.time() * 1000)}" if bg else ""
        )
        out.append(
            {
                "id": wid,
                "name": r["name"] or f"World {wid}",
                "background_url": url,
                "music_file": music,
                "music_url": (f"/music/{music}" if music else ""),
                "music_volume_pct": vol_pct,
                "created_at": float(r["created_at"] or 0.0),
                "created_by_user_id": int(r["created_by_user_id"] or 0),
                "created_by_username": (r["created_by_username"] or "") if "created_by_username" in r.keys() else "",
                "pet_count": int((pet_meta.get(wid) or {}).get("pet_count", 0)),
                "pet_sprite_urls": (pet_meta.get(wid) or {}).get("pet_sprite_urls", []),
            }
        )
    return out


def _list_world_pets() -> dict[int, list[dict]]:
    return _list_world_pet_types()


def _obstacle_sprite_dims(sprite_file: str) -> tuple[int, int]:
    sid = normalize_attack_sprite_file(sprite_file)
    if not sid:
        return (1, 1)
    p = UPLOAD_DIR / sid
    if not p.is_file():
        return (1, 1)
    try:
        with Image.open(p) as im:
            w = int(im.width or 1)
            h = int(im.height or 1)
            return (max(1, w), max(1, h))
    except Exception:
        return (1, 1)


def load_world_obstacles() -> None:
    world_obstacles.clear()
    conn = _connect_db()
    try:
        rows = conn.execute(
            """
            SELECT
                id,
                world_id,
                COALESCE(world_screen, 1) AS world_screen,
                COALESCE(sprite_file, '') AS sprite_file,
                COALESCE(sprite_flipped, 0) AS sprite_flipped,
                x, y,
                COALESCE(width_px, 120) AS width_px
            FROM world_obstacles
            ORDER BY world_id, id
            """
        ).fetchall()
    finally:
        conn.close()
    for r in rows:
        sf = normalize_attack_sprite_file(r["sprite_file"])
        if not sf:
            continue
        ow, oh = _obstacle_sprite_dims(sf)
        wpx = normalize_obstacle_width_px(r["width_px"], 120)
        hpx = max(24, int(round(wpx * (oh / max(1, ow)))))
        wid = int(r["world_id"] or 1)
        ws = normalize_world_screen(r["world_screen"])
        world_obstacles.setdefault(wid * 10 + ws, []).append(
            {
                "id": int(r["id"]),
                "world_id": wid,
                "world_screen": ws,
                "sprite_file": sf,
                "sprite_flipped": int(r["sprite_flipped"] or 0),
                "sprite_url": f"/sprites/{sf}",
                "x": float(r["x"]),
                "y": float(r["y"]),
                "width_px": wpx,
                "height_px": hpx,
            }
        )


def _obstacle_bucket_key(world_id: int, world_screen: int) -> int:
    return int(world_id) * 10 + normalize_world_screen(world_screen)


def obstacles_snapshot(
    world_id: int | None = None, world_screen: int | None = None
) -> list[dict]:
    if world_id is None:
        out: list[dict] = []
        for key in sorted(world_obstacles.keys()):
            parts = divmod(int(key), 10)
            out.extend(obstacles_snapshot(parts[0], parts[1]))
        return out
    ws = normalize_world_screen(world_screen if world_screen is not None else 1)
    obs = world_obstacles.get(_obstacle_bucket_key(int(world_id), ws), [])
    return [
        {
            "id": int(o["id"]),
            "world_id": int(o["world_id"]),
            "world_screen": normalize_world_screen(o.get("world_screen", 1)),
            "sprite": o["sprite_url"],
            "x": float(o["x"]),
            "y": float(o["y"]),
            "width_px": int(o["width_px"]),
            "height_px": int(o["height_px"]),
            "sprite_flipped": int(o.get("sprite_flipped", 0)),
            # Bottom one-third hitbox.
            "hit_h_px": max(8, int(round(float(o["height_px"]) / 3.0))),
        }
        for o in obs
    ]


def _obstacle_config(world_id: int) -> dict:
    wid = int(world_id)
    conn = _connect_db()
    try:
        row = conn.execute(
            """
            SELECT
                COALESCE(sprite_file, '') AS sprite_file,
                COALESCE(sprite_flipped, 0) AS sprite_flipped,
                COALESCE(min_width_px, 80) AS min_width_px,
                COALESCE(max_width_px, 180) AS max_width_px,
                COALESCE(obstacle_count, 6) AS obstacle_count
            FROM world_obstacle_configs
            WHERE world_id = ?
            """,
            (wid,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {
            "world_id": wid,
            "sprite_file": "",
            "sprite_url": "",
            "sprite_flipped": 0,
            "min_width_px": 80,
            "max_width_px": 180,
            "obstacle_count": 6,
        }
    min_w = normalize_obstacle_width_px(row["min_width_px"], 80)
    max_w = normalize_obstacle_width_px(row["max_width_px"], 180)
    if max_w < min_w:
        max_w = min_w
    sf = normalize_attack_sprite_file(row["sprite_file"])
    return {
        "world_id": wid,
        "sprite_file": sf,
        "sprite_url": f"/sprites/{sf}" if sf else "",
        "sprite_flipped": 1 if int(row["sprite_flipped"] or 0) else 0,
        "min_width_px": min_w,
        "max_width_px": max_w,
        "obstacle_count": normalize_obstacle_count(row["obstacle_count"], 6),
    }


def _upsert_obstacle_config(
    world_id: int,
    sprite_file: str | None = None,
    sprite_flipped: int | None = None,
    min_width_px: int | float | str | None = None,
    max_width_px: int | float | str | None = None,
    obstacle_count: int | float | str | None = None,
) -> None:
    cur = _obstacle_config(world_id)
    sf = cur["sprite_file"] if sprite_file is None else normalize_attack_sprite_file(sprite_file)
    fl = int(cur["sprite_flipped"] if sprite_flipped is None else (1 if int(sprite_flipped) else 0))
    min_w = cur["min_width_px"] if min_width_px is None else normalize_obstacle_width_px(min_width_px, cur["min_width_px"])
    max_w = cur["max_width_px"] if max_width_px is None else normalize_obstacle_width_px(max_width_px, cur["max_width_px"])
    if max_w < min_w:
        max_w = min_w
    cnt = cur["obstacle_count"] if obstacle_count is None else normalize_obstacle_count(obstacle_count, cur["obstacle_count"])
    conn = _connect_db()
    try:
        conn.execute(
            """
            INSERT INTO world_obstacle_configs
                (world_id, sprite_file, sprite_flipped, min_width_px, max_width_px, obstacle_count)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(world_id) DO UPDATE SET
                sprite_file = excluded.sprite_file,
                sprite_flipped = excluded.sprite_flipped,
                min_width_px = excluded.min_width_px,
                max_width_px = excluded.max_width_px,
                obstacle_count = excluded.obstacle_count
            """,
            (int(world_id), sf, fl, min_w, max_w, cnt),
        )
        conn.commit()
    finally:
        conn.close()


def _randomize_world_obstacles(world_id: int) -> int:
    cfg = _obstacle_config(world_id)
    sf = cfg["sprite_file"]
    if not sf:
        return 0
    count = normalize_obstacle_count(cfg["obstacle_count"], 6)
    min_w = normalize_obstacle_width_px(cfg["min_width_px"], 80)
    max_w = normalize_obstacle_width_px(cfg["max_width_px"], 180)
    if max_w < min_w:
        max_w = min_w
    conn = _connect_db()
    try:
        conn.execute("DELETE FROM world_obstacles WHERE world_id = ?", (int(world_id),))
        for ws in (0, 1, 2):
            for _ in range(count):
                wpx = random.randint(min_w, max_w)
                x = random.uniform(120.0, 1240.0)
                y = random.uniform(140.0, 650.0)
                conn.execute(
                    """
                    INSERT INTO world_obstacles
                        (world_id, world_screen, sprite_file, sprite_flipped, x, y, width_px, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(world_id),
                        int(ws),
                        sf,
                        int(cfg["sprite_flipped"]),
                        float(x),
                        float(y),
                        int(wpx),
                        time.time(),
                    ),
                )
        conn.commit()
    finally:
        conn.close()
    load_world_obstacles()
    return count * 3


def _ensure_world_screen_obstacles(world_id: int, world_screen: int) -> int:
    ws = normalize_world_screen(world_screen)
    if ws == 1:
        return 0
    wid = int(world_id)
    key = _obstacle_bucket_key(wid, ws)
    if world_obstacles.get(key):
        return 0
    cfg = _obstacle_config(wid)
    if not cfg.get("sprite_file"):
        return 0
    count = normalize_obstacle_count(cfg.get("obstacle_count", 0), 0)
    if count <= 0:
        return 0
    min_w = normalize_obstacle_width_px(cfg["min_width_px"], 80)
    max_w = normalize_obstacle_width_px(cfg["max_width_px"], 180)
    if max_w < min_w:
        max_w = min_w
    conn = _connect_db()
    try:
        for _ in range(count):
            wpx = random.randint(min_w, max_w)
            x = random.uniform(120.0, 1240.0)
            y = random.uniform(140.0, 650.0)
            conn.execute(
                """
                INSERT INTO world_obstacles
                    (world_id, world_screen, sprite_file, sprite_flipped, x, y, width_px, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (wid, ws, str(cfg["sprite_file"]), int(cfg["sprite_flipped"]), float(x), float(y), int(wpx), time.time()),
            )
        conn.commit()
    finally:
        conn.close()
    load_world_obstacles()
    return count


def _ensure_world_screen_pets(world_id: int, world_screen: int) -> int:
    wid = int(world_id)
    ws = normalize_world_screen(world_screen)
    target = get_world_pet_target_count(wid, 5)
    conn = _connect_db()
    created = 0
    changed = False
    try:
        rows = conn.execute(
            "SELECT id FROM pets WHERE world_id = ? AND COALESCE(world_screen, 1) = ? ORDER BY id",
            (wid, ws),
        ).fetchall()
        existing = [int(r["id"]) for r in rows]
        if len(existing) > target:
            for pid in existing[target:]:
                conn.execute("DELETE FROM pets WHERE id = ?", (pid,))
            changed = True
        if target <= 0:
            conn.commit()
            return 0
        # Pick templates from explicitly assigned world pet types when available.
        trows = conn.execute(
            """
            SELECT
                p.name,
                COALESCE(p.sprite_file, '') AS sprite_file,
                COALESCE(p.sprite_flipped, 0) AS sprite_flipped,
                COALESCE(p.attack_sprite_file, '') AS attack_sprite_file,
                COALESCE(p.attack_sprite_flipped, 0) AS attack_sprite_flipped,
                COALESCE(p.size_px, 52) AS size_px,
                COALESCE(p.speed_px, 1.0) AS speed_px,
                COALESCE(p.code_mode, 'default') AS code_mode,
                COALESCE(p.attack_sfx_file, '') AS attack_sfx_file,
                COALESCE(p.attack_sfx_volume_pct, 100) AS attack_sfx_volume_pct
            FROM world_pet_types wpt
            LEFT JOIN pets p ON p.id = wpt.template_pet_id
            WHERE wpt.world_id = ?
              AND p.id IS NOT NULL
            ORDER BY wpt.template_pet_id
            """,
            (wid,),
        ).fetchall()
        templates = list(trows)
        # Self-heal stale world_pet_types rows that point to deleted pets.
        if not templates:
            stale_rows = conn.execute(
                """
                SELECT wpt.template_pet_id AS tid
                FROM world_pet_types wpt
                LEFT JOIN pets p ON p.id = wpt.template_pet_id
                WHERE wpt.world_id = ? AND p.id IS NULL
                """,
                (wid,),
            ).fetchall()
            if stale_rows:
                for sr in stale_rows:
                    conn.execute(
                        "DELETE FROM world_pet_types WHERE world_id = ? AND template_pet_id = ?",
                        (wid, int(sr["tid"])),
                    )
                changed = True
        if not templates:
            templates = conn.execute(
                """
                SELECT
                    name,
                    COALESCE(sprite_file, '') AS sprite_file,
                    COALESCE(sprite_flipped, 0) AS sprite_flipped,
                    COALESCE(attack_sprite_file, '') AS attack_sprite_file,
                    COALESCE(attack_sprite_flipped, 0) AS attack_sprite_flipped,
                    COALESCE(size_px, 52) AS size_px,
                    COALESCE(speed_px, 1.0) AS speed_px,
                    COALESCE(code_mode, 'default') AS code_mode,
                    COALESCE(attack_sfx_file, '') AS attack_sfx_file,
                    COALESCE(attack_sfx_volume_pct, 100) AS attack_sfx_volume_pct
                FROM pets
                WHERE world_id = ? AND COALESCE(world_screen, 1) = 1
                ORDER BY id
                LIMIT 32
                """,
                (wid,),
            ).fetchall()
        need = max(0, target - min(target, len(existing)))
        for _ in range(need):
            tpl = random.choice(templates) if templates else None
            if not tpl:
                break
            _spawn_pet_from_template(conn, wid, ws, tpl)
            created += 1
            changed = True
        conn.commit()
    finally:
        conn.close()
    if changed:
        load_pets_runtime()
    return created


def _delete_world_obstacles(world_id: int) -> None:
    conn = _connect_db()
    try:
        conn.execute("DELETE FROM world_obstacles WHERE world_id = ?", (int(world_id),))
        conn.commit()
    finally:
        conn.close()
    load_world_obstacles()


def get_world_pet_target_count(world_id: int, default: int = 5) -> int:
    return get_meta_int(f"world_{int(world_id)}_pet_target_count", default, 0, 5)


def set_world_pet_target_count(world_id: int, count: int | float | str | None) -> int:
    value = normalize_world_pet_target_count(count, 5)
    set_meta_value(f"world_{int(world_id)}_pet_target_count", str(value))
    return value


def _list_pet_templates() -> list[dict]:
    conn = _connect_db()
    try:
        rows = conn.execute(
            """
            SELECT
                MIN(id) AS id,
                name,
                COALESCE(sprite_file, '') AS sprite_file
            FROM pets
            GROUP BY
                COALESCE(name, ''),
                COALESCE(sprite_file, ''),
                COALESCE(attack_sprite_file, ''),
                COALESCE(size_px, 52),
                COALESCE(speed_px, 1.0),
                COALESCE(code_mode, 'default'),
                COALESCE(attack_sfx_file, ''),
                COALESCE(attack_sfx_volume_pct, 100)
            ORDER BY name COLLATE NOCASE, id
            """
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        out.append(
            {
                "id": int(r["id"]),
                "name": (r["name"] or "Pet")[:24],
                "sprite_url": f"/sprites/{r['sprite_file']}" if (r["sprite_file"] or "").strip() else "",
            }
        )
    return out


def _list_world_pet_types() -> dict[int, list[dict]]:
    conn = _connect_db()
    try:
        rows = conn.execute(
            """
            SELECT
                wpt.world_id,
                wpt.template_pet_id,
                COALESCE(p.name, 'Pet') AS name,
                COALESCE(p.sprite_file, '') AS sprite_file
            FROM world_pet_types wpt
            LEFT JOIN pets p ON p.id = wpt.template_pet_id
            WHERE p.id IS NOT NULL
            ORDER BY wpt.world_id, p.name COLLATE NOCASE, wpt.template_pet_id
            """
        ).fetchall()
    finally:
        conn.close()
    out: dict[int, list[dict]] = {}
    for r in rows:
        wid = int(r["world_id"] or 1)
        tid = int(r["template_pet_id"] or 0)
        if tid <= 0:
            continue
        out.setdefault(wid, []).append(
            {
                "id": tid,
                "name": (r["name"] or "Pet")[:24],
                "sprite_url": f"/sprites/{r['sprite_file']}" if (r["sprite_file"] or "").strip() else "",
            }
        )
    return out


def _set_world_pet_types(world_id: int, template_ids: list[int]) -> None:
    wid = int(world_id)
    clean: list[int] = []
    seen: set[int] = set()
    for raw in template_ids:
        tid = int(raw or 0)
        if tid <= 0 or tid in seen:
            continue
        seen.add(tid)
        clean.append(tid)
    conn = _connect_db()
    try:
        conn.execute("DELETE FROM world_pet_types WHERE world_id = ?", (wid,))
        for tid in clean:
            conn.execute(
                "INSERT INTO world_pet_types (world_id, template_pet_id, created_at) VALUES (?, ?, ?)",
                (wid, tid, time.time()),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_world_pet_types_if_missing(world_id: int) -> None:
    wid = int(world_id)
    conn = _connect_db()
    try:
        existing = conn.execute(
            "SELECT 1 FROM world_pet_types WHERE world_id = ? LIMIT 1",
            (wid,),
        ).fetchone()
        if existing:
            return
        rows = conn.execute(
            """
            SELECT id
            FROM pets
            WHERE world_id = ? AND COALESCE(world_screen, 1) = 1
            ORDER BY id
            LIMIT 5
            """,
            (wid,),
        ).fetchall()
        for r in rows:
            conn.execute(
                """
                INSERT INTO world_pet_types (world_id, template_pet_id, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(world_id, template_pet_id) DO NOTHING
                """,
                (wid, int(r["id"]), time.time()),
            )
        conn.commit()
    finally:
        conn.close()


@app.get("/worlds")
async def worlds_page(request: Request):
    if not user_id_from_session_token(_session_token(request)):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(STATIC_DIR / "worlds.html")


@app.get("/worlds/new")
async def worlds_new_page(request: Request):
    if not user_id_from_session_token(_session_token(request)):
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/backpack", status_code=303)


@app.get("/api/worlds")
async def api_worlds_list(user: sqlite3.Row = Depends(require_login)):
    worlds = _list_worlds_with_thumbs()
    for w in worlds:
        _seed_world_pet_types_if_missing(int(w["id"]))
    world_pets = _list_world_pets()
    obstacle_configs = {
        int(w["id"]): _obstacle_config(int(w["id"]))
        for w in worlds
    }
    return {
        "current_world_id": int(user["current_world_id"] or 1),
        "worlds": worlds,
        "world_pets": world_pets,
        "pet_catalog": _list_pet_templates(),
        "world_pet_target_count": {int(w["id"]): get_world_pet_target_count(int(w["id"]), 5) for w in worlds},
        "obstacle_configs": obstacle_configs,
        "obstacles_by_world": {
            int(k): obstacles_snapshot(int(k))
            for k in world_obstacles.keys()
        },
        "magicoin": int(user["magicoin"] or 0),
        "cost_enter": MAGICOIN_COST_ENTER_WORLD,
        "cost_create": MAGICOIN_COST_CREATE_WORLD,
        "sfx_volume": get_sfx_volume(),
        "music_volume": get_music_volume(),
        "screen_music": get_screen_music_config(),
    }


@app.post("/api/worlds")
async def api_worlds_create(
    user: sqlite3.Row = Depends(require_login),
    name: str = Form(...),
    file: UploadFile = File(...),
    music_file: str = Form(""),
):
    body = await file.read()
    kind = sniff_image_kind(body[:32])
    if kind not in ("png", "jpg"):
        return {"ok": False, "error": "bad_image"}

    body, kind = maybe_shrink_world_background(body, kind)

    raw_music = (music_file or "").strip()
    if raw_music and raw_music not in _list_world_music_filenames():
        return {"ok": False, "error": "bad_music"}

    ok, balance = _spend_magicoin(int(user["id"]), MAGICOIN_COST_CREATE_WORLD)
    if not ok:
        return {"ok": False, "error": "insufficient_magicoin", "magicoin": balance}

    safe_name = (name or "").strip()[:48] or "World"
    conn = _connect_db()
    try:
        cur = conn.execute(
            "INSERT INTO worlds (name, background_file, created_at, created_by_user_id) VALUES (?, ?, ?, ?)",
            (safe_name, "", time.time(), int(user["id"])),
        )
        new_id = int(cur.lastrowid)
        bg_filename = f"world_{new_id}.{ 'png' if kind == 'png' else 'jpg' }"
        conn.execute(
            "UPDATE worlds SET background_file = ? WHERE id = ?",
            (bg_filename, new_id),
        )
        conn.commit()
    finally:
        conn.close()
    bg_dir = STATIC_DIR / "bg"
    bg_dir.mkdir(parents=True, exist_ok=True)
    (bg_dir / bg_filename).write_bytes(body)

    if raw_music:
        _set_world_music_file(new_id, raw_music)

    new_pet_ids = _spawn_pets_in_world(new_id, random.randint(3, 5))
    if new_pet_ids:
        _set_world_pet_types(new_id, [int(pid) for pid in new_pet_ids[:5]])
        set_world_pet_target_count(new_id, min(5, len(new_pet_ids)))
    load_pets_runtime()
    await broadcast_user_magicoin(int(user["id"]), balance)

    return {
        "ok": True,
        "world_id": new_id,
        "pets": new_pet_ids,
        "magicoin": balance,
        "music_url": _world_music_url(new_id),
    }


@app.post("/api/worlds/{world_id}/enter")
async def api_worlds_enter(
    world_id: int,
    user: sqlite3.Row = Depends(require_login),
):
    if not _world_exists(world_id):
        return {"ok": False, "error": "no_such_world"}
    uid = int(user["id"])
    cur_world = int(user["current_world_id"] or 0)
    cur_health = int(user["health"] or 0) if "health" in user.keys() else 0
    needs_revive = cur_health <= 0
    if cur_world == int(world_id) and not needs_revive:
        return {
            "ok": True,
            "world_id": int(world_id),
            "magicoin": int(user["magicoin"] or 0),
            "skipped_cost": True,
        }
    if cur_world == int(world_id) and needs_revive:
        balance = int(user["magicoin"] or 0)
    else:
        ok, balance = _spend_magicoin(uid, MAGICOIN_COST_ENTER_WORLD)
        if not ok:
            return {"ok": False, "error": "insufficient_magicoin", "magicoin": balance}
    _set_user_current_world(uid, int(world_id))
    _set_user_current_world_screen(uid, 1)
    _set_user_position(uid, -1.0, -1.0)
    _set_user_health(uid, PLAYER_MAX_HEALTH)
    _ensure_world_screen_pets(int(world_id), 1)
    _ensure_world_screen_obstacles(int(world_id), 0)
    _ensure_world_screen_obstacles(int(world_id), 2)
    _ensure_world_screen_pets(int(world_id), 0)
    _ensure_world_screen_pets(int(world_id), 2)
    await broadcast_user_magicoin(uid, balance)
    return {"ok": True, "world_id": int(world_id), "magicoin": balance}


@app.post("/api/worlds/{world_id}/name")
async def api_worlds_rename(
    world_id: int,
    user: sqlite3.Row = Depends(require_login),
    name: str = Form(...),
):
    if not _world_exists(world_id):
        return {"ok": False, "error": "no_such_world"}
    safe_name = (name or "").strip()[:48] or f"World {world_id}"
    conn = _connect_db()
    try:
        conn.execute("UPDATE worlds SET name = ? WHERE id = ?", (safe_name, int(world_id)))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "name": safe_name}


@app.post("/api/worlds/{world_id}/background")
async def api_worlds_background(
    world_id: int,
    user: sqlite3.Row = Depends(require_login),
    file: UploadFile = File(...),
):
    if not _world_exists(world_id):
        return {"ok": False, "error": "no_such_world"}
    body = await file.read()
    kind = sniff_image_kind(body[:32])
    if kind not in ("png", "jpg"):
        return {"ok": False, "error": "bad_image"}
    body, kind = maybe_shrink_world_background(body, kind)
    bg_filename = f"world_{world_id}.{ 'png' if kind == 'png' else 'jpg' }"
    other_ext = "jpg" if kind == "png" else "png"
    bg_dir = STATIC_DIR / "bg"
    bg_dir.mkdir(parents=True, exist_ok=True)
    (bg_dir / f"world_{world_id}.{other_ext}").unlink(missing_ok=True)
    (bg_dir / bg_filename).write_bytes(body)
    conn = _connect_db()
    try:
        conn.execute(
            "UPDATE worlds SET background_file = ? WHERE id = ?",
            (bg_filename, int(world_id)),
        )
        conn.commit()
    finally:
        conn.close()
    await notify_world_background_changed(int(world_id))
    return {"ok": True, "background_url": f"/world-background/{int(world_id)}?v={int(time.time() * 1000)}"}


@app.post("/api/worlds/{world_id}/delete")
async def api_worlds_delete(
    world_id: int,
    user: sqlite3.Row = Depends(require_login),
):
    wid = int(world_id)
    conn = _connect_db()
    try:
        world_row = conn.execute(
            "SELECT id FROM worlds WHERE id = ?",
            (wid,),
        ).fetchone()
        if not world_row:
            return {"ok": False, "error": "no_such_world"}

        fallback_row = conn.execute(
            "SELECT id FROM worlds WHERE id <> ? ORDER BY id LIMIT 1",
            (wid,),
        ).fetchone()
        if not fallback_row:
            return {"ok": False, "error": "cannot_delete_last_world"}
        fallback_id = int(fallback_row["id"])

        conn.execute(
            "UPDATE users SET current_world_id = ? WHERE current_world_id = ?",
            (fallback_id, wid),
        )
        conn.execute("DELETE FROM pets WHERE world_id = ?", (wid,))
        conn.execute("DELETE FROM world_obstacles WHERE world_id = ?", (wid,))
        conn.execute("DELETE FROM world_obstacle_configs WHERE world_id = ?", (wid,))
        conn.execute("DELETE FROM worlds WHERE id = ?", (wid,))
        conn.commit()
    finally:
        conn.close()

    for cid, c in list(coin_piles.items()):
        if int(c.get("world_id", 1) or 1) == wid:
            coin_piles.pop(cid, None)

    load_pets_runtime()
    load_world_obstacles()
    await broadcast_pets_state(wid)
    await broadcast_pets_state(fallback_id)

    # Force active clients in the deleted world to reconnect into fallback world.
    affected_user_ids: set[int] = set()
    for info in players.values():
        if _player_world_id(info) == wid:
            uid_i = int(info.get("user_id") or 0)
            if uid_i > 0:
                affected_user_ids.add(uid_i)
    for uid_i in affected_user_ids:
        await close_connections_for_user(uid_i)

    return {"ok": True, "deleted_world_id": wid, "fallback_world_id": fallback_id}


@app.post("/api/worlds/{world_id}/pets/add-existing")
async def api_worlds_add_existing_pet(
    world_id: int,
    user: sqlite3.Row = Depends(require_login),
    template_pet_id: int = Form(...),
):
    wid = int(world_id)
    if not _world_exists(wid):
        return {"ok": False, "error": "no_such_world"}
    conn = _connect_db()
    try:
        exists = conn.execute("SELECT id FROM pets WHERE id = ?", (int(template_pet_id),)).fetchone()
        if not exists:
            return {"ok": False, "error": "no_such_template"}
        conn.execute(
            """
            INSERT INTO world_pet_types (world_id, template_pet_id, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(world_id, template_pet_id) DO NOTHING
            """,
            (wid, int(template_pet_id), time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    _ensure_world_screen_pets(wid, 1)
    _ensure_world_screen_pets(wid, 0)
    _ensure_world_screen_pets(wid, 2)
    await broadcast_pets_state(wid)
    return {"ok": True, "template_pet_id": int(template_pet_id)}


@app.post("/api/worlds/{world_id}/pets/{pet_id}/delete")
async def api_worlds_delete_pet(
    world_id: int,
    pet_id: int,
    user: sqlite3.Row = Depends(require_login),
):
    wid = int(world_id)
    tid = int(pet_id)
    if not _world_exists(wid):
        return {"ok": False, "error": "no_such_world"}
    conn = _connect_db()
    try:
        conn.execute(
            "DELETE FROM world_pet_types WHERE world_id = ? AND template_pet_id = ?",
            (wid, tid),
        )
        conn.commit()
    finally:
        conn.close()
    _ensure_world_screen_pets(wid, 1)
    _ensure_world_screen_pets(wid, 0)
    _ensure_world_screen_pets(wid, 2)
    await broadcast_pets_state(wid)
    return {"ok": True, "template_pet_id": tid}


@app.post("/api/worlds/{world_id}/pets/settings")
async def api_worlds_pet_settings(
    world_id: int,
    user: sqlite3.Row = Depends(require_login),
    target_count: int = Form(...),
):
    wid = int(world_id)
    if not _world_exists(wid):
        return {"ok": False, "error": "no_such_world"}
    final_count = set_world_pet_target_count(wid, target_count)
    _ensure_world_screen_pets(wid, 1)
    _ensure_world_screen_pets(wid, 0)
    _ensure_world_screen_pets(wid, 2)
    await broadcast_pets_state(wid)
    return {"ok": True, "target_count": final_count}


@app.post("/api/worlds/{world_id}/obstacles/upload")
async def api_world_obstacles_upload(
    world_id: int,
    user: sqlite3.Row = Depends(require_login),
    file: UploadFile = File(...),
):
    wid = int(world_id)
    if not _world_exists(wid):
        return {"ok": False, "error": "no_such_world"}
    body = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(body) > MAX_UPLOAD_BYTES:
        return {"ok": False, "error": "file_too_big"}
    kind = sniff_image_kind(body[:32])
    if kind not in ("png", "jpg", "gif", "webp"):
        return {"ok": False, "error": "bad_image"}
    ext = extension_for_kind(kind)
    fname = f"{uuid.uuid4().hex}{ext}"
    try:
        (UPLOAD_DIR / fname).write_bytes(body)
    except OSError:
        return {"ok": False, "error": "server"}
    old = _obstacle_config(wid).get("sprite_file", "")
    if old:
        archive_user_sprite_file(str(old), "world_obstacle")
    _upsert_obstacle_config(wid, sprite_file=fname, sprite_flipped=0)
    return {"ok": True, "config": _obstacle_config(wid)}


@app.post("/api/worlds/{world_id}/obstacles/settings")
async def api_world_obstacles_settings(
    world_id: int,
    user: sqlite3.Row = Depends(require_login),
    min_width_px: int = Form(...),
    max_width_px: int = Form(...),
    obstacle_count: int = Form(...),
):
    wid = int(world_id)
    if not _world_exists(wid):
        return {"ok": False, "error": "no_such_world"}
    _upsert_obstacle_config(
        wid,
        min_width_px=min_width_px,
        max_width_px=max_width_px,
        obstacle_count=obstacle_count,
    )
    return {"ok": True, "config": _obstacle_config(wid)}


@app.post("/api/worlds/{world_id}/obstacles/flip")
async def api_world_obstacles_flip(
    world_id: int,
    user: sqlite3.Row = Depends(require_login),
):
    wid = int(world_id)
    cfg = _obstacle_config(wid)
    sf = str(cfg.get("sprite_file") or "")
    if not sf:
        return {"ok": False, "error": "no_obstacle_sprite"}
    p = UPLOAD_DIR / sf
    if not p.is_file():
        return {"ok": False, "error": "no_obstacle_sprite"}
    try:
        flip_sprite_file_on_disk(p)
    except (OSError, UnidentifiedImageError, ValueError):
        return {"ok": False, "error": "flip_failed"}
    next_flipped = 0 if int(cfg.get("sprite_flipped") or 0) else 1
    _upsert_obstacle_config(wid, sprite_flipped=next_flipped)
    conn = _connect_db()
    try:
        conn.execute(
            "UPDATE world_obstacles SET sprite_flipped = ? WHERE world_id = ?",
            (int(next_flipped), wid),
        )
        conn.commit()
    finally:
        conn.close()
    load_world_obstacles()
    await broadcast_pets_state(wid)
    return {"ok": True, "config": _obstacle_config(wid)}


@app.post("/api/worlds/{world_id}/obstacles/trim-white")
async def api_world_obstacles_trim_white(
    world_id: int,
    user: sqlite3.Row = Depends(require_login),
):
    wid = int(world_id)
    cfg = _obstacle_config(wid)
    sf = str(cfg.get("sprite_file") or "")
    if not sf:
        return {"ok": False, "error": "no_obstacle_sprite"}
    old_path = UPLOAD_DIR / sf
    if not old_path.is_file():
        return {"ok": False, "error": "no_obstacle_sprite"}
    new_file = f"{uuid.uuid4().hex}.png"
    new_path = UPLOAD_DIR / new_file
    try:
        trim_sprite_white_margin_to_png(old_path, dst_path=new_path)
    except (OSError, UnidentifiedImageError, ValueError):
        return {"ok": False, "error": "trim_failed"}
    archive_user_sprite_file(sf, "world_obstacle")
    _upsert_obstacle_config(wid, sprite_file=new_file, sprite_flipped=0)
    conn = _connect_db()
    try:
        conn.execute(
            "UPDATE world_obstacles SET sprite_file = ?, sprite_flipped = 0 WHERE world_id = ?",
            (new_file, wid),
        )
        conn.commit()
    finally:
        conn.close()
    load_world_obstacles()
    await broadcast_pets_state(wid)
    return {"ok": True, "config": _obstacle_config(wid)}


@app.post("/api/worlds/{world_id}/obstacles/trim-bounds")
async def api_world_obstacles_trim_bounds(
    world_id: int,
    user: sqlite3.Row = Depends(require_login),
):
    wid = int(world_id)
    cfg = _obstacle_config(wid)
    sf = str(cfg.get("sprite_file") or "")
    if not sf:
        return {"ok": False, "error": "no_obstacle_sprite"}
    old_path = UPLOAD_DIR / sf
    if not old_path.is_file():
        return {"ok": False, "error": "no_obstacle_sprite"}
    new_file = f"{uuid.uuid4().hex}.png"
    new_path = UPLOAD_DIR / new_file
    try:
        trim_sprite_empty_space_to_png(old_path, dst_path=new_path)
    except (OSError, UnidentifiedImageError, ValueError):
        return {"ok": False, "error": "trim_failed"}
    archive_user_sprite_file(sf, "world_obstacle")
    _upsert_obstacle_config(wid, sprite_file=new_file, sprite_flipped=0)
    conn = _connect_db()
    try:
        conn.execute(
            "UPDATE world_obstacles SET sprite_file = ?, sprite_flipped = 0 WHERE world_id = ?",
            (new_file, wid),
        )
        conn.commit()
    finally:
        conn.close()
    load_world_obstacles()
    await broadcast_pets_state(wid)
    return {"ok": True, "config": _obstacle_config(wid)}


@app.post("/api/worlds/{world_id}/obstacles/randomize")
async def api_world_obstacles_randomize(
    world_id: int,
    user: sqlite3.Row = Depends(require_login),
):
    wid = int(world_id)
    if not _world_exists(wid):
        return {"ok": False, "error": "no_such_world"}
    cfg = _obstacle_config(wid)
    if not cfg.get("sprite_file"):
        return {"ok": False, "error": "no_obstacle_sprite"}
    created = _randomize_world_obstacles(wid)
    await broadcast_pets_state(wid)
    return {
        "ok": True,
        "count": int(created),
        "obstacles": obstacles_snapshot(wid),
        "config": _obstacle_config(wid),
    }


@app.post("/api/worlds/{world_id}/obstacles/delete-all")
async def api_world_obstacles_delete_all(
    world_id: int,
    user: sqlite3.Row = Depends(require_login),
):
    wid = int(world_id)
    if not _world_exists(wid):
        return {"ok": False, "error": "no_such_world"}
    _delete_world_obstacles(wid)
    await broadcast_pets_state(wid)
    return {"ok": True, "count": 0}


@app.get("/api/worlds/music/list")
async def api_world_music_list(user: sqlite3.Row = Depends(require_login)):
    return {"files": _list_world_music_filenames()}


@app.post("/api/worlds/{world_id}/music")
async def api_worlds_set_music(
    world_id: int,
    user: sqlite3.Row = Depends(require_login),
    music_file: str = Form(""),
):
    if not _world_exists(world_id):
        return {"ok": False, "error": "no_such_world"}
    raw = (music_file or "").strip()
    if raw and raw not in _list_world_music_filenames():
        return {"ok": False, "error": "no_such_file"}
    _set_world_music_file(int(world_id), raw)
    music_url = _world_music_url(int(world_id))
    vol_pct = _world_music_volume_pct(int(world_id))
    await broadcast_world(
        int(world_id),
        {
            "type": "music",
            "music_url": music_url,
            "world_music_volume_pct": vol_pct,
        },
    )
    return {"ok": True, "music_url": music_url, "music_file": raw, "music_volume_pct": vol_pct}


@app.post("/api/worlds/{world_id}/music-volume")
async def api_worlds_set_music_volume(
    world_id: int,
    user: sqlite3.Row = Depends(require_login),
    volume_pct: int = Form(...),
):
    if not _world_exists(world_id):
        return {"ok": False, "error": "no_such_world"}
    _set_world_music_volume_pct(int(world_id), volume_pct)
    vol_pct = _world_music_volume_pct(int(world_id))
    music_url = _world_music_url(int(world_id))
    await broadcast_world(
        int(world_id),
        {
            "type": "music",
            "music_url": music_url,
            "world_music_volume_pct": vol_pct,
        },
    )
    return {"ok": True, "music_volume_pct": vol_pct}


@app.get("/backpack")
async def backpack_page(request: Request):
    if not user_id_from_session_token(_session_token(request)):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(STATIC_DIR / "backpack.html")


@app.get("/api/backpack")
async def api_backpack(user: sqlite3.Row = Depends(require_login)):
    conn = _connect_db()
    try:
        rows = conn.execute(
            """
            SELECT id, pet_id, pet_name, sprite_file, attack_sprite_file, size_px, captured_at,
                   COALESCE(instance_name, '') AS instance_name,
                   COALESCE(shiny, 0) AS shiny,
                   COALESCE(release_base_magicoin, 0) AS release_base_magicoin
            FROM captures
            WHERE user_id = ?
            ORDER BY id DESC
            """,
            (user["id"],),
        ).fetchall()
        u = conn.execute(
            """
            SELECT username,
                   COALESCE(correct_answers, 0) AS correct_answers,
                   COALESCE(magicoin, 0) AS magicoin,
                   COALESCE(health, ?) AS health
            FROM users WHERE id = ?
            """,
            (PLAYER_MAX_HEALTH, user["id"]),
        ).fetchone()
    finally:
        conn.close()
    captures = []
    counts: dict[int, int] = {}
    counts_by_variant: dict[str, int] = {}
    counts_by_tier: dict[str, int] = {}
    for r in rows:
        sprite_url = f"/sprites/{r['sprite_file']}" if r["sprite_file"] else ""
        attack_sprite_url = (
            f"/sprites/{r['attack_sprite_file']}" if r["attack_sprite_file"] else ""
        )
        pet_id = int(r["pet_id"])
        shiny_i = 1 if int(r["shiny"] or 0) else 0
        rel_base = int(r["release_base_magicoin"] or 0)
        if rel_base <= 0:
            rel_base = CAPTURE_RELEASE_MIN_MAGICOIN
        counts[pet_id] = counts.get(pet_id, 0) + 1
        variant_key = f"{pet_id}:{shiny_i}"
        counts_by_variant[variant_key] = counts_by_variant.get(variant_key, 0) + 1
        tier = "golden" if shiny_i else "regular"
        tier_key = f"{pet_id}:{tier}"
        counts_by_tier[tier_key] = counts_by_tier.get(tier_key, 0) + 1
        captures.append(
            {
                "id": r["id"],
                "pet_id": pet_id,
                "pet_name": r["pet_name"] or "Pet",
                "instance_name": r["instance_name"] or "",
                "shiny": bool(int(r["shiny"] or 0)),
                "sprite_url": sprite_url,
                "attack_sprite_url": attack_sprite_url,
                "sprite_file": r["sprite_file"] or "",
                "attack_sprite_file": r["attack_sprite_file"] or "",
                "size_px": int(r["size_px"]),
                "captured_at": float(r["captured_at"]),
                "release_base_magicoin": rel_base,
                "release_magicoin": rel_base * (CAPTURE_RELEASE_SHINY_MULT if shiny_i else 1),
            }
        )
    user_max_hp = _get_user_max_health(int(user["id"]))
    return {
        "username": u["username"] if u else "?",
        "correct_answers": int(u["correct_answers"]) if u else 0,
        "magicoin": int(u["magicoin"]) if u else 0,
        "health": int(u["health"]) if u else user_max_hp,
        "max_health": user_max_hp,
        "max_health_cap": PLAYER_MAX_HEALTH_CAP,
        "coin_icon_url": "/static-sprites/magicoin/coin.png",
        "sfx_volume": get_sfx_volume(),
        "music_volume": get_music_volume(),
        "event_sfx": get_event_sfx_config(),
        "screen_music": get_screen_music_config(),
        "shop": {
            "health_potion_cost": MAGICOIN_COST_HEALTH_POTION,
            "transform_cost": MAGICOIN_COST_PET_TRANSFORM,
            "max_hp_boost_cost": MAGICOIN_COST_MAX_HP_BOOST,
            "max_hp_boost_amount": MAX_HP_BOOST_AMOUNT,
            "max_hp_cap": PLAYER_MAX_HEALTH_CAP,
        },
        "captures": captures,
        "counts": counts,
        "counts_by_variant": counts_by_variant,
        "counts_by_tier": counts_by_tier,
    }


@app.post("/api/captures/{capture_id}/release")
async def api_release_capture(
    capture_id: int,
    user: sqlite3.Row = Depends(require_login),
):
    cid = int(capture_id)
    uid = int(user["id"])
    conn = _connect_db()
    payout = 0
    shiny_i = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT
                id,
                COALESCE(shiny, 0) AS shiny,
                COALESCE(release_base_magicoin, 0) AS release_base_magicoin
            FROM captures
            WHERE id = ? AND user_id = ?
            """,
            (cid, uid),
        ).fetchone()
        if not row:
            conn.rollback()
            return {"ok": False, "error": "no_capture"}
        shiny_i = 1 if int(row["shiny"] or 0) else 0
        base = int(row["release_base_magicoin"] or 0)
        if base <= 0:
            base = CAPTURE_RELEASE_MIN_MAGICOIN
        payout = base * (CAPTURE_RELEASE_SHINY_MULT if shiny_i else 1)
        bal_row = conn.execute(
            "SELECT COALESCE(magicoin, 0) AS m FROM users WHERE id = ?",
            (uid,),
        ).fetchone()
        cur_balance = int(bal_row["m"] or 0) if bal_row else 0
        new_balance = max(0, cur_balance + payout)
        conn.execute("DELETE FROM captures WHERE id = ? AND user_id = ?", (cid, uid))
        conn.execute("UPDATE users SET magicoin = ? WHERE id = ?", (new_balance, uid))
        conn.commit()
    finally:
        conn.close()
    for info in players.values():
        if int(info.get("user_id") or 0) == uid:
            info["magicoin"] = new_balance
            info["capture_count"] = max(0, int(info.get("capture_count", 0) or 0) - 1)
    await broadcast_user_magicoin(uid, new_balance, delta=payout, reason="capture_release")
    return {
        "ok": True,
        "capture_id": cid,
        "magicoin": int(new_balance),
        "payout": int(payout),
        "shiny": bool(shiny_i),
    }


@app.post("/api/shop/health-potion")
async def api_shop_health_potion(user: sqlite3.Row = Depends(require_login)):
    user_max_hp = _get_user_max_health(int(user["id"]))
    current_health = (
        int(user["health"]) if "health" in user.keys() and user["health"] is not None else user_max_hp
    )
    if current_health >= user_max_hp:
        return {"ok": False, "error": "already_full", "magicoin": int(user["magicoin"] or 0)}
    ok, balance = _spend_magicoin(int(user["id"]), MAGICOIN_COST_HEALTH_POTION)
    if not ok:
        return {"ok": False, "error": "insufficient_magicoin", "magicoin": balance}
    _set_user_health(int(user["id"]), user_max_hp)
    for pid, info in list(players.items()):
        if info.get("user_id") != int(user["id"]):
            continue
        info["health"] = user_max_hp
        info["max_health"] = user_max_hp
        info["dead"] = False
        await broadcast_all_world(
            _player_world_id(info),
            {
                "type": "player_state",
                "id": pid,
                "x": info["x"],
                "y": info["y"],
                "health": user_max_hp,
                "max_health": user_max_hp,
                "dead": False,
                "spawn_seq": int(info.get("spawn_seq", 0)),
            },
        )
    await broadcast_user_magicoin(int(user["id"]), balance)
    return {"ok": True, "magicoin": balance, "health": user_max_hp, "max_health": user_max_hp}


@app.post("/api/shop/max-hp")
async def api_shop_max_hp(user: sqlite3.Row = Depends(require_login)):
    current_max = _get_user_max_health(int(user["id"]))
    if current_max >= PLAYER_MAX_HEALTH_CAP:
        return {
            "ok": False,
            "error": "at_cap",
            "magicoin": int(user["magicoin"] or 0),
            "max_health": current_max,
            "max_health_cap": PLAYER_MAX_HEALTH_CAP,
        }
    ok, balance = _spend_magicoin(int(user["id"]), MAGICOIN_COST_MAX_HP_BOOST)
    if not ok:
        return {"ok": False, "error": "insufficient_magicoin", "magicoin": balance}
    new_max = _set_user_max_health(int(user["id"]), current_max + MAX_HP_BOOST_AMOUNT)
    current_health = (
        int(user["health"]) if "health" in user.keys() and user["health"] is not None else new_max
    )
    new_health = min(new_max, current_health + MAX_HP_BOOST_AMOUNT)
    _set_user_health(int(user["id"]), new_health)
    for pid, info in list(players.items()):
        if info.get("user_id") != int(user["id"]):
            continue
        info["max_health"] = new_max
        info["health"] = new_health
        await broadcast_all_world(
            _player_world_id(info),
            {
                "type": "player_state",
                "id": pid,
                "x": info["x"],
                "y": info["y"],
                "health": new_health,
                "max_health": new_max,
                "dead": bool(info.get("dead", False)),
                "spawn_seq": int(info.get("spawn_seq", 0)),
            },
        )
    await broadcast_user_magicoin(
        int(user["id"]), balance,
        delta=-MAGICOIN_COST_MAX_HP_BOOST, reason="max_hp_boost",
    )
    return {
        "ok": True,
        "magicoin": balance,
        "max_health": new_max,
        "health": new_health,
        "max_health_cap": PLAYER_MAX_HEALTH_CAP,
    }


@app.get("/api/shop/pet/options")
async def api_shop_pet_options(user: sqlite3.Row = Depends(require_login)):
    return {
        "cost": get_buy_pet_cost(),
        "size_min": get_pet_size_min(),
        "size_max": get_pet_size_max(),
        "magicoin": int(user["magicoin"] or 0),
        "worlds": _list_worlds_with_thumbs(),
    }


@app.post("/api/shop/pet")
async def api_shop_buy_pet(
    user: sqlite3.Row = Depends(require_login),
    name: str = Form("Pet"),
    world_id: int = Form(...),
    size_px: int = Form(...),
    sprite: UploadFile = File(...),
    attack_sprite: UploadFile = File(None),
    attack_sfx_file: str = Form(""),
    attack_sfx_volume_pct: int = Form(100),
):
    if not _world_exists(int(world_id)):
        return {"ok": False, "error": "no_such_world"}
    cost = get_buy_pet_cost()
    smin = get_pet_size_min()
    smax = get_pet_size_max()
    size = max(smin, min(smax, int(size_px)))
    safe_name = (name or "Pet").strip()[:24] or "Pet"

    sprite_body = await sprite.read()
    sprite_kind = sniff_image_kind(sprite_body[:32])
    if sprite_kind is None:
        return {"ok": False, "error": "bad_image"}
    sprite_ext = extension_for_kind(sprite_kind)
    sprite_fname = f"{uuid.uuid4().hex}{sprite_ext}"

    attack_fname = ""
    attack_body: bytes | None = None
    if attack_sprite is not None:
        try:
            ab = await attack_sprite.read()
        except Exception:
            ab = b""
        if ab:
            akind = sniff_image_kind(ab[:32])
            if akind is None:
                return {"ok": False, "error": "bad_attack_image"}
            attack_body = ab
            attack_fname = f"{uuid.uuid4().hex}{extension_for_kind(akind)}"

    ok, balance = _spend_magicoin(int(user["id"]), cost)
    if not ok:
        return {"ok": False, "error": "insufficient_magicoin", "magicoin": balance, "cost": cost}

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    try:
        (UPLOAD_DIR / sprite_fname).write_bytes(sprite_body)
        if attack_fname and attack_body is not None:
            (UPLOAD_DIR / attack_fname).write_bytes(attack_body)
    except OSError:
        _set_user_magicoin(int(user["id"]), balance + cost)
        return {"ok": False, "error": "server"}

    spawn_x = PET_CENTER_X + random.uniform(-60, 60)
    spawn_y = PET_CENTER_Y + random.uniform(-40, 40)

    safe_sfx_file = (attack_sfx_file or "").strip()
    if safe_sfx_file and safe_sfx_file not in _list_sfx_filenames():
        safe_sfx_file = ""
    try:
        safe_sfx_vol = int(attack_sfx_volume_pct)
    except (TypeError, ValueError):
        safe_sfx_vol = 100
    safe_sfx_vol = max(0, min(100, safe_sfx_vol))

    inst_name = generate_pet_instance_name()
    is_shiny = 1 if random.random() < SHINY_CHANCE else 0
    conn = _connect_db()
    try:
        cur = conn.execute(
            """
            INSERT INTO pets (
                name, sprite_file, attack_sprite_file,
                x, y, facing_h, size_px, speed_px, world_id, code_mode,
                attack_sfx_file, attack_sfx_volume_pct,
                instance_name, shiny
            ) VALUES (?, ?, ?, ?, ?, 1, ?, 0.6, ?, 'default', ?, ?, ?, ?)
            """,
            (
                safe_name,
                sprite_fname,
                attack_fname,
                spawn_x,
                spawn_y,
                size,
                int(world_id),
                safe_sfx_file,
                safe_sfx_vol,
                inst_name,
                is_shiny,
            ),
        )
        conn.commit()
        new_pet_id = int(cur.lastrowid)
    finally:
        conn.close()

    load_pets_runtime()
    await broadcast_user_magicoin(
        int(user["id"]), balance, delta=-cost, reason="buy_pet"
    )
    await broadcast_pets_state(int(world_id))
    return {
        "ok": True,
        "pet_id": new_pet_id,
        "name": safe_name,
        "world_id": int(world_id),
        "magicoin": balance,
        "cost": cost,
    }


@app.post("/logout")
async def logout(request: Request):
    return _redirect_clear_session(request, "/login")


@app.post("/login")
async def login_submit(username: str = Form(...), password: str = Form(...)):
    name = username.strip()[:32]
    conn = _connect_db()
    try:
        row = conn.execute(
            "SELECT id, password FROM users WHERE username = ?",
            (name,),
        ).fetchone()
    finally:
        conn.close()
    if row is None or password != row["password"]:
        return RedirectResponse("/login?error=bad_login", status_code=303)
    return _redirect_with_session_cookie("/worlds", int(row["id"]))


@app.post("/register")
async def register_submit(
    username: str = Form(...),
    password: str = Form(...),
    sprite: UploadFile = File(...),
):
    name = username.strip()[:32]
    if len(name) < 2:
        return RedirectResponse("/login?error=short_name", status_code=303)
    if len(password) < 4:
        return RedirectResponse("/login?error=weak_password", status_code=303)

    body = await sprite.read(MAX_UPLOAD_BYTES + 1)
    if len(body) > MAX_UPLOAD_BYTES:
        return RedirectResponse("/login?error=file_too_big", status_code=303)
    kind = sniff_image_kind(body[:32])
    if kind is None:
        return RedirectResponse("/login?error=bad_image", status_code=303)

    ext = extension_for_kind(kind)
    fname = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / fname

    conn = _connect_db()
    try:
        try:
            dest.write_bytes(body)
            conn.execute(
                "INSERT INTO users (username, password, sprite_file) VALUES (?, ?, ?)",
                (name, password, fname),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            if dest.exists():
                dest.unlink(missing_ok=True)
            return RedirectResponse("/login?error=taken", status_code=303)
        except Exception:
            conn.rollback()
            if dest.exists():
                dest.unlink(missing_ok=True)
            return RedirectResponse("/login?error=server", status_code=303)
    finally:
        conn.close()

    return RedirectResponse("/login?registered=1", status_code=303)


@app.get("/api/me")
async def api_me(user: sqlite3.Row = Depends(require_login)):
    return {
        "username": user["username"],
        "sprite_url": f"/sprites/{user['sprite_file']}",
    }


def _list_sfx_filenames() -> list[str]:
    if not SFX_DIR.is_dir():
        return []
    out: list[str] = []
    for path in sorted(SFX_DIR.iterdir()):
        if path.is_file() and path.suffix.lower() in SFX_ALLOWED_SUFFIXES:
            out.append(path.name)
    return out


@app.get("/api/sfx/list")
async def api_sfx_list(user: sqlite3.Row = Depends(require_login)):
    return {"files": _list_sfx_filenames()}


@app.post("/api/config/event-sfx")
async def api_config_event_sfx_save(request: Request, user: sqlite3.Row = Depends(require_login)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="bad_json")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="bad_json")
    for k in EVENT_SFX_KEYS:
        block = body.get(k)
        if block is None:
            continue
        if not isinstance(block, dict):
            raise HTTPException(status_code=400, detail="bad_event")
        set_event_sfx_pair(k, str(block.get("file") or ""), block.get("vol"))
    await notify_sprite_settings_changed()
    return {"ok": True, "event_sfx": get_event_sfx_config()}


@app.post("/api/config/screen-music")
async def api_config_screen_music_save(
    request: Request,
    user: sqlite3.Row = Depends(require_login),
):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="bad_json")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="bad_json")
    for k in SCREEN_MUSIC_KEYS:
        block = body.get(k)
        if block is None:
            continue
        if not isinstance(block, dict):
            raise HTTPException(status_code=400, detail="bad_screen")
        set_screen_music_pair(k, str(block.get("file") or ""), block.get("vol"))
    return {"ok": True, "screen_music": get_screen_music_config()}


@app.get("/api/screen-music")
async def api_screen_music_read(user: sqlite3.Row = Depends(require_login)):
    return {"screen_music": get_screen_music_config()}


@app.get("/api/config/summary")
async def api_config_summary(user: sqlite3.Row = Depends(require_login)):
    conn = _connect_db()
    try:
        user_rows = conn.execute(
            """
            SELECT
                id, username, sprite_file, sprite_flipped, shout_greeting,
                sprite_width_px, attack_sprite_file, attack_sprite_flipped,
                COALESCE(magicoin, 0) AS magicoin,
                COALESCE(attack_sfx_file, '') AS attack_sfx_file,
                COALESCE(attack_sfx_volume_pct, 100) AS attack_sfx_volume_pct
            FROM users ORDER BY username COLLATE NOCASE
            """,
        ).fetchall()
        pet_rows = conn.execute(
            """
            SELECT
                id, name, sprite_file, sprite_flipped,
                attack_sprite_file, attack_sprite_flipped,
                size_px, speed_px, code_mode,
                COALESCE(attack_sfx_file, '') AS attack_sfx_file,
                COALESCE(attack_sfx_volume_pct, 100) AS attack_sfx_volume_pct
            FROM pets ORDER BY id
            """
        ).fetchall()
    finally:
        conn.close()
    pet_type_groups: dict[
        tuple[str, str, int, str, int, int, float, str, str, int],
        dict,
    ] = {}
    for p in pet_rows:
        name = (p["name"] or "Pet")[:24]
        sprite_file = normalize_attack_sprite_file(p["sprite_file"])
        attack_file = normalize_attack_sprite_file(p["attack_sprite_file"])
        sprite_flip = 1 if bool(p["sprite_flipped"]) else 0
        attack_flip = 1 if bool(p["attack_sprite_flipped"]) else 0
        size_px = normalize_pet_size_px(p["size_px"])
        speed_px = float(normalize_pet_speed_px(p["speed_px"]))
        code_mode = (p["code_mode"] or "default")
        sfx_file = (p["attack_sfx_file"] or "")
        sfx_vol = max(0, min(100, int(p["attack_sfx_volume_pct"] or 100)))
        key = (
            name,
            sprite_file,
            sprite_flip,
            attack_file,
            attack_flip,
            size_px,
            speed_px,
            code_mode,
            sfx_file,
            sfx_vol,
        )
        grp = pet_type_groups.get(key)
        if not grp:
            grp = {
                "id": int(p["id"]),
                "instance_ids": [int(p["id"])],
                "instance_count": 1,
                "name": name,
                "sprite_url": f"/sprites/{sprite_file}" if sprite_file else "",
                "sprite_flipped": bool(sprite_flip),
                "attack_sprite_url": f"/sprites/{attack_file}" if attack_file else "",
                "attack_sprite_flipped": bool(attack_flip),
                "size_px": size_px,
                "speed_px": speed_px,
                "code_mode": code_mode,
                "attack_sfx_file": sfx_file,
                "attack_sfx_volume_pct": sfx_vol,
            }
            pet_type_groups[key] = grp
        else:
            grp["instance_ids"].append(int(p["id"]))
            grp["instance_count"] = int(grp["instance_count"]) + 1
            grp["id"] = min(int(grp["id"]), int(p["id"]))
    pet_types = sorted(
        pet_type_groups.values(),
        key=lambda g: (str(g["name"]).lower(), int(g["id"])),
    )

    music_file = get_music_file()
    return {
        "speech_font_px": get_speech_font_px(),
        "sfx_volume": get_sfx_volume(),
        "music_volume": get_music_volume(),
        "music_url": f"/sfx/{music_file}" if music_file else "",
        "shadow_darkness_pct": get_shadow_darkness_pct(),
        "shadow_height_pct": get_shadow_height_pct(),
        "shadow_blur_px": get_shadow_blur_px(),
        "defeat_ball_url": get_defeat_ball_url(),
        "defeat_ball_flipped": bool(get_meta_int("defeat_ball_sprite_flipped", 0, 0, 1)),
        "defeat_ball_size_px": get_defeat_ball_size_px(),
        "quiz_max_number": get_quiz_max_number(),
        "quiz_mode": get_quiz_mode(),
        "buy_pet_cost": get_buy_pet_cost(),
        "pet_size_min": get_pet_size_min(),
        "pet_size_max": get_pet_size_max(),
        "event_sfx": get_event_sfx_config(),
        "screen_music": get_screen_music_config(),
        "music_files": _list_world_music_filenames(),
        "login_bg_world_id": get_login_bg_world_id(),
        "worlds": [
            {"id": w["id"], "name": w["name"], "background_url": w["background_url"]}
            for w in _list_worlds_with_thumbs()
        ],
        "users": [
            {
                "id": r["id"],
                "username": r["username"],
                "sprite_url": f"/sprites/{r['sprite_file']}",
                "sprite_flipped": bool(r["sprite_flipped"]),
                "shout_greeting": normalize_shout_greeting(r["shout_greeting"]),
                "sprite_width_px": normalize_sprite_width_px(r["sprite_width_px"]),
                "attack_sprite_url": (
                    f"/sprites/{r['attack_sprite_file']}"
                    if normalize_attack_sprite_file(r["attack_sprite_file"])
                    else ""
                ),
                "attack_sprite_flipped": bool(r["attack_sprite_flipped"]),
                "magicoin": int(r["magicoin"] or 0),
                "attack_sfx_file": (r["attack_sfx_file"] or ""),
                "attack_sfx_volume_pct": max(
                    0, min(100, int(r["attack_sfx_volume_pct"] or 100))
                ),
            }
            for r in user_rows
        ],
        "pets": pet_types,
    }


@app.post("/config/world")
async def config_world_replace(
    user: sqlite3.Row = Depends(require_login),
    file: UploadFile = File(...),
):
    body = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(body) > MAX_UPLOAD_BYTES:
        return RedirectResponse("/config?error=world_too_big", status_code=303)
    kind = sniff_image_kind(body[:32])
    if kind not in ("png", "jpg"):
        return RedirectResponse("/config?error=world_bad_image", status_code=303)

    archive_world_background_files()
    if kind == "png":
        WORLD_BG_JPG.unlink(missing_ok=True)
        WORLD_BG_PNG.write_bytes(body)
    else:
        WORLD_BG_PNG.unlink(missing_ok=True)
        WORLD_BG_JPG.write_bytes(body)

    await notify_world_background_changed()
    return RedirectResponse("/config?saved=world", status_code=303)


@app.post("/config/users/{target_id}/sprite-width")
async def config_user_sprite_width(
    target_id: int,
    user: sqlite3.Row = Depends(require_login),
    sprite_width_px: int = Form(...),
):
    width = normalize_sprite_width_px(sprite_width_px)
    conn = _connect_db()
    try:
        cur = conn.execute(
            "UPDATE users SET sprite_width_px = ? WHERE id = ?",
            (width, target_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return RedirectResponse("/config?error=no_user", status_code=303)
    finally:
        conn.close()
    for info in players.values():
        if info.get("user_id") == target_id:
            info["sprite_width_px"] = width
    await broadcast_all(
        {"type": "user_profile", "account_id": target_id, "sprite_width_px": width}
    )
    return RedirectResponse("/config?saved=size", status_code=303)


@app.post("/config/speech-font")
async def config_speech_font(
    user: sqlite3.Row = Depends(require_login),
    font_px: int = Form(...),
):
    try:
        set_speech_font_px(font_px)
    except (TypeError, ValueError):
        return RedirectResponse("/config?error=bad_speech_font", status_code=303)
    await notify_sprite_settings_changed()
    return RedirectResponse("/config?saved=speech_font", status_code=303)


@app.post("/config/sfx-volume")
async def config_sfx_volume(
    user: sqlite3.Row = Depends(require_login),
    volume: int = Form(...),
):
    set_sfx_volume(volume)
    await notify_sprite_settings_changed()
    return RedirectResponse("/config?saved=sfx_volume", status_code=303)


@app.post("/config/music-volume")
async def config_music_volume(
    user: sqlite3.Row = Depends(require_login),
    volume: int = Form(...),
):
    set_music_volume(volume)
    await notify_sprite_settings_changed()
    return RedirectResponse("/config?saved=music_volume", status_code=303)


@app.post("/config/music")
async def config_music_file(
    user: sqlite3.Row = Depends(require_login),
    file: UploadFile = File(...),
):
    body = await file.read(MAX_AUDIO_UPLOAD_BYTES + 1)
    if len(body) > MAX_AUDIO_UPLOAD_BYTES:
        return RedirectResponse("/config?error=music_too_big", status_code=303)
    name = (file.filename or "").lower()
    ext = Path(name).suffix.lower()
    if ext not in SFX_ALLOWED_SUFFIXES:
        return RedirectResponse("/config?error=bad_music", status_code=303)
    fname = f"music_{uuid.uuid4().hex}{ext}"
    try:
        (SFX_DIR / fname).write_bytes(body)
    except OSError:
        return RedirectResponse("/config?error=server", status_code=303)
    old = get_music_file()
    set_music_file(fname)
    if old:
        (SFX_DIR / old).unlink(missing_ok=True)
    await notify_sprite_settings_changed()
    return RedirectResponse("/config?saved=music", status_code=303)


@app.post("/config/shadow")
async def config_shadow_settings(
    user: sqlite3.Row = Depends(require_login),
    darkness_pct: int = Form(...),
    height_pct: int = Form(...),
    blur_px: int = Form(...),
):
    set_shadow_darkness_pct(darkness_pct)
    set_shadow_height_pct(height_pct)
    set_shadow_blur_px(blur_px)
    await notify_sprite_settings_changed()
    return RedirectResponse("/config?saved=shadow", status_code=303)


@app.post("/config/defeat-ball/size")
async def config_defeat_ball_size(
    user: sqlite3.Row = Depends(require_login),
    size_px: int = Form(...),
):
    set_defeat_ball_size_px(size_px)
    await notify_sprite_settings_changed()
    return RedirectResponse("/config?saved=defeat_ball", status_code=303)


@app.post("/config/quiz")
async def config_quiz_settings(
    user: sqlite3.Row = Depends(require_login),
    max_number: int = Form(...),
    quiz_mode: str = Form("algebra_add_sub"),
):
    set_quiz_max_number(max_number)
    set_quiz_mode(quiz_mode)
    await notify_sprite_settings_changed()
    return RedirectResponse("/config?saved=quiz", status_code=303)


@app.post("/config/login")
async def config_login_settings(
    user: sqlite3.Row = Depends(require_login),
    bg_world_id: int = Form(0),
):
    try:
        wid = int(bg_world_id)
    except (TypeError, ValueError):
        wid = 0
    set_login_bg_world_id(max(0, wid))
    return RedirectResponse("/config?saved=login", status_code=303)


@app.post("/config/shop/buy-pet-cost")
async def config_buy_pet_cost(
    user: sqlite3.Row = Depends(require_login),
    cost: int = Form(...),
):
    set_buy_pet_cost(cost)
    await notify_sprite_settings_changed()
    return RedirectResponse("/config?saved=economy", status_code=303)


@app.post("/config/shop/pet-size-limits")
async def config_pet_size_limits(
    user: sqlite3.Row = Depends(require_login),
    size_min: int = Form(...),
    size_max: int = Form(...),
):
    lo = max(8, min(1024, int(size_min)))
    hi = max(8, min(1024, int(size_max)))
    if lo > hi:
        lo, hi = hi, lo
    set_pet_size_min(lo)
    set_pet_size_max(hi)
    await notify_sprite_settings_changed()
    return RedirectResponse("/config?saved=economy", status_code=303)


@app.post("/config/defeat-ball/sprite")
async def config_defeat_ball_sprite_upload(
    user: sqlite3.Row = Depends(require_login),
    file: UploadFile = File(...),
):
    body = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(body) > MAX_UPLOAD_BYTES:
        return RedirectResponse("/config?error=file_too_big", status_code=303)
    kind = sniff_image_kind(body[:32])
    if kind is None:
        return RedirectResponse("/config?error=bad_image", status_code=303)
    ext = extension_for_kind(kind)
    fname = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / fname
    try:
        dest.write_bytes(body)
    except OSError:
        return RedirectResponse("/config?error=server", status_code=303)
    old = get_defeat_ball_file()
    if old:
        archive_user_sprite_file(old, "defeat_ball")
    set_meta_value("defeat_ball_sprite_file", fname)
    set_meta_value("defeat_ball_sprite_flipped", "0")
    await notify_sprite_settings_changed()
    return RedirectResponse("/config?saved=defeat_ball", status_code=303)


async def _config_defeat_ball_image_mutate(action: str) -> RedirectResponse:
    old = get_defeat_ball_file()
    if not old:
        return RedirectResponse("/config?error=no_defeat_ball", status_code=303)
    old_path = UPLOAD_DIR / old
    if not old_path.is_file():
        return RedirectResponse("/config?error=no_defeat_ball", status_code=303)
    if action == "flip":
        try:
            flip_sprite_file_on_disk(old_path)
        except (OSError, UnidentifiedImageError, ValueError):
            return RedirectResponse("/config?error=flip_failed", status_code=303)
        flipped = 0 if get_meta_int("defeat_ball_sprite_flipped", 0, 0, 1) else 1
        set_meta_value("defeat_ball_sprite_flipped", str(flipped))
    else:
        new_file = f"{uuid.uuid4().hex}.png"
        new_path = UPLOAD_DIR / new_file
        try:
            if action == "trim_bounds":
                trim_sprite_empty_space_to_png(old_path, dst_path=new_path)
            else:
                trim_sprite_white_margin_to_png(old_path, dst_path=new_path)
        except (OSError, UnidentifiedImageError, ValueError):
            return RedirectResponse("/config?error=trim_failed", status_code=303)
        archive_user_sprite_file(old, "defeat_ball")
        set_meta_value("defeat_ball_sprite_file", new_file)
        set_meta_value("defeat_ball_sprite_flipped", "0")
    await notify_sprite_settings_changed()
    return RedirectResponse("/config?saved=defeat_ball", status_code=303)


@app.post("/config/defeat-ball/flip")
async def config_defeat_ball_flip(user: sqlite3.Row = Depends(require_login)):
    return await _config_defeat_ball_image_mutate("flip")


@app.post("/config/defeat-ball/trim-white")
async def config_defeat_ball_trim_white(user: sqlite3.Row = Depends(require_login)):
    return await _config_defeat_ball_image_mutate("trim_white")


@app.post("/config/defeat-ball/trim-bounds")
async def config_defeat_ball_trim_bounds(user: sqlite3.Row = Depends(require_login)):
    return await _config_defeat_ball_image_mutate("trim_bounds")


@app.post("/config/users/{target_id}/greeting")
async def config_user_greeting(
    target_id: int,
    user: sqlite3.Row = Depends(require_login),
    greeting: str = Form(""),
):
    text = normalize_shout_greeting(greeting)
    conn = _connect_db()
    try:
        cur = conn.execute(
            "UPDATE users SET shout_greeting = ? WHERE id = ?",
            (text, target_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return RedirectResponse("/config?error=no_user", status_code=303)
    finally:
        conn.close()
    for info in players.values():
        if info.get("user_id") == target_id:
            info["shout_greeting"] = text
    await broadcast_all(
        {"type": "shout_prefs", "account_id": target_id, "shout_greeting": text}
    )
    return RedirectResponse("/config?saved=greeting", status_code=303)


@app.post("/config/users/{target_id}/magicoin")
async def config_grant_magicoin(
    target_id: int,
    user: sqlite3.Row = Depends(require_login),
    delta: int = Form(...),
):
    try:
        amount = int(delta)
    except (TypeError, ValueError):
        return RedirectResponse("/config?error=bad_amount", status_code=303)
    if amount == 0:
        return RedirectResponse("/config?saved=magicoin", status_code=303)
    conn = _connect_db()
    try:
        row = conn.execute(
            "SELECT COALESCE(magicoin, 0) AS magicoin FROM users WHERE id = ?",
            (target_id,),
        ).fetchone()
        if not row:
            return RedirectResponse("/config?error=no_user", status_code=303)
        new_balance = max(0, int(row["magicoin"] or 0) + amount)
        conn.execute(
            "UPDATE users SET magicoin = ? WHERE id = ?",
            (new_balance, target_id),
        )
        conn.commit()
    finally:
        conn.close()
    for info in players.values():
        if info.get("user_id") == target_id:
            info["magicoin"] = new_balance
    actual_delta = new_balance - int(row["magicoin"] or 0)
    await broadcast_user_magicoin(
        target_id, new_balance, delta=actual_delta, reason="admin_grant"
    )
    return RedirectResponse("/config?saved=magicoin", status_code=303)


@app.post("/config/users/{target_id}/sprite")
async def config_replace_user_sprite(
    target_id: int,
    user: sqlite3.Row = Depends(require_login),
    file: UploadFile = File(...),
):
    conn = _connect_db()
    try:
        target = conn.execute(
            "SELECT id, username, sprite_file, attack_sprite_file FROM users WHERE id = ?",
            (target_id,),
        ).fetchone()
    finally:
        conn.close()
    if target is None:
        return RedirectResponse("/config?error=no_user", status_code=303)

    body = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(body) > MAX_UPLOAD_BYTES:
        return RedirectResponse("/config?error=file_too_big", status_code=303)
    kind = sniff_image_kind(body[:32])
    if kind is None:
        return RedirectResponse("/config?error=bad_image", status_code=303)

    ext = extension_for_kind(kind)
    fname = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / fname

    try:
        dest.write_bytes(body)
    except OSError:
        return RedirectResponse("/config?error=server", status_code=303)

    archive_user_sprite_file(target["sprite_file"], target["username"])

    conn = _connect_db()
    try:
        conn.execute(
            "UPDATE users SET sprite_file = ?, sprite_flipped = 0 WHERE id = ?",
            (fname, target_id),
        )
        conn.commit()
    except Exception:
        dest.unlink(missing_ok=True)
        return RedirectResponse("/config?error=server", status_code=303)
    finally:
        conn.close()

    await notify_user_sprite_updated(target_id, fname)
    return RedirectResponse("/config?saved=sprite", status_code=303)


@app.post("/config/users/{target_id}/flip-sprite")
async def config_flip_user_sprite(
    target_id: int,
    user: sqlite3.Row = Depends(require_login),
):
    conn = _connect_db()
    try:
        target = conn.execute(
            "SELECT id, username, sprite_file, sprite_flipped FROM users WHERE id = ?",
            (target_id,),
        ).fetchone()
    finally:
        conn.close()
    if target is None:
        return RedirectResponse("/config?error=no_user", status_code=303)

    path = UPLOAD_DIR / target["sprite_file"]
    if not path.is_file():
        return RedirectResponse("/config?error=no_user", status_code=303)

    try:
        flip_sprite_file_on_disk(path)
    except (OSError, UnidentifiedImageError, ValueError):
        return RedirectResponse("/config?error=flip_failed", status_code=303)

    new_flipped = 0 if int(target["sprite_flipped"] or 0) else 1
    conn = _connect_db()
    try:
        conn.execute(
            "UPDATE users SET sprite_flipped = ? WHERE id = ?",
            (new_flipped, target_id),
        )
        conn.commit()
    finally:
        conn.close()

    await notify_user_sprite_updated(
        target_id, target["sprite_file"], bust_cache=True
    )
    return RedirectResponse("/config?saved=flip", status_code=303)


@app.post("/config/users/{target_id}/trim-white")
async def config_trim_user_sprite_white(
    target_id: int,
    user: sqlite3.Row = Depends(require_login),
):
    conn = _connect_db()
    try:
        target = conn.execute(
            "SELECT id, username, sprite_file, attack_sprite_file FROM users WHERE id = ?",
            (target_id,),
        ).fetchone()
    finally:
        conn.close()
    if target is None:
        return RedirectResponse("/config?error=no_user", status_code=303)

    old_name = target["sprite_file"]
    old_path = UPLOAD_DIR / old_name
    if not old_path.is_file():
        return RedirectResponse("/config?error=no_user", status_code=303)

    new_name = f"{uuid.uuid4().hex}.png"
    new_path = UPLOAD_DIR / new_name
    try:
        trim_sprite_white_margin_to_png(old_path, dst_path=new_path)
    except (OSError, UnidentifiedImageError, ValueError):
        return RedirectResponse("/config?error=trim_failed", status_code=303)

    archive_user_sprite_file(old_name, target["username"])
    conn = _connect_db()
    try:
        conn.execute(
            "UPDATE users SET sprite_file = ?, sprite_flipped = 0 WHERE id = ?",
            (new_name, target_id),
        )
        conn.commit()
    except Exception:
        new_path.unlink(missing_ok=True)
        return RedirectResponse("/config?error=server", status_code=303)
    finally:
        conn.close()

    await notify_user_sprite_updated(target_id, new_name, bust_cache=True)
    return RedirectResponse("/config?saved=trim", status_code=303)


@app.post("/config/users/{target_id}/trim-bounds")
async def config_trim_user_sprite_bounds(
    target_id: int,
    user: sqlite3.Row = Depends(require_login),
):
    conn = _connect_db()
    try:
        target = conn.execute(
            "SELECT id, username, sprite_file FROM users WHERE id = ?",
            (target_id,),
        ).fetchone()
    finally:
        conn.close()
    if target is None:
        return RedirectResponse("/config?error=no_user", status_code=303)
    old_name = target["sprite_file"]
    old_path = UPLOAD_DIR / old_name
    if not old_path.is_file():
        return RedirectResponse("/config?error=no_user", status_code=303)
    new_name = f"{uuid.uuid4().hex}.png"
    new_path = UPLOAD_DIR / new_name
    try:
        trim_sprite_empty_space_to_png(old_path, dst_path=new_path)
    except (OSError, UnidentifiedImageError, ValueError):
        return RedirectResponse("/config?error=trim_failed", status_code=303)
    archive_user_sprite_file(old_name, target["username"])
    conn = _connect_db()
    try:
        conn.execute(
            "UPDATE users SET sprite_file = ?, sprite_flipped = 0 WHERE id = ?",
            (new_name, target_id),
        )
        conn.commit()
    finally:
        conn.close()
    await notify_user_sprite_updated(target_id, new_name, bust_cache=True)
    return RedirectResponse("/config?saved=trim_bounds", status_code=303)


@app.post("/config/users/{target_id}/attack-sprite")
async def config_replace_user_attack_sprite(
    target_id: int,
    user: sqlite3.Row = Depends(require_login),
    file: UploadFile = File(...),
):
    conn = _connect_db()
    try:
        target = conn.execute(
            "SELECT id, username, attack_sprite_file FROM users WHERE id = ?",
            (target_id,),
        ).fetchone()
    finally:
        conn.close()
    if target is None:
        return RedirectResponse("/config?error=no_user", status_code=303)

    body = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(body) > MAX_UPLOAD_BYTES:
        return RedirectResponse("/config?error=file_too_big", status_code=303)
    kind = sniff_image_kind(body[:32])
    if kind is None:
        return RedirectResponse("/config?error=bad_image", status_code=303)

    ext = extension_for_kind(kind)
    fname = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / fname
    try:
        dest.write_bytes(body)
    except OSError:
        return RedirectResponse("/config?error=server", status_code=303)

    old_attack = normalize_attack_sprite_file(target["attack_sprite_file"])
    if old_attack:
        archive_user_sprite_file(old_attack, f"{target['username']}_attack")

    conn = _connect_db()
    try:
        conn.execute(
            """
            UPDATE users
            SET attack_sprite_file = ?, attack_sprite_flipped = 0
            WHERE id = ?
            """,
            (fname, target_id),
        )
        conn.commit()
    except Exception:
        dest.unlink(missing_ok=True)
        return RedirectResponse("/config?error=server", status_code=303)
    finally:
        conn.close()

    await notify_user_attack_sprite_updated(target_id, fname, bust_cache=True)
    return RedirectResponse("/config?saved=attack_sprite", status_code=303)


@app.post("/config/users/{target_id}/flip-attack-sprite")
async def config_flip_user_attack_sprite(
    target_id: int,
    user: sqlite3.Row = Depends(require_login),
):
    conn = _connect_db()
    try:
        target = conn.execute(
            """
            SELECT id, username, attack_sprite_file, attack_sprite_flipped
            FROM users WHERE id = ?
            """,
            (target_id,),
        ).fetchone()
    finally:
        conn.close()
    if target is None:
        return RedirectResponse("/config?error=no_user", status_code=303)

    attack_file = normalize_attack_sprite_file(target["attack_sprite_file"])
    if not attack_file:
        return RedirectResponse("/config?error=no_attack", status_code=303)
    path = UPLOAD_DIR / attack_file
    if not path.is_file():
        return RedirectResponse("/config?error=no_attack", status_code=303)

    try:
        flip_sprite_file_on_disk(path)
    except (OSError, UnidentifiedImageError, ValueError):
        return RedirectResponse("/config?error=flip_failed", status_code=303)

    new_flipped = 0 if int(target["attack_sprite_flipped"] or 0) else 1
    conn = _connect_db()
    try:
        conn.execute(
            "UPDATE users SET attack_sprite_flipped = ? WHERE id = ?",
            (new_flipped, target_id),
        )
        conn.commit()
    finally:
        conn.close()

    await notify_user_attack_sprite_updated(target_id, attack_file, bust_cache=True)
    return RedirectResponse("/config?saved=flip_attack", status_code=303)


@app.post("/config/users/{target_id}/trim-attack-white")
async def config_trim_user_attack_sprite_white(
    target_id: int,
    user: sqlite3.Row = Depends(require_login),
):
    conn = _connect_db()
    try:
        target = conn.execute(
            "SELECT id, username, attack_sprite_file FROM users WHERE id = ?",
            (target_id,),
        ).fetchone()
    finally:
        conn.close()
    if target is None:
        return RedirectResponse("/config?error=no_user", status_code=303)

    old_name = normalize_attack_sprite_file(target["attack_sprite_file"])
    if not old_name:
        return RedirectResponse("/config?error=no_attack", status_code=303)
    old_path = UPLOAD_DIR / old_name
    if not old_path.is_file():
        return RedirectResponse("/config?error=no_attack", status_code=303)

    new_name = f"{uuid.uuid4().hex}.png"
    new_path = UPLOAD_DIR / new_name
    try:
        trim_sprite_white_margin_to_png(old_path, dst_path=new_path)
    except (OSError, UnidentifiedImageError, ValueError):
        return RedirectResponse("/config?error=trim_failed", status_code=303)

    archive_user_sprite_file(old_name, f"{target['username']}_attack")
    conn = _connect_db()
    try:
        conn.execute(
            """
            UPDATE users
            SET attack_sprite_file = ?, attack_sprite_flipped = 0
            WHERE id = ?
            """,
            (new_name, target_id),
        )
        conn.commit()
    except Exception:
        new_path.unlink(missing_ok=True)
        return RedirectResponse("/config?error=server", status_code=303)
    finally:
        conn.close()

    await notify_user_attack_sprite_updated(target_id, new_name, bust_cache=True)
    return RedirectResponse("/config?saved=trim_attack", status_code=303)


@app.post("/config/users/{target_id}/trim-attack-bounds")
async def config_trim_user_attack_sprite_bounds(
    target_id: int,
    user: sqlite3.Row = Depends(require_login),
):
    conn = _connect_db()
    try:
        target = conn.execute(
            "SELECT id, username, attack_sprite_file FROM users WHERE id = ?",
            (target_id,),
        ).fetchone()
    finally:
        conn.close()
    if target is None:
        return RedirectResponse("/config?error=no_user", status_code=303)
    old_name = normalize_attack_sprite_file(target["attack_sprite_file"])
    if not old_name:
        return RedirectResponse("/config?error=no_attack", status_code=303)
    old_path = UPLOAD_DIR / old_name
    if not old_path.is_file():
        return RedirectResponse("/config?error=no_attack", status_code=303)
    new_name = f"{uuid.uuid4().hex}.png"
    new_path = UPLOAD_DIR / new_name
    try:
        trim_sprite_empty_space_to_png(old_path, dst_path=new_path)
    except (OSError, UnidentifiedImageError, ValueError):
        return RedirectResponse("/config?error=trim_failed", status_code=303)
    archive_user_sprite_file(old_name, f"{target['username']}_attack")
    conn = _connect_db()
    try:
        conn.execute(
            "UPDATE users SET attack_sprite_file = ?, attack_sprite_flipped = 0 WHERE id = ?",
            (new_name, target_id),
        )
        conn.commit()
    finally:
        conn.close()
    await notify_user_attack_sprite_updated(target_id, new_name, bust_cache=True)
    return RedirectResponse("/config?saved=trim_attack_bounds", status_code=303)


@app.post("/config/pets/add")
async def config_add_pet(user: sqlite3.Row = Depends(require_login)):
    conn = _connect_db()
    try:
        cur = conn.execute(
            """
            INSERT INTO pets (name, x, y, facing_h, instance_name, shiny)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "Pet",
                420.0 + random.uniform(-40, 40),
                320.0 + random.uniform(-40, 40),
                1,
                generate_pet_instance_name(),
                1 if random.random() < SHINY_CHANCE else 0,
            ),
        )
        conn.commit()
        pet_id = int(cur.lastrowid)
    finally:
        conn.close()
    load_pets_runtime()
    await broadcast_pets_state()
    return RedirectResponse(f"/config?saved=pet_add#{pet_id}", status_code=303)


@app.post("/config/pets/{pet_id}/name")
async def config_pet_name(
    pet_id: int,
    user: sqlite3.Row = Depends(require_login),
    name: str = Form("Pet"),
    apply_to_ids: str = Form(""),
):
    text = (name or "Pet").strip()[:24] or "Pet"
    conn = _connect_db()
    try:
        ids = _resolve_pet_apply_ids(int(pet_id), apply_to_ids)
        if not ids:
            return RedirectResponse("/config?error=no_pet", status_code=303)
        q = ",".join("?" for _ in ids)
        cur = conn.execute(
            f"UPDATE pets SET name = ? WHERE id IN ({q})",
            (text, *ids),
        )
        conn.commit()
        if cur.rowcount == 0:
            return RedirectResponse("/config?error=no_pet", status_code=303)
    finally:
        conn.close()
    load_pets_runtime()
    await broadcast_pets_state()
    return RedirectResponse("/config?saved=pet_name", status_code=303)


@app.post("/config/pets/{pet_id}/stats")
async def config_pet_stats(
    pet_id: int,
    user: sqlite3.Row = Depends(require_login),
    size_px: int = Form(...),
    speed_px: float = Form(...),
    apply_to_ids: str = Form(""),
):
    s = normalize_pet_size_px(size_px)
    v = normalize_pet_speed_px(speed_px)
    conn = _connect_db()
    try:
        ids = _resolve_pet_apply_ids(int(pet_id), apply_to_ids)
        if not ids:
            return RedirectResponse("/config?error=no_pet", status_code=303)
        q = ",".join("?" for _ in ids)
        cur = conn.execute(
            f"UPDATE pets SET size_px = ?, speed_px = ? WHERE id IN ({q})",
            (s, v, *ids),
        )
        conn.commit()
        if cur.rowcount == 0:
            return RedirectResponse("/config?error=no_pet", status_code=303)
    finally:
        conn.close()
    load_pets_runtime()
    await broadcast_pets_state()
    return RedirectResponse("/config?saved=pet_stats", status_code=303)


@app.post("/api/pets/{pet_id}/attack-sfx")
async def api_pet_attack_sfx(
    pet_id: int,
    user: sqlite3.Row = Depends(require_login),
    file: str = Form(""),
    volume_pct: int = Form(100),
    apply_to_ids: str = Form(""),
):
    raw = (file or "").strip()
    if raw and raw not in _list_sfx_filenames():
        return {"ok": False, "error": "no_such_file"}
    try:
        v = int(volume_pct)
    except (TypeError, ValueError):
        v = 100
    v = max(0, min(100, v))
    conn = _connect_db()
    try:
        ids = _resolve_pet_apply_ids(int(pet_id), apply_to_ids)
        if not ids:
            return {"ok": False, "error": "no_pet"}
        q = ",".join("?" for _ in ids)
        cur = conn.execute(
            f"UPDATE pets SET attack_sfx_file = ?, attack_sfx_volume_pct = ? WHERE id IN ({q})",
            (raw, v, *ids),
        )
        conn.commit()
        if cur.rowcount == 0:
            return {"ok": False, "error": "no_pet"}
    finally:
        conn.close()
    load_pets_runtime()
    await broadcast_pets_state()
    return {
        "ok": True,
        "attack_sfx_file": raw,
        "attack_sfx_volume_pct": v,
        "attack_sfx_url": f"/sfx/{raw}" if raw else "",
    }


@app.post("/api/users/{target_id}/attack-sfx")
async def api_user_attack_sfx(
    target_id: int,
    user: sqlite3.Row = Depends(require_login),
    file: str = Form(""),
    volume_pct: int = Form(100),
):
    raw = (file or "").strip()
    if raw and raw not in _list_sfx_filenames():
        return {"ok": False, "error": "no_such_file"}
    try:
        v = int(volume_pct)
    except (TypeError, ValueError):
        v = 100
    v = max(0, min(100, v))
    conn = _connect_db()
    try:
        cur = conn.execute(
            "UPDATE users SET attack_sfx_file = ?, attack_sfx_volume_pct = ? WHERE id = ?",
            (raw, v, int(target_id)),
        )
        conn.commit()
        if cur.rowcount == 0:
            return {"ok": False, "error": "no_user"}
    finally:
        conn.close()
    sfx_url = f"/sfx/{raw}" if raw else ""
    for pid, info in players.items():
        if info.get("user_id") == int(target_id):
            info["attack_sfx_url"] = sfx_url
            info["attack_sfx_volume_pct"] = v
    await broadcast_all(
        {
            "type": "user_attack_sfx",
            "account_id": int(target_id),
            "attack_sfx_url": sfx_url,
            "attack_sfx_volume_pct": v,
        }
    )
    return {
        "ok": True,
        "attack_sfx_file": raw,
        "attack_sfx_volume_pct": v,
        "attack_sfx_url": sfx_url,
    }


@app.post("/config/pets/{pet_id}/recenter")
async def config_pet_recenter(
    pet_id: int,
    user: sqlite3.Row = Depends(require_login),
    apply_to_ids: str = Form(""),
):
    conn = _connect_db()
    try:
        ids = _resolve_pet_apply_ids(int(pet_id), apply_to_ids)
        if not ids:
            return RedirectResponse("/config?error=no_pet", status_code=303)
        q = ",".join("?" for _ in ids)
        cur = conn.execute(
            f"UPDATE pets SET x = ?, y = ?, facing_h = ? WHERE id IN ({q})",
            (PET_CENTER_X, PET_CENTER_Y, 1, *ids),
        )
        conn.commit()
        if cur.rowcount == 0:
            return RedirectResponse("/config?error=no_pet", status_code=303)
    finally:
        conn.close()

    # Reset runtime targets so the pet snaps back immediately.
    load_pets_runtime()
    await broadcast_pets_state()
    return RedirectResponse("/config?saved=pet_recenter", status_code=303)


@app.post("/config/pets/{pet_id}/code-mode")
async def config_pet_code_mode(
    pet_id: int,
    user: sqlite3.Row = Depends(require_login),
    code_mode: str = Form("default"),
    apply_to_ids: str = Form(""),
):
    mode = "code" if (code_mode or "").strip().lower() == "code" else "default"
    conn = _connect_db()
    try:
        ids = _resolve_pet_apply_ids(int(pet_id), apply_to_ids)
        if not ids:
            return RedirectResponse("/config?error=no_pet", status_code=303)
        q = ",".join("?" for _ in ids)
        cur = conn.execute(f"UPDATE pets SET code_mode = ? WHERE id IN ({q})", (mode, *ids))
        conn.commit()
        if cur.rowcount == 0:
            return RedirectResponse("/config?error=no_pet", status_code=303)
    finally:
        conn.close()
    if mode == "code":
        for pid in ids:
            if not _get_pet_code(int(pid)):
                _set_pet_code(int(pid), DEFAULT_PET_CODE)
    load_pets_runtime()
    await broadcast_pets_state()
    return RedirectResponse("/config?saved=pet_code_mode", status_code=303)


@app.get("/config/pets/{pet_id}/code")
async def config_pet_code_page(pet_id: int, request: Request):
    if not user_id_from_session_token(_session_token(request)):
        return RedirectResponse("/login", status_code=303)
    # simple static page; code is loaded via API
    return FileResponse(STATIC_DIR / "pet_code.html")


@app.get("/api/pets/{pet_id}/code")
async def api_pet_code_get(
    pet_id: int,
    apply_to_ids: str = "",
    user: sqlite3.Row = Depends(require_login),
):
    ids = _resolve_pet_apply_ids(int(pet_id), apply_to_ids)
    if not ids:
        raise HTTPException(status_code=404)
    code = _get_pet_code(int(ids[0])) or DEFAULT_PET_CODE
    return {"pet_id": int(ids[0]), "code": code, "apply_to_ids": ids}


@app.post("/api/pets/{pet_id}/code")
async def api_pet_code_set(
    pet_id: int,
    request: Request,
    user: sqlite3.Row = Depends(require_login),
):
    payload = await request.json()
    code = str((payload or {}).get("code", ""))[:20000]
    ids = _resolve_pet_apply_ids(int(pet_id), str((payload or {}).get("apply_to_ids", "")))
    if not ids:
        raise HTTPException(status_code=404)
    final_code = code or DEFAULT_PET_CODE
    for pid in ids:
        _set_pet_code(int(pid), final_code)
    load_pets_runtime()
    await broadcast_pets_state()
    return {"ok": True}


@app.post("/config/pets/{pet_id}/delete")
async def config_pet_delete(
    pet_id: int,
    user: sqlite3.Row = Depends(require_login),
    apply_to_ids: str = Form(""),
):
    ids = _resolve_pet_apply_ids(int(pet_id), apply_to_ids)
    if not ids:
        return RedirectResponse("/config?error=no_pet", status_code=303)
    conn = _connect_db()
    try:
        q = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT sprite_file, attack_sprite_file, name FROM pets WHERE id IN ({q})",
            tuple(ids),
        ).fetchall()
        for row in rows:
            if normalize_attack_sprite_file(row["sprite_file"]):
                archive_user_sprite_file(row["sprite_file"], f"pet_{row['name']}")
            if normalize_attack_sprite_file(row["attack_sprite_file"]):
                archive_user_sprite_file(row["attack_sprite_file"], f"pet_{row['name']}_attack")
        conn.execute(f"DELETE FROM pets WHERE id IN ({q})", tuple(ids))
        conn.commit()
    finally:
        conn.close()
    load_pets_runtime()
    await broadcast_pets_state()
    return RedirectResponse("/config?saved=pet_delete", status_code=303)


def _pet_image_columns(role: str) -> tuple[str, str, str]:
    if role == "attack":
        return "attack_sprite_file", "attack_sprite_flipped", "_attack"
    return "sprite_file", "sprite_flipped", ""


def _parse_pet_id_csv(raw: str | None) -> list[int]:
    out: list[int] = []
    if not raw:
        return out
    seen: set[int] = set()
    for part in str(raw).split(","):
        txt = part.strip()
        if not txt:
            continue
        try:
            pid = int(txt)
        except (TypeError, ValueError):
            continue
        if pid <= 0 or pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
    return out


def _resolve_pet_apply_ids(base_pet_id: int, apply_to_ids_raw: str | None) -> list[int]:
    candidate_ids = _parse_pet_id_csv(apply_to_ids_raw)
    if int(base_pet_id) not in candidate_ids:
        candidate_ids.append(int(base_pet_id))
    conn = _connect_db()
    try:
        rows = conn.execute("SELECT id FROM pets").fetchall()
    finally:
        conn.close()
    existing = {int(r["id"]) for r in rows}
    ids = [pid for pid in candidate_ids if pid in existing]
    if int(base_pet_id) in existing and int(base_pet_id) not in ids:
        ids.append(int(base_pet_id))
    if not ids:
        return []
    # Keep ordering stable but dedup.
    final_ids: list[int] = []
    seen: set[int] = set()
    for pid in ids:
        if pid in seen:
            continue
        seen.add(pid)
        final_ids.append(pid)
    return final_ids


async def _config_pet_image_upload(
    pet_id: int, file: UploadFile, *, role: str, apply_to_ids: str = ""
) -> RedirectResponse:
    body = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(body) > MAX_UPLOAD_BYTES:
        return RedirectResponse("/config?error=file_too_big", status_code=303)
    kind = sniff_image_kind(body[:32])
    if kind is None:
        return RedirectResponse("/config?error=bad_image", status_code=303)
    ext = extension_for_kind(kind)
    fname = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / fname
    try:
        dest.write_bytes(body)
    except OSError:
        return RedirectResponse("/config?error=server", status_code=303)
    col, flip_col, archive_suffix = _pet_image_columns(role)
    ids = _resolve_pet_apply_ids(int(pet_id), apply_to_ids)
    if not ids:
        dest.unlink(missing_ok=True)
        return RedirectResponse("/config?error=no_pet", status_code=303)
    conn = _connect_db()
    try:
        q = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT {col}, name FROM pets WHERE id IN ({q})",
            tuple(ids),
        ).fetchall()
        for row in rows:
            old = normalize_attack_sprite_file(row[col])
            if old:
                archive_user_sprite_file(old, f"pet_{row['name']}{archive_suffix}")
        conn.execute(
            f"UPDATE pets SET {col} = ?, {flip_col} = 0 WHERE id IN ({q})",
            (fname, *ids),
        )
        conn.commit()
    finally:
        conn.close()
    load_pets_runtime()
    await broadcast_pets_state()
    return RedirectResponse("/config?saved=pet_sprite", status_code=303)


@app.post("/config/pets/{pet_id}/sprite")
async def config_pet_sprite_upload(
    pet_id: int,
    user: sqlite3.Row = Depends(require_login),
    file: UploadFile = File(...),
    apply_to_ids: str = Form(""),
):
    return await _config_pet_image_upload(pet_id, file, role="sprite", apply_to_ids=apply_to_ids)


@app.post("/config/pets/{pet_id}/attack-sprite")
async def config_pet_attack_sprite_upload(
    pet_id: int,
    user: sqlite3.Row = Depends(require_login),
    file: UploadFile = File(...),
    apply_to_ids: str = Form(""),
):
    return await _config_pet_image_upload(pet_id, file, role="attack", apply_to_ids=apply_to_ids)


async def _config_pet_image_mutate(
    pet_id: int, *, role: str, action: str, apply_to_ids: str = ""
) -> RedirectResponse:
    col, flip_col, archive_suffix = _pet_image_columns(role)
    ids = _resolve_pet_apply_ids(int(pet_id), apply_to_ids)
    if not ids:
        return RedirectResponse("/config?error=no_pet", status_code=303)
    conn = _connect_db()
    try:
        row = conn.execute(
            f"SELECT {col}, {flip_col}, name FROM pets WHERE id = ?",
            (int(ids[0]),),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return RedirectResponse("/config?error=no_pet", status_code=303)
    old = normalize_attack_sprite_file(row[col])
    if not old:
        return RedirectResponse("/config?error=no_pet_sprite", status_code=303)
    old_path = UPLOAD_DIR / old
    if not old_path.is_file():
        return RedirectResponse("/config?error=no_pet_sprite", status_code=303)

    if action == "flip":
        try:
            flip_sprite_file_on_disk(old_path)
        except (OSError, UnidentifiedImageError, ValueError):
            return RedirectResponse("/config?error=flip_failed", status_code=303)
        new_file = old
        new_flip = 0 if int(row[flip_col] or 0) else 1
    else:
        new_file = f"{uuid.uuid4().hex}.png"
        new_path = UPLOAD_DIR / new_file
        try:
            if action == "trim_bounds":
                trim_sprite_empty_space_to_png(old_path, dst_path=new_path)
            else:
                trim_sprite_white_margin_to_png(old_path, dst_path=new_path)
        except (OSError, UnidentifiedImageError, ValueError):
            return RedirectResponse("/config?error=trim_failed", status_code=303)
        archive_user_sprite_file(old, f"pet_{row['name']}{archive_suffix}")
        new_flip = 0

    conn = _connect_db()
    try:
        q = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE pets SET {col} = ?, {flip_col} = ? WHERE id IN ({q})",
            (new_file, new_flip, *ids),
        )
        conn.commit()
    finally:
        conn.close()
    load_pets_runtime()
    await broadcast_pets_state()
    return RedirectResponse("/config?saved=pet_sprite", status_code=303)


@app.post("/config/pets/{pet_id}/flip-sprite")
async def config_pet_flip_sprite(
    pet_id: int,
    user: sqlite3.Row = Depends(require_login),
    apply_to_ids: str = Form(""),
):
    return await _config_pet_image_mutate(pet_id, role="sprite", action="flip", apply_to_ids=apply_to_ids)


@app.post("/config/pets/{pet_id}/trim-white")
async def config_pet_trim_white(
    pet_id: int,
    user: sqlite3.Row = Depends(require_login),
    apply_to_ids: str = Form(""),
):
    return await _config_pet_image_mutate(pet_id, role="sprite", action="trim_white", apply_to_ids=apply_to_ids)


@app.post("/config/pets/{pet_id}/trim-bounds")
async def config_pet_trim_bounds(
    pet_id: int,
    user: sqlite3.Row = Depends(require_login),
    apply_to_ids: str = Form(""),
):
    return await _config_pet_image_mutate(pet_id, role="sprite", action="trim_bounds", apply_to_ids=apply_to_ids)


@app.post("/config/pets/{pet_id}/flip-attack-sprite")
async def config_pet_flip_attack_sprite(
    pet_id: int,
    user: sqlite3.Row = Depends(require_login),
    apply_to_ids: str = Form(""),
):
    return await _config_pet_image_mutate(pet_id, role="attack", action="flip", apply_to_ids=apply_to_ids)


@app.post("/config/pets/{pet_id}/trim-attack-white")
async def config_pet_trim_attack_white(
    pet_id: int,
    user: sqlite3.Row = Depends(require_login),
    apply_to_ids: str = Form(""),
):
    return await _config_pet_image_mutate(pet_id, role="attack", action="trim_white", apply_to_ids=apply_to_ids)


@app.post("/config/pets/{pet_id}/trim-attack-bounds")
async def config_pet_trim_attack_bounds(
    pet_id: int,
    user: sqlite3.Row = Depends(require_login),
    apply_to_ids: str = Form(""),
):
    return await _config_pet_image_mutate(pet_id, role="attack", action="trim_bounds", apply_to_ids=apply_to_ids)


@app.post("/config/users/{target_id}/delete")
async def config_delete_user(
    request: Request,
    target_id: int,
    user: sqlite3.Row = Depends(require_login),
):
    conn = _connect_db()
    try:
        target = conn.execute(
            "SELECT id, username, sprite_file FROM users WHERE id = ?",
            (target_id,),
        ).fetchone()
    finally:
        conn.close()
    if target is None:
        return RedirectResponse("/config?error=no_user", status_code=303)

    await close_connections_for_user(target_id)
    archive_user_sprite_file(target["sprite_file"], target["username"])
    if normalize_attack_sprite_file(target["attack_sprite_file"]):
        archive_user_sprite_file(
            target["attack_sprite_file"], f"{target['username']}_attack"
        )

    conn = _connect_db()
    try:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (target_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (target_id,))
        conn.commit()
    finally:
        conn.close()

    if target_id == user["id"]:
        return _redirect_clear_session(request, "/login")
    return RedirectResponse("/config?deleted=1", status_code=303)


@app.post("/config/users/{target_id}/reset-captures")
async def config_reset_user_captures(
    target_id: int,
    user: sqlite3.Row = Depends(require_login),
):
    conn = _connect_db()
    try:
        target = conn.execute(
            "SELECT id FROM users WHERE id = ?",
            (int(target_id),),
        ).fetchone()
        if not target:
            return RedirectResponse("/config?error=no_user", status_code=303)
        conn.execute("DELETE FROM captures WHERE user_id = ?", (int(target_id),))
        conn.commit()
    finally:
        conn.close()

    # Keep live in-memory session state in sync immediately.
    for pid, info in list(players.items()):
        if int(info.get("user_id") or 0) != int(target_id):
            continue
        info["capture_count"] = 0
        await broadcast_all_world(
            _player_world_id(info),
            {
                "type": "score",
                "id": pid,
                "defeat_count": int(info.get("defeat_count", 0)),
                "capture_count": 0,
            },
        )
    return RedirectResponse("/config?saved=reset_captures", status_code=303)


@app.get("/sprites/{filename}")
async def serve_sprite(filename: str):
    if not SPRITE_NAME_RE.match(filename):
        raise HTTPException(status_code=404)
    path = UPLOAD_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path)


@app.get("/world-background/{world_id}")
async def world_background_for(world_id: int):
    conn = _connect_db()
    try:
        row = conn.execute(
            "SELECT background_file FROM worlds WHERE id = ?", (int(world_id),)
        ).fetchone()
    finally:
        conn.close()
    fname = row["background_file"] if row and row["background_file"] else ""
    if fname:
        path = STATIC_DIR / "bg" / fname
        if path.is_file():
            return FileResponse(path)
    raise HTTPException(status_code=404, detail="No background for world")


@app.get("/world-background")
async def world_background():
    if WORLD_BG_PNG.is_file():
        return FileResponse(WORLD_BG_PNG)
    if WORLD_BG_JPG.is_file():
        return FileResponse(WORLD_BG_JPG)
    raise HTTPException(status_code=404, detail="Put static/bg/world.png or world.jpg")


@app.websocket("/ws")
async def game_socket(websocket: WebSocket):
    await websocket.accept()
    tok = websocket.cookies.get(SESSION_COOKIE)
    uid = user_id_from_session_token(tok)
    if not uid:
        await websocket.close(code=1008)
        return

    conn = _connect_db()
    try:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    finally:
        conn.close()

    if user is None:
        await websocket.close(code=1008)
        return

    player_id = uuid.uuid4().hex[:10]
    username = user["username"]
    greet = normalize_shout_greeting(user["shout_greeting"])
    sprite_width_px = normalize_sprite_width_px(user["sprite_width_px"])
    pos_x = float(user["pos_x"])
    pos_y = float(user["pos_y"])
    needs_recenter = pos_x < 0.0 or pos_y < 0.0
    if needs_recenter:
        pos_x = 1360.0 / 2.0
        pos_y = 760.0 / 2.0
        _set_user_position(int(uid), pos_x, pos_y)
    facing_h = normalize_facing_h(user["facing_h"])
    attack_file = normalize_attack_sprite_file(user["attack_sprite_file"])
    attack_sprite_url = f"/sprites/{attack_file}" if attack_file else ""
    sfx_raw, sfx_vol_u, sfx_url_u = _user_attack_sfx_triple(user)

    world_id = int((user["current_world_id"] if "current_world_id" in user.keys() else 1) or 1)
    world_screen = normalize_world_screen(
        user["current_world_screen"] if "current_world_screen" in user.keys() else 1
    )
    if not _world_exists(world_id):
        world_id = _ensure_default_world_id()
        _set_user_current_world(uid, world_id)
    _ensure_world_screen_pets(world_id, 1)
    _ensure_world_screen_obstacles(world_id, 0)
    _ensure_world_screen_obstacles(world_id, 2)
    _ensure_world_screen_pets(world_id, 0)
    _ensure_world_screen_pets(world_id, 2)
    _ensure_world_screen_pets(world_id, world_screen)
    _ensure_world_screen_obstacles(world_id, world_screen)
    user_max_hp = _get_user_max_health(int(uid)) if uid else PLAYER_MAX_HEALTH
    persisted_health = (
        int(user["health"]) if "health" in user.keys() and user["health"] is not None else user_max_hp
    )
    persisted_health = max(1, min(user_max_hp, persisted_health))
    magicoin_balance = (
        int(user["magicoin"]) if "magicoin" in user.keys() and user["magicoin"] is not None else MAGICOIN_DEFAULT
    )
    players[player_id] = {
        "ws": websocket,
        "x": pos_x,
        "y": pos_y,
        "name": username,
        "sprite": f"/sprites/{user['sprite_file']}",
        "attack_sprite": attack_sprite_url,
        "user_id": uid,
        "shout_greeting": greet,
        "sprite_width_px": sprite_width_px,
        "facing_h": facing_h,
        "defeat_count": 0,
        "capture_count": _get_capture_count(int(uid)) if uid else 0,
        "viewport_w": 1360.0,
        "viewport_h": 760.0,
        "health": persisted_health,
        "max_health": user_max_hp,
        "dead": False,
        "respawn_at": 0.0,
        "spawn_seq": 0,
        "world_id": world_id,
        "world_screen": world_screen,
        "magicoin": magicoin_balance,
        "attack_sfx_url": sfx_url_u,
        "attack_sfx_volume_pct": sfx_vol_u,
        "transformed_shiny": bool(int(user["transformed_shiny"] or 0))
        if "transformed_shiny" in user.keys()
        else False,
    }

    try:
        await websocket.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "id": player_id,
                    "username": username,
                    "sprite": players[player_id]["sprite"],
                    "account_id": uid,
                    "speech_font_px": get_speech_font_px(),
                    "sfx_volume": get_sfx_volume(),
                    "music_volume": get_music_volume(),
                    "music_url": (
                        _world_music_url(world_id)
                        or (f"/sfx/{get_music_file()}" if get_music_file() else "")
                    ),
                    "defeat_ball_url": get_defeat_ball_url(),
                    "defeat_ball_size_px": get_defeat_ball_size_px(),
                    "quiz_max_number": get_quiz_max_number(),
                    "quiz_mode": get_quiz_mode(),
                    "world_music_volume_pct": _world_music_volume_pct(world_id),
                    "event_sfx": get_event_sfx_config(),
                    "shout_greeting": greet,
                    "sprite_width_px": sprite_width_px,
                    "x": pos_x,
                    "y": pos_y,
                    "facing_h": facing_h,
                    "attack_sprite": attack_sprite_url,
                    "defeat_count": players[player_id]["defeat_count"],
                    "capture_count": players[player_id]["capture_count"],
                    "transformed_shiny": bool(players[player_id].get("transformed_shiny", False)),
                    "health": players[player_id]["health"],
                    "max_health": players[player_id]["max_health"],
                    "dead": players[player_id]["dead"],
                    "spawn_seq": players[player_id]["spawn_seq"],
                    "players": others_snapshot(player_id, world_id, world_screen),
                    "npcs": NPCS,
                    "pets": pets_snapshot(world_id, world_screen),
                    "coin_piles": _coin_piles_snapshot(world_id, world_screen),
                    "obstacles": obstacles_snapshot(world_id, world_screen),
                    "world_id": world_id,
                    "world_screen": world_screen,
                    "world_background_url": _world_background_url(world_id),
                    "world_mirrored": bool(world_screen != 1),
                    "magicoin": magicoin_balance,
                    "coin_icon_url": "/static-sprites/magicoin/coin.png",
                    "center_on_load": needs_recenter,
                    "attack_sfx_url": sfx_url_u,
                    "attack_sfx_volume_pct": sfx_vol_u,
                }
            )
        )
        await broadcast_world(
            world_id,
            {
                "type": "join",
                "id": player_id,
                "x": players[player_id]["x"],
                "y": players[player_id]["y"],
                "name": username,
                "sprite": players[player_id]["sprite"],
                "account_id": uid,
                "shout_greeting": greet,
                "sprite_width_px": sprite_width_px,
                "facing_h": facing_h,
                "attack_sprite": attack_sprite_url,
                "defeat_count": players[player_id]["defeat_count"],
                "capture_count": players[player_id]["capture_count"],
                "transformed_shiny": bool(players[player_id].get("transformed_shiny", False)),
                "health": players[player_id]["health"],
                "max_health": players[player_id]["max_health"],
                "dead": players[player_id]["dead"],
                "spawn_seq": players[player_id]["spawn_seq"],
                "world_screen": players[player_id]["world_screen"],
            },
            exclude_id=player_id,
            world_screen=players[player_id]["world_screen"],
        )

        pending_quiz_reward = 0
        try:
            while True:
                raw = await websocket.receive_text()
                data = json.loads(raw)
                msg_type = data.get("type")

                if msg_type == "hello":
                    await broadcast_world(
                        _player_world_id(players[player_id]),
                        {
                            "type": "move",
                            "id": player_id,
                            "x": players[player_id]["x"],
                            "y": players[player_id]["y"],
                            "name": username,
                            "sprite": players[player_id]["sprite"],
                            "account_id": uid,
                            "shout_greeting": players[player_id]["shout_greeting"],
                            "sprite_width_px": players[player_id]["sprite_width_px"],
                            "facing_h": players[player_id]["facing_h"],
                            "attack_sprite": players[player_id]["attack_sprite"],
                            "defeat_count": players[player_id]["defeat_count"],
                            "capture_count": players[player_id]["capture_count"],
                            "transformed_shiny": bool(players[player_id].get("transformed_shiny", False)),
                            "health": players[player_id]["health"],
                            "max_health": players[player_id]["max_health"],
                            "dead": players[player_id]["dead"],
                            "spawn_seq": players[player_id]["spawn_seq"],
                            "world_screen": players[player_id]["world_screen"],
                        },
                        exclude_id=player_id,
                        world_screen=players[player_id]["world_screen"],
                    )
                elif msg_type == "switch_world":
                    # In-place world switching: update server-side world membership,
                    # broadcast leave/join to other players, and re-send a hello payload
                    # (so pets/background/music/peers re-sync without a full page reload).
                    requested_world_id = int(data.get("world_id") or 1)
                    if not _world_exists(requested_world_id):
                        continue

                    conn = _connect_db()
                    try:
                        user_row = conn.execute(
                            """
                            SELECT current_world_id, current_world_screen,
                                   pos_x, pos_y, facing_h, health, magicoin,
                                   COALESCE(sprite_width_px, 48) AS sprite_width_px
                            FROM users
                            WHERE id = ?
                            """,
                            (uid,),
                        ).fetchone()
                    finally:
                        conn.close()

                    if not user_row:
                        continue

                    db_world_id = int(user_row["current_world_id"] or 1)
                    db_world_screen = normalize_world_screen(user_row["current_world_screen"])
                    if db_world_id != requested_world_id:
                        # Prevent out-of-band switching without a corresponding /enter call.
                        continue

                    old_world_id = int(players[player_id].get("world_id", 1) or 1)
                    pos_x = float(user_row["pos_x"])
                    pos_y = float(user_row["pos_y"])
                    needs_recenter = pos_x < 0.0 or pos_y < 0.0
                    if needs_recenter:
                        pos_x = 1360.0 / 2.0
                        pos_y = 760.0 / 2.0
                        _set_user_position(int(uid), pos_x, pos_y)

                    facing_h_new = normalize_facing_h(user_row["facing_h"])
                    user_max_hp = _get_user_max_health(int(uid)) if uid else PLAYER_MAX_HEALTH
                    persisted_health = int(user_row["health"] or user_max_hp) if user_row["health"] is not None else user_max_hp
                    persisted_health = max(1, min(user_max_hp, persisted_health))
                    magicoin_balance = int(user_row["magicoin"] or 0)

                    players[player_id]["world_id"] = db_world_id
                    players[player_id]["world_screen"] = db_world_screen
                    players[player_id]["x"] = pos_x
                    players[player_id]["y"] = pos_y
                    players[player_id]["facing_h"] = facing_h_new
                    players[player_id]["sprite_width_px"] = normalize_sprite_width_px(
                        user_row["sprite_width_px"]
                    )
                    players[player_id]["health"] = persisted_health
                    players[player_id]["max_health"] = user_max_hp
                    players[player_id]["dead"] = False
                    players[player_id]["spawn_seq"] = 0

                    if old_world_id != db_world_id:
                        await broadcast_world(
                            old_world_id,
                            {"type": "leave", "id": player_id, "world_screen": players[player_id]["world_screen"]},
                            world_screen=players[player_id]["world_screen"],
                        )
                        await broadcast_world(
                            db_world_id,
                            {
                                "type": "join",
                                "id": player_id,
                                "x": players[player_id]["x"],
                                "y": players[player_id]["y"],
                                "name": username,
                                "sprite": players[player_id]["sprite"],
                                "account_id": uid,
                                "shout_greeting": greet,
                                "sprite_width_px": players[player_id]["sprite_width_px"],
                                "facing_h": facing_h_new,
                                "attack_sprite": attack_sprite_url,
                                "defeat_count": players[player_id]["defeat_count"],
                                "capture_count": players[player_id]["capture_count"],
                                "transformed_shiny": bool(players[player_id].get("transformed_shiny", False)),
                                "health": players[player_id]["health"],
                                "max_health": players[player_id]["max_health"],
                                "dead": players[player_id]["dead"],
                                "spawn_seq": players[player_id]["spawn_seq"],
                                "world_screen": players[player_id]["world_screen"],
                            },
                            exclude_id=player_id,
                            world_screen=players[player_id]["world_screen"],
                        )

                    music_url = _world_music_url(db_world_id)
                    if not music_url:
                        fallback_music_file = get_music_file()
                        music_url = f"/sfx/{fallback_music_file}" if fallback_music_file else ""

                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "hello",
                                "id": player_id,
                                "username": username,
                                "sprite": players[player_id]["sprite"],
                                "account_id": uid,
                                "speech_font_px": get_speech_font_px(),
                                "sfx_volume": get_sfx_volume(),
                                "music_volume": get_music_volume(),
                                "music_url": music_url,
                                "defeat_ball_url": get_defeat_ball_url(),
                                "defeat_ball_size_px": get_defeat_ball_size_px(),
                                "quiz_max_number": get_quiz_max_number(),
                                "quiz_mode": get_quiz_mode(),
                                "world_music_volume_pct": _world_music_volume_pct(db_world_id),
                                "event_sfx": get_event_sfx_config(),
                                "shout_greeting": greet,
                                "sprite_width_px": players[player_id]["sprite_width_px"],
                                "x": pos_x,
                                "y": pos_y,
                                "facing_h": facing_h_new,
                                "attack_sprite": attack_sprite_url,
                                "defeat_count": players[player_id]["defeat_count"],
                                "capture_count": players[player_id]["capture_count"],
                                "transformed_shiny": bool(players[player_id].get("transformed_shiny", False)),
                                "health": players[player_id]["health"],
                                "max_health": players[player_id]["max_health"],
                                "dead": players[player_id]["dead"],
                                "spawn_seq": players[player_id]["spawn_seq"],
                                "players": others_snapshot(player_id, db_world_id, db_world_screen),
                                "npcs": NPCS,
                                "pets": pets_snapshot(db_world_id, db_world_screen),
                                "coin_piles": _coin_piles_snapshot(db_world_id, db_world_screen),
                                "obstacles": obstacles_snapshot(db_world_id, db_world_screen),
                                "world_id": db_world_id,
                                "world_screen": db_world_screen,
                                "world_background_url": _world_background_url(db_world_id),
                                "world_mirrored": bool(db_world_screen != 1),
                                "magicoin": magicoin_balance,
                                "coin_icon_url": "/static-sprites/magicoin/coin.png",
                                "center_on_load": needs_recenter,
                                "attack_sfx_url": sfx_url_u,
                                "attack_sfx_volume_pct": sfx_vol_u,
                            }
                        )
                    )
                elif msg_type == "switch_world_screen":
                    delta = int(data.get("delta", 0))
                    if delta == 0:
                        continue
                    cur_world = _player_world_id(players[player_id])
                    cur_screen = _player_world_screen(players[player_id])
                    target_screen = normalize_world_screen(cur_screen + (1 if delta > 0 else -1))
                    if target_screen == cur_screen:
                        continue
                    _ensure_world_screen_obstacles(cur_world, target_screen)
                    _ensure_world_screen_pets(cur_world, target_screen)
                    players[player_id]["world_screen"] = target_screen
                    _set_user_current_world_screen(int(uid), target_screen)
                    vw = max(320.0, float(players[player_id].get("viewport_w", 1360.0) or 1360.0))
                    vh = max(240.0, float(players[player_id].get("viewport_h", 760.0) or 760.0))
                    half_w = max(12.0, normalize_sprite_width_px(players[player_id].get("sprite_width_px")) * 0.5)
                    edge_pad = max(6.0, min(36.0, half_w * 0.35))
                    if delta > 0:
                        players[player_id]["x"] = half_w + edge_pad
                    else:
                        players[player_id]["x"] = vw - half_w - edge_pad
                    players[player_id]["y"] = max(
                        half_w,
                        min(vh - half_w, float(players[player_id].get("y", vh / 2.0))),
                    )
                    _set_user_position(int(uid), players[player_id]["x"], players[player_id]["y"])
                    await broadcast_world(
                        cur_world,
                        {"type": "leave", "id": player_id, "world_screen": cur_screen},
                        world_screen=cur_screen,
                    )
                    await broadcast_world(
                        cur_world,
                        {
                            "type": "join",
                            "id": player_id,
                            "x": players[player_id]["x"],
                            "y": players[player_id]["y"],
                            "name": username,
                            "sprite": players[player_id]["sprite"],
                            "account_id": uid,
                            "shout_greeting": greet,
                            "sprite_width_px": players[player_id]["sprite_width_px"],
                            "facing_h": players[player_id]["facing_h"],
                            "attack_sprite": players[player_id]["attack_sprite"],
                            "defeat_count": players[player_id]["defeat_count"],
                            "capture_count": players[player_id]["capture_count"],
                            "transformed_shiny": bool(players[player_id].get("transformed_shiny", False)),
                            "health": players[player_id]["health"],
                            "max_health": players[player_id]["max_health"],
                            "dead": players[player_id]["dead"],
                            "spawn_seq": players[player_id]["spawn_seq"],
                            "world_screen": target_screen,
                        },
                        exclude_id=player_id,
                        world_screen=target_screen,
                    )
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "hello",
                                "id": player_id,
                                "username": username,
                                "sprite": players[player_id]["sprite"],
                                "account_id": uid,
                                "speech_font_px": get_speech_font_px(),
                                "sfx_volume": get_sfx_volume(),
                                "music_volume": get_music_volume(),
                                "music_url": (
                                    _world_music_url(cur_world)
                                    or (f"/sfx/{get_music_file()}" if get_music_file() else "")
                                ),
                                "defeat_ball_url": get_defeat_ball_url(),
                                "defeat_ball_size_px": get_defeat_ball_size_px(),
                                "quiz_max_number": get_quiz_max_number(),
                                "quiz_mode": get_quiz_mode(),
                                "world_music_volume_pct": _world_music_volume_pct(cur_world),
                                "event_sfx": get_event_sfx_config(),
                                "shout_greeting": greet,
                                "sprite_width_px": players[player_id]["sprite_width_px"],
                                "x": players[player_id]["x"],
                                "y": players[player_id]["y"],
                                "facing_h": players[player_id]["facing_h"],
                                "attack_sprite": players[player_id]["attack_sprite"],
                                "defeat_count": players[player_id]["defeat_count"],
                                "capture_count": players[player_id]["capture_count"],
                                "transformed_shiny": bool(players[player_id].get("transformed_shiny", False)),
                                "health": players[player_id]["health"],
                                "max_health": players[player_id]["max_health"],
                                "dead": players[player_id]["dead"],
                                "spawn_seq": players[player_id]["spawn_seq"],
                                "players": others_snapshot(player_id, cur_world, target_screen),
                                "npcs": NPCS,
                                "pets": pets_snapshot(cur_world, target_screen),
                                "coin_piles": _coin_piles_snapshot(cur_world, target_screen),
                                "obstacles": obstacles_snapshot(cur_world, target_screen),
                                "world_id": cur_world,
                                "world_screen": target_screen,
                                "world_background_url": _world_background_url(cur_world),
                                "world_mirrored": bool(target_screen != 1),
                                "magicoin": int(players[player_id].get("magicoin", 0) or 0),
                                "coin_icon_url": "/static-sprites/magicoin/coin.png",
                                "center_on_load": False,
                                "attack_sfx_url": players[player_id]["attack_sfx_url"],
                                "attack_sfx_volume_pct": players[player_id]["attack_sfx_volume_pct"],
                            }
                        )
                    )
                elif msg_type == "move":
                    next_x = float(data.get("x", players[player_id]["x"]))
                    next_y = float(data.get("y", players[player_id]["y"]))
                    pworld = _player_world_id(players[player_id])
                    pr = _player_hitbox_radius(players[player_id])
                    pwidth = float(normalize_sprite_width_px(players[player_id].get("sprite_width_px")))
                    if not _blocked_by_world_obstacles(
                        pworld,
                        _player_world_screen(players[player_id]),
                        next_x,
                        next_y,
                        pr,
                        width_px=pwidth,
                        height_px=pwidth,
                    ):
                        players[player_id]["x"] = next_x
                        players[player_id]["y"] = next_y
                    players[player_id]["viewport_w"] = max(
                        320.0, min(4096.0, float(data.get("viewport_w", players[player_id]["viewport_w"])))
                    )
                    players[player_id]["viewport_h"] = max(
                        240.0, min(4096.0, float(data.get("viewport_h", players[player_id]["viewport_h"])))
                    )
                    players[player_id]["facing_h"] = normalize_facing_h(
                        data.get("facing_h", players[player_id]["facing_h"])
                    )
                    conn = _connect_db()
                    try:
                        conn.execute(
                            """
                            UPDATE users
                            SET pos_x = ?, pos_y = ?, facing_h = ?
                            WHERE id = ?
                            """,
                            (
                                players[player_id]["x"],
                                players[player_id]["y"],
                                players[player_id]["facing_h"],
                                uid,
                            ),
                        )
                        conn.commit()
                    finally:
                        conn.close()
                    await broadcast_world(
                        _player_world_id(players[player_id]),
                        {
                            "type": "move",
                            "id": player_id,
                            "x": players[player_id]["x"],
                            "y": players[player_id]["y"],
                            "name": username,
                            "sprite": players[player_id]["sprite"],
                            "account_id": uid,
                            "shout_greeting": players[player_id]["shout_greeting"],
                            "sprite_width_px": players[player_id]["sprite_width_px"],
                            "facing_h": players[player_id]["facing_h"],
                            "attack_sprite": players[player_id]["attack_sprite"],
                            "defeat_count": players[player_id]["defeat_count"],
                            "capture_count": players[player_id]["capture_count"],
                            "transformed_shiny": bool(players[player_id].get("transformed_shiny", False)),
                            "health": players[player_id]["health"],
                            "max_health": players[player_id]["max_health"],
                            "dead": players[player_id]["dead"],
                            "spawn_seq": players[player_id]["spawn_seq"],
                            "world_screen": players[player_id]["world_screen"],
                        },
                        exclude_id=player_id,
                        world_screen=players[player_id]["world_screen"],
                    )
                elif msg_type == "shout":
                    text = players[player_id]["shout_greeting"]
                    await broadcast_world(
                        _player_world_id(players[player_id]),
                        {
                            "type": "speech",
                            "id": player_id,
                            "text": text,
                        },
                        exclude_id=player_id,
                        world_screen=players[player_id]["world_screen"],
                    )
                elif msg_type == "attack":
                    apply_live_damage = bool(data.get("damage_pet", False))
                    if apply_live_damage:
                        pets_changed, score_delta = damage_pets_for_player_attack(players[player_id])
                    else:
                        pets_changed, score_delta = collect_defeated_targets_for_attack(players[player_id])
                    if score_delta:
                        players[player_id]["defeat_count"] += score_delta
                    pile_changed, pile_amount = _collect_coin_piles_for_attack(players[player_id])
                    if pile_changed:
                        pets_changed = True
                    pworld = _player_world_id(players[player_id])
                    await broadcast_world(
                        pworld,
                        {
                            "type": "attack",
                            "id": player_id,
                            "facing_h": players[player_id]["facing_h"],
                        },
                        exclude_id=player_id,
                        world_screen=players[player_id]["world_screen"],
                    )
                    if score_delta:
                        await broadcast_all_world(
                            pworld,
                            {
                                "type": "score",
                                "id": player_id,
                                "defeat_count": players[player_id]["defeat_count"],
                                "capture_count": players[player_id]["capture_count"],
                            },
                        )
                    if pile_amount > 0 and uid:
                        try:
                            cur_balance = _get_user_magicoin(int(uid))
                            new_balance = cur_balance + int(pile_amount)
                            _set_user_magicoin(int(uid), new_balance)
                            players[player_id]["magicoin"] = new_balance
                            await broadcast_user_magicoin(
                                int(uid), new_balance,
                                delta=int(pile_amount), reason="coin_pile",
                            )
                        except Exception:
                            pass
                    if pets_changed:
                        await broadcast_pets_state(pworld)
                elif msg_type == "quiz_begin":
                    pet_id_q = int(data.get("pet_id", 0) or 0)
                    pet_q = pets_runtime.get(pet_id_q)
                    if (
                        pet_q
                        and not bool(pet_q.get("collected", False))
                        and int(pet_q.get("world_id", 1))
                        == _player_world_id(players[player_id])
                    ):
                        now_q = time.time()
                        pet_q["quiz_hold_until"] = now_q + 30.0
                        pet_q["vx"] = 0.0
                        pet_q["vy"] = 0.0
                        pet_q["target_x"] = None
                        pet_q["target_y"] = None
                        pet_q["await_move"] = False
                        pet_q["attack_until"] = 0.0
                        pet_q["attacking"] = False
                        pending_quiz_reward = random.randint(
                            MAGICOIN_QUIZ_REWARD_MIN, MAGICOIN_QUIZ_REWARD_MAX
                        )
                        try:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "quiz_reward",
                                        "pet_id": pet_id_q,
                                        "reward": pending_quiz_reward,
                                    }
                                )
                            )
                        except Exception:
                            pass
                        await broadcast_pets_state(_player_world_id(players[player_id]))
                elif msg_type == "quiz_result":
                    pet_id = int(data.get("pet_id", 0) or 0)
                    success = bool(data.get("success", False))
                    pet = pets_runtime.get(pet_id)
                    if not pet or bool(pet.get("collected", False)):
                        continue
                    pet.pop("quiz_hold_until", None)
                    pets_changed = False
                    now = time.time()
                    score_delta = 0
                    if success:
                        reward = int(pending_quiz_reward or 0)
                        if reward <= 0:
                            reward = random.randint(
                                MAGICOIN_QUIZ_REWARD_MIN, MAGICOIN_QUIZ_REWARD_MAX
                            )
                        new_balance = uid
                        try:
                            conn = _connect_db()
                            try:
                                conn.execute(
                                    """
                                    UPDATE users
                                    SET correct_answers = COALESCE(correct_answers, 0) + 1,
                                        magicoin = COALESCE(magicoin, 0) + ?
                                    WHERE id = ?
                                    """,
                                    (reward, uid),
                                )
                                conn.commit()
                                row = conn.execute(
                                    "SELECT magicoin FROM users WHERE id = ?", (uid,)
                                ).fetchone()
                                new_balance = int(row["magicoin"] or 0) if row else 0
                            finally:
                                conn.close()
                        except Exception:
                            new_balance = _get_user_magicoin(uid)
                        await broadcast_user_magicoin(
                            uid, new_balance, delta=reward, reason="quiz_correct"
                        )
                        health = max(0, min(PET_MAX_HEALTH, int(pet.get("health", PET_MAX_HEALTH))))
                        if health > 0 and _find_attackable_pet_id(players[player_id]) == pet_id:
                            quiz_dmg = PLAYER_ATTACK_DAMAGE
                            if bool(pet.get("shiny", False)):
                                quiz_dmg = max(1, int(round(PLAYER_ATTACK_DAMAGE * 0.5)))
                            pet["health"] = max(0, health - quiz_dmg)
                            if pet["health"] <= 0:
                                pet["vx"] = 0.0
                                pet["vy"] = 0.0
                                pet["target_x"] = None
                                pet["target_y"] = None
                                pet["await_move"] = False
                                pet["attacking"] = False
                                pet["attack_until"] = 0.0
                                pet["pause_until"] = 0.0
                                pet["sleep_until"] = 0.0
                                _spawn_coin_pile_for_pet(pet_id, pet)
                            pets_changed = True
                        await broadcast_world(
                            _player_world_id(players[player_id]),
                            {
                                "type": "attack",
                                "id": player_id,
                                "facing_h": players[player_id]["facing_h"],
                            },
                            exclude_id=player_id,
                        )
                    else:
                        pet["attack_until"] = now + 0.42
                        pet["attack_seq"] = int(pet.get("attack_seq", 0)) + 1
                        pet["attacking"] = bool(pet.get("attack_sprite"))
                        last_hit_players = pet.get("last_hit_seq_by_player") or {}
                        last_hit_players[player_id] = int(pet["attack_seq"])
                        pet["last_hit_seq_by_player"] = last_hit_players
                        pets_changed = True
                        info = players[player_id]
                        quiz_pet_dmg = PET_ATTACK_DAMAGE * 2 if bool(pet.get("shiny", False)) else PET_ATTACK_DAMAGE
                        if _damage_player(info, quiz_pet_dmg, now):
                            died_now = bool(info.pop("died_just_now", False))
                            if died_now:
                                new_balance, delta = _apply_death_penalty(info)
                                if delta != 0:
                                    try:
                                        uid_q = int(info.get("user_id") or 0)
                                        if uid_q > 0:
                                            await broadcast_user_magicoin(uid_q, new_balance, delta=delta, reason="death_penalty")
                                    except Exception:
                                        pass
                            await broadcast_all_world(
                                _player_world_id(info),
                                {
                                    "type": "player_state",
                                    "id": player_id,
                                    "x": info["x"],
                                    "y": info["y"],
                                    "health": info["health"],
                                    "max_health": int(info.get("max_health", PLAYER_MAX_HEALTH)),
                                    "dead": bool(info.get("dead", False)),
                                    "spawn_seq": int(info.get("spawn_seq", 0)),
                                },
                            )
                    if score_delta:
                        players[player_id]["defeat_count"] += score_delta
                        await broadcast_all_world(
                            _player_world_id(players[player_id]),
                            {
                                "type": "score",
                                "id": player_id,
                                "defeat_count": players[player_id]["defeat_count"],
                                "capture_count": players[player_id]["capture_count"],
                            },
                        )
                    if pets_changed:
                        await broadcast_pets_state(_player_world_id(players[player_id]))
                    pending_quiz_reward = 0
        except WebSocketDisconnect:
            pass
    finally:
        leaving_world = _player_world_id(players.get(player_id, {"world_id": world_id}))
        leaving_screen = _player_world_screen(players.get(player_id, {"world_screen": 1}))
        players.pop(player_id, None)
        await broadcast_world(
            leaving_world,
            {"type": "leave", "id": player_id, "world_screen": leaving_screen},
            world_screen=leaving_screen,
        )
