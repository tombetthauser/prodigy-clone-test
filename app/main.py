"""
Club MMO: FastAPI + sessions + SQLite users + WebSockets + sprite uploads.
World background: static/bg/world.png or world.jpg (see /config).

Demo only: passwords are stored in SQLite as plain text. Never use that on a real site.
"""

from __future__ import annotations

import json
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
from PIL import Image, UnidentifiedImageError

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
ARCHIVE_WORLD = DATA_DIR / "archives" / "world"
ARCHIVE_SPRITES = DATA_DIR / "archives" / "sprites"
DB_PATH = DATA_DIR / "game.db"
STATIC_DIR = BASE_DIR / "static"
WORLD_BG_PNG = STATIC_DIR / "bg" / "world.png"
WORLD_BG_JPG = STATIC_DIR / "bg" / "world.jpg"

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
SPRITE_NAME_RE = re.compile(r"^[a-f0-9]{32}\.(png|jpg|jpeg|gif|webp)$")

NPCS: list[dict] = [
    {"id": "npc-guide", "x": 180, "y": 120, "name": "Guide"},
    {"id": "npc-bot", "x": 620, "y": 400, "name": "Bot"},
]

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


def normalize_shout_greeting(raw: str | None) -> str:
    text = (raw or "").strip() or "Hello!"
    return text[:64]


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_WORLD.mkdir(parents=True, exist_ok=True)
    ARCHIVE_SPRITES.mkdir(parents=True, exist_ok=True)
    (STATIC_DIR / "bg").mkdir(parents=True, exist_ok=True)
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
                shout_greeting TEXT NOT NULL DEFAULT 'Hello!'
            );
            """
        )
        _migrate_users_password_column(conn)
        _migrate_users_sprite_flipped(conn)
        _migrate_users_shout_greeting(conn)
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
            INSERT OR IGNORE INTO meta (key, value)
            VALUES ('schema_version', '7');
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO meta (key, value)
            VALUES ('sprite_width', '48');
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO meta (key, value)
            VALUES ('speech_font_px', '20');
            """
        )
        conn.commit()
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(lifespan=lifespan)


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
    await broadcast_all(
        {
            "type": "settings",
            "sprite_size": get_sprite_width(),
            "speech_font_px": get_speech_font_px(),
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


@app.get("/api/config/summary")
async def api_config_summary(user: sqlite3.Row = Depends(require_login)):
    conn = _connect_db()
    try:
        rows = conn.execute(
            """
            SELECT id, username, sprite_file, sprite_flipped, shout_greeting
            FROM users ORDER BY username COLLATE NOCASE
            """,
        ).fetchall()
    finally:
        conn.close()
    return {
        "sprite_width": get_sprite_width(),
        "speech_font_px": get_speech_font_px(),
        "users": [
            {
                "id": r["id"],
                "username": r["username"],
                "sprite_url": f"/sprites/{r['sprite_file']}",
                "sprite_flipped": bool(r["sprite_flipped"]),
                "shout_greeting": normalize_shout_greeting(r["shout_greeting"]),
            }
            for r in rows
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


@app.post("/config/sprite-size")
async def config_sprite_size(
    user: sqlite3.Row = Depends(require_login),
    width: int = Form(...),
):
    try:
        set_sprite_width(width)
    except (TypeError, ValueError):
        return RedirectResponse("/config?error=bad_width", status_code=303)
    await notify_sprite_settings_changed()
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
            "SELECT id, username, sprite_file FROM users WHERE id = ?",
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
    sprite_px = get_sprite_width()
    greet = normalize_shout_greeting(user["shout_greeting"])

    players[player_id] = {
        "ws": websocket,
        "x": 400.0,
        "y": 300.0,
        "name": username,
        "sprite": f"/sprites/{user['sprite_file']}",
        "user_id": uid,
        "shout_greeting": greet,
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
                    "sprite_size": sprite_px,
                    "speech_font_px": get_speech_font_px(),
                    "shout_greeting": greet,
                    "players": others_snapshot(player_id),
                    "npcs": NPCS,
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
                        },
                        exclude_id=player_id,
                    )
                elif msg_type == "move":
                    players[player_id]["x"] = float(data.get("x", players[player_id]["x"]))
                    players[player_id]["y"] = float(data.get("y", players[player_id]["y"]))
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
        except WebSocketDisconnect:
            pass
    finally:
        players.pop(player_id, None)
        await broadcast({"type": "leave", "id": player_id})
