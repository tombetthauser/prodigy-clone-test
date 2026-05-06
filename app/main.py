"""
Club MMO: FastAPI + sessions + SQLite users + WebSockets + sprite uploads.
World background: static/bg/world.png or world.jpg (see /config).

Demo only: passwords are stored in SQLite as plain text. Never use that on a real site.
"""

from __future__ import annotations

import json
import asyncio
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

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_AUDIO_UPLOAD_BYTES = 15 * 1024 * 1024
SFX_ALLOWED_SUFFIXES = frozenset({".wav", ".ogg", ".mp3", ".m4a", ".webm", ".flac"})
SPRITE_NAME_RE = re.compile(r"^[a-f0-9]{32}\.(png|jpg|jpeg|gif|webp)$")

NPCS: list[dict] = []
pets_runtime: dict[int, dict] = {}
pet_loop_task: asyncio.Task | None = None

players: dict[str, dict] = {}

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


def normalize_volume(v: int | float | str | None, default: int = 35) -> int:
    try:
        n = int(float(v if v is not None else default))
    except (TypeError, ValueError):
        n = default
    return max(0, min(100, n))


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


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_WORLD.mkdir(parents=True, exist_ok=True)
    ARCHIVE_SPRITES.mkdir(parents=True, exist_ok=True)
    (STATIC_DIR / "bg").mkdir(parents=True, exist_ok=True)
    SFX_DIR.mkdir(parents=True, exist_ok=True)
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
        conn.commit()
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pet_loop_task
    init_db()
    load_pets_runtime()
    pet_loop_task = asyncio.create_task(pet_runtime_loop())
    yield
    if pet_loop_task:
        pet_loop_task.cancel()


app = FastAPI(lifespan=lifespan)

app.mount("/sfx", StaticFiles(directory=str(SFX_DIR)), name="sfx")


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


async def notify_world_background_changed() -> None:
    await broadcast_all({"type": "reload_world_bg", "t": int(time.time() * 1000)})


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


def pets_snapshot() -> list[dict]:
    out: list[dict] = []
    now = time.time()
    for pid in sorted(pets_runtime.keys()):
        p = pets_runtime[pid]
        sleeping = now < float(p.get("sleep_until", 0.0))
        out.append(
            {
                "id": pid,
                "name": p["name"],
                "x": p["x"],
                "y": p["y"],
                "facing_h": p["facing_h"],
                "sprite": p.get("sprite", ""),
                "attack_sprite": p.get("attack_sprite", ""),
                "attacking": bool(p.get("attacking", False)),
                "sleeping": sleeping,
                "size_px": normalize_pet_size_px(p.get("size_px")),
                "speed_px": normalize_pet_speed_px(p.get("speed_px")),
            }
        )
    return out


def load_pets_runtime() -> None:
    pets_runtime.clear()
    conn = _connect_db()
    try:
        rows = conn.execute(
            """
            SELECT id, name, sprite_file, attack_sprite_file, x, y, facing_h, size_px, speed_px, code_mode
            FROM pets ORDER BY id
            """
        ).fetchall()
    finally:
        conn.close()
    for r in rows:
        sid = normalize_attack_sprite_file(r["sprite_file"])
        aid = normalize_attack_sprite_file(r["attack_sprite_file"])
        pets_runtime[int(r["id"])] = {
            "name": (r["name"] or "Pet")[:24],
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
        }


async def broadcast_pets_state() -> None:
    await broadcast_all({"type": "pets", "pets": pets_snapshot()})


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

async def pet_runtime_loop() -> None:
    while True:
        if pets_runtime:
            changed = False
            now = time.time()
            for pid, p in pets_runtime.items():
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
                        if random.random() < 0.55:
                            p["pause_until"] = now + random.uniform(0.8, 2.3)
                attacking = now < p.get("attack_until", 0.0) and bool(p.get("attack_sprite"))
                p["attacking"] = attacking
                paused = now < float(p.get("pause_until", 0.0))
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
                        p["x"] = max(24, min(1360, float(p["x"]) + float(p.get("vx", 0.0))))
                        p["y"] = max(24, min(760, float(p["y"]) + float(p.get("vy", 0.0))))
                        if abs(float(p.get("vx", 0.0))) > 0.02:
                            p["facing_h"] = -1 if float(p.get("vx", 0.0)) < 0 else 1
                _persist_pet_state(pid, p)
                changed = True
            if changed:
                await broadcast_pets_state()
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


def others_snapshot(except_id: str) -> list[dict]:
    out: list[dict] = []
    for pid, info in players.items():
        if pid == except_id:
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
            }
        )
    return out


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
        return RedirectResponse("/play", status_code=303)
    return RedirectResponse("/login", status_code=303)


@app.get("/login")
async def login_page():
    return FileResponse(STATIC_DIR / "login.html")


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
    return _redirect_with_session_cookie("/play", int(row["id"]))


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


@app.get("/api/config/summary")
async def api_config_summary(user: sqlite3.Row = Depends(require_login)):
    conn = _connect_db()
    try:
        user_rows = conn.execute(
            """
            SELECT
                id, username, sprite_file, sprite_flipped, shout_greeting,
                sprite_width_px, attack_sprite_file, attack_sprite_flipped
            FROM users ORDER BY username COLLATE NOCASE
            """,
        ).fetchall()
        pet_rows = conn.execute(
            """
            SELECT
                id, name, sprite_file, sprite_flipped,
                attack_sprite_file, attack_sprite_flipped, size_px, speed_px, code_mode
            FROM pets ORDER BY id
            """
        ).fetchall()
    finally:
        conn.close()
    music_file = get_music_file()
    return {
        "speech_font_px": get_speech_font_px(),
        "sfx_volume": get_sfx_volume(),
        "music_volume": get_music_volume(),
        "music_url": f"/sfx/{music_file}" if music_file else "",
        "shadow_darkness_pct": get_shadow_darkness_pct(),
        "shadow_height_pct": get_shadow_height_pct(),
        "shadow_blur_px": get_shadow_blur_px(),
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
            }
            for r in user_rows
        ],
        "pets": [
            {
                "id": p["id"],
                "name": (p["name"] or "Pet")[:24],
                "sprite_url": f"/sprites/{p['sprite_file']}" if normalize_attack_sprite_file(p["sprite_file"]) else "",
                "sprite_flipped": bool(p["sprite_flipped"]),
                "attack_sprite_url": (
                    f"/sprites/{p['attack_sprite_file']}"
                    if normalize_attack_sprite_file(p["attack_sprite_file"])
                    else ""
                ),
                "attack_sprite_flipped": bool(p["attack_sprite_flipped"]),
                "size_px": normalize_pet_size_px(p["size_px"]),
                "speed_px": normalize_pet_speed_px(p["speed_px"]),
                "code_mode": (p["code_mode"] or "default"),
            }
            for p in pet_rows
        ],
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
            INSERT INTO pets (name, x, y, facing_h)
            VALUES (?, ?, ?, ?)
            """,
            ("Pet", 420.0 + random.uniform(-40, 40), 320.0 + random.uniform(-40, 40), 1),
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
):
    text = (name or "Pet").strip()[:24] or "Pet"
    conn = _connect_db()
    try:
        cur = conn.execute("UPDATE pets SET name = ? WHERE id = ?", (text, pet_id))
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
):
    s = normalize_pet_size_px(size_px)
    v = normalize_pet_speed_px(speed_px)
    conn = _connect_db()
    try:
        cur = conn.execute(
            "UPDATE pets SET size_px = ?, speed_px = ? WHERE id = ?",
            (s, v, pet_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return RedirectResponse("/config?error=no_pet", status_code=303)
    finally:
        conn.close()
    load_pets_runtime()
    await broadcast_pets_state()
    return RedirectResponse("/config?saved=pet_stats", status_code=303)


@app.post("/config/pets/{pet_id}/recenter")
async def config_pet_recenter(
    pet_id: int,
    user: sqlite3.Row = Depends(require_login),
):
    conn = _connect_db()
    try:
        cur = conn.execute(
            "UPDATE pets SET x = ?, y = ?, facing_h = ? WHERE id = ?",
            (PET_CENTER_X, PET_CENTER_Y, 1, pet_id),
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
):
    mode = "code" if (code_mode or "").strip().lower() == "code" else "default"
    conn = _connect_db()
    try:
        cur = conn.execute("UPDATE pets SET code_mode = ? WHERE id = ?", (mode, pet_id))
        conn.commit()
        if cur.rowcount == 0:
            return RedirectResponse("/config?error=no_pet", status_code=303)
    finally:
        conn.close()
    if mode == "code":
        if not _get_pet_code(pet_id):
            _set_pet_code(pet_id, DEFAULT_PET_CODE)
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
async def api_pet_code_get(pet_id: int, user: sqlite3.Row = Depends(require_login)):
    conn = _connect_db()
    try:
        row = conn.execute("SELECT 1 FROM pets WHERE id = ?", (pet_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404)
    code = _get_pet_code(pet_id) or DEFAULT_PET_CODE
    return {"pet_id": pet_id, "code": code}


@app.post("/api/pets/{pet_id}/code")
async def api_pet_code_set(
    pet_id: int,
    request: Request,
    user: sqlite3.Row = Depends(require_login),
):
    payload = await request.json()
    code = str((payload or {}).get("code", ""))[:20000]
    conn = _connect_db()
    try:
        row = conn.execute("SELECT 1 FROM pets WHERE id = ?", (pet_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404)
    _set_pet_code(pet_id, code or DEFAULT_PET_CODE)
    load_pets_runtime()
    await broadcast_pets_state()
    return {"ok": True}


@app.post("/config/pets/{pet_id}/delete")
async def config_pet_delete(pet_id: int, user: sqlite3.Row = Depends(require_login)):
    conn = _connect_db()
    try:
        row = conn.execute(
            "SELECT sprite_file, attack_sprite_file, name FROM pets WHERE id = ?",
            (pet_id,),
        ).fetchone()
        if not row:
            return RedirectResponse("/config?error=no_pet", status_code=303)
        if normalize_attack_sprite_file(row["sprite_file"]):
            archive_user_sprite_file(row["sprite_file"], f"pet_{row['name']}")
        if normalize_attack_sprite_file(row["attack_sprite_file"]):
            archive_user_sprite_file(row["attack_sprite_file"], f"pet_{row['name']}_attack")
        conn.execute("DELETE FROM pets WHERE id = ?", (pet_id,))
        conn.commit()
    finally:
        conn.close()
    load_pets_runtime()
    await broadcast_pets_state()
    return RedirectResponse("/config?saved=pet_delete", status_code=303)


async def _config_pet_image_upload(
    pet_id: int, file: UploadFile, *, attack: bool
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
    col = "attack_sprite_file" if attack else "sprite_file"
    flip_col = "attack_sprite_flipped" if attack else "sprite_flipped"
    conn = _connect_db()
    try:
        row = conn.execute(f"SELECT {col}, name FROM pets WHERE id = ?", (pet_id,)).fetchone()
        if not row:
            dest.unlink(missing_ok=True)
            return RedirectResponse("/config?error=no_pet", status_code=303)
        old = normalize_attack_sprite_file(row[col])
        if old:
            archive_user_sprite_file(old, f"pet_{row['name']}{'_attack' if attack else ''}")
        conn.execute(
            f"UPDATE pets SET {col} = ?, {flip_col} = 0 WHERE id = ?",
            (fname, pet_id),
        )
        conn.commit()
    finally:
        conn.close()
    load_pets_runtime()
    await broadcast_pets_state()
    return RedirectResponse("/config?saved=pet_sprite", status_code=303)


@app.post("/config/pets/{pet_id}/sprite")
async def config_pet_sprite_upload(
    pet_id: int, user: sqlite3.Row = Depends(require_login), file: UploadFile = File(...)
):
    return await _config_pet_image_upload(pet_id, file, attack=False)


@app.post("/config/pets/{pet_id}/attack-sprite")
async def config_pet_attack_sprite_upload(
    pet_id: int, user: sqlite3.Row = Depends(require_login), file: UploadFile = File(...)
):
    return await _config_pet_image_upload(pet_id, file, attack=True)


async def _config_pet_image_mutate(
    pet_id: int, *, attack: bool, action: str
) -> RedirectResponse:
    col = "attack_sprite_file" if attack else "sprite_file"
    flip_col = "attack_sprite_flipped" if attack else "sprite_flipped"
    conn = _connect_db()
    try:
        row = conn.execute(
            f"SELECT {col}, {flip_col}, name FROM pets WHERE id = ?", (pet_id,)
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
        archive_user_sprite_file(old, f"pet_{row['name']}{'_attack' if attack else ''}")
        new_flip = 0

    conn = _connect_db()
    try:
        conn.execute(
            f"UPDATE pets SET {col} = ?, {flip_col} = ? WHERE id = ?",
            (new_file, new_flip, pet_id),
        )
        conn.commit()
    finally:
        conn.close()
    load_pets_runtime()
    await broadcast_pets_state()
    return RedirectResponse("/config?saved=pet_sprite", status_code=303)


@app.post("/config/pets/{pet_id}/flip-sprite")
async def config_pet_flip_sprite(pet_id: int, user: sqlite3.Row = Depends(require_login)):
    return await _config_pet_image_mutate(pet_id, attack=False, action="flip")


@app.post("/config/pets/{pet_id}/trim-white")
async def config_pet_trim_white(pet_id: int, user: sqlite3.Row = Depends(require_login)):
    return await _config_pet_image_mutate(pet_id, attack=False, action="trim_white")


@app.post("/config/pets/{pet_id}/trim-bounds")
async def config_pet_trim_bounds(pet_id: int, user: sqlite3.Row = Depends(require_login)):
    return await _config_pet_image_mutate(pet_id, attack=False, action="trim_bounds")


@app.post("/config/pets/{pet_id}/flip-attack-sprite")
async def config_pet_flip_attack_sprite(
    pet_id: int, user: sqlite3.Row = Depends(require_login)
):
    return await _config_pet_image_mutate(pet_id, attack=True, action="flip")


@app.post("/config/pets/{pet_id}/trim-attack-white")
async def config_pet_trim_attack_white(
    pet_id: int, user: sqlite3.Row = Depends(require_login)
):
    return await _config_pet_image_mutate(pet_id, attack=True, action="trim_white")


@app.post("/config/pets/{pet_id}/trim-attack-bounds")
async def config_pet_trim_attack_bounds(
    pet_id: int, user: sqlite3.Row = Depends(require_login)
):
    return await _config_pet_image_mutate(pet_id, attack=True, action="trim_bounds")


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


@app.get("/sprites/{filename}")
async def serve_sprite(filename: str):
    if not SPRITE_NAME_RE.match(filename):
        raise HTTPException(status_code=404)
    path = UPLOAD_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path)


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
    facing_h = normalize_facing_h(user["facing_h"])
    attack_file = normalize_attack_sprite_file(user["attack_sprite_file"])
    attack_sprite_url = f"/sprites/{attack_file}" if attack_file else ""

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
                    "music_url": (f"/sfx/{get_music_file()}" if get_music_file() else ""),
                    "shout_greeting": greet,
                    "sprite_width_px": sprite_width_px,
                    "x": pos_x,
                    "y": pos_y,
                    "facing_h": facing_h,
                    "attack_sprite": attack_sprite_url,
                    "players": others_snapshot(player_id),
                    "npcs": NPCS,
                    "pets": pets_snapshot(),
                }
            )
        )
        await broadcast(
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
            },
            exclude_id=player_id,
        )

        try:
            while True:
                raw = await websocket.receive_text()
                data = json.loads(raw)
                msg_type = data.get("type")

                if msg_type == "hello":
                    await broadcast(
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
                        },
                        exclude_id=player_id,
                    )
                elif msg_type == "move":
                    players[player_id]["x"] = float(data.get("x", players[player_id]["x"]))
                    players[player_id]["y"] = float(data.get("y", players[player_id]["y"]))
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
                    await broadcast(
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
                        },
                        exclude_id=player_id,
                    )
                elif msg_type == "shout":
                    text = players[player_id]["shout_greeting"]
                    await broadcast(
                        {
                            "type": "speech",
                            "id": player_id,
                            "text": text,
                        },
                        exclude_id=player_id,
                    )
                elif msg_type == "attack":
                    await broadcast(
                        {
                            "type": "attack",
                            "id": player_id,
                            "facing_h": players[player_id]["facing_h"],
                        },
                        exclude_id=player_id,
                    )
        except WebSocketDisconnect:
            pass
    finally:
        players.pop(player_id, None)
        await broadcast({"type": "leave", "id": player_id})
