# Initial setup checklist

This project uses **Python**, **FastAPI**, **Uvicorn**, **WebSockets**, **SQLite** (one file on disk), and a small **HTML/CSS/JS** client. There is **no login** yet—players pick a display name in the browser.

Work inside your project folder (the folder that contains this file).

---

## 1. Check Python

You need **Python 3.10 or newer** (3.11+ recommended).

```bash
python3 --version
```

If that fails on Windows, try `py --version`.

---

## 2. Create a virtual environment

This keeps dependencies in one place for this project.

```bash
cd /path/to/your/project
python3 -m venv .venv
```

Activate it:

- **macOS / Linux:**

```bash
source .venv/bin/activate
```

- **Windows (Command Prompt):**

```cmd
.venv\Scripts\activate.bat
```

- **Windows (PowerShell):**

```powershell
.venv\Scripts\Activate.ps1
```

Your terminal prompt will usually show `(.venv)` when it is active.

---

## 3. Install dependencies

Create **`requirements.txt`** in the project root with exactly:

```text
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
```

Install:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 4. Create folders

From the project root:

```bash
mkdir -p app static data
```

- **`app/`** — Python server code  
- **`static/`** — files the browser loads (HTML, CSS, JS)  
- **`data/`** — SQLite database file will appear here automatically  

---

## 5. Add `.gitignore` (optional but recommended)

Create **`.gitignore`** in the project root:

```gitignore
.venv/
__pycache__/
*.pyc
data/*.db
```

---

## 6. Create `app/__init__.py`

Create an empty file so Python treats `app` as a package:

**`app/__init__.py`** — leave this file empty.

---

## 7. Create the server (`app/main.py`)

Create **`app/main.py`** with the following full contents:

```python
"""
Mini-MMO starter: FastAPI + WebSockets + SQLite file (no login yet).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "game.db"

# Fixed NPCs for the first world (expand later or load from SQLite).
NPCS: list[dict] = [
    {"id": "npc-guide", "x": 180, "y": 120, "name": "Guide"},
    {"id": "npc-bot", "x": 620, "y": 400, "name": "Bot"},
]

players: dict[str, dict] = {}


def _connect_db() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = _connect_db()
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
            INSERT OR IGNORE INTO meta (key, value)
            VALUES ('schema_version', '1');
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
                "name": info.get("name", "Guest"),
            }
        )
    return out


@app.websocket("/ws")
async def game_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    player_id = uuid.uuid4().hex[:10]
    players[player_id] = {
        "ws": websocket,
        "x": 400.0,
        "y": 300.0,
        "name": "Guest",
    }

    try:
        await websocket.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "id": player_id,
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
                "name": players[player_id]["name"],
            },
            exclude_id=player_id,
        )

        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type == "hello":
                name = str(data.get("name", "Guest"))[:32]
                players[player_id]["name"] = name
                await broadcast(
                    {
                        "type": "move",
                        "id": player_id,
                        "x": players[player_id]["x"],
                        "y": players[player_id]["y"],
                        "name": name,
                    },
                    exclude_id=player_id,
                )
            elif msg_type == "move":
                players[player_id]["x"] = float(data.get("x", players[player_id]["x"]))
                players[player_id]["y"] = float(data.get("y", players[player_id]["y"]))
                if "name" in data:
                    players[player_id]["name"] = str(data["name"])[:32]
                await broadcast(
                    {
                        "type": "move",
                        "id": player_id,
                        "x": players[player_id]["x"],
                        "y": players[player_id]["y"],
                        "name": players[player_id]["name"],
                    },
                    exclude_id=player_id,
                )
    except WebSocketDisconnect:
        pass
    finally:
        players.pop(player_id, None)
        await broadcast({"type": "leave", "id": player_id})


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")
```

---

## 8. Create the browser UI (`static/index.html`)

Everything the browser needs is in **one** plain HTML file: a few tags plus an inline `<script>` (no separate CSS or JS files, no `/assets` route).

Create **`static/index.html`**:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>game</title>
</head>
<body>
<p>Name <input id="name" type="text" maxlength="32"></p>
<p id="status">…</p>
<canvas id="world" width="800" height="600"></canvas>
<p>Move: arrows or WASD.</p>
<script>
(function () {
  var canvas = document.getElementById("world");
  var ctx = canvas.getContext("2d");
  var nameInput = document.getElementById("name");
  var statusEl = document.getElementById("status");
  var keys = {};
  var speed = 4;
  var myId = null;
  var ws = null;
  var peers = {};
  var npcs = [];
  var me = { x: 400, y: 300, name: "Guest" };

  function setStatus(t) { statusEl.textContent = t; }

  function draw() {
    var w = canvas.width, h = canvas.height;
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = "#000";
    ctx.strokeRect(0, 0, w, h);

    ctx.fillStyle = "#070";
    for (var i = 0; i < npcs.length; i++) {
      var n = npcs[i];
      ctx.beginPath();
      ctx.arc(n.x, n.y, 14, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#000";
      ctx.font = "12px sans-serif";
      ctx.fillText(n.name, n.x - 20, n.y - 20);
      ctx.fillStyle = "#070";
    }

    for (var pid in peers) {
      if (!Object.prototype.hasOwnProperty.call(peers, pid)) continue;
      var p = peers[pid];
      ctx.fillStyle = "#c00";
      ctx.beginPath();
      ctx.arc(p.x, p.y, 12, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#000";
      ctx.font = "12px sans-serif";
      ctx.fillText(p.name || pid, p.x - 18, p.y - 18);
    }

    ctx.fillStyle = "#00c";
    ctx.beginPath();
    ctx.arc(me.x, me.y, 12, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#000";
    ctx.font = "14px sans-serif";
    ctx.fillText(me.name, me.x - 24, me.y - 18);

    requestAnimationFrame(draw);
  }

  function connect() {
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(proto + "//" + location.host + "/ws");

    ws.onopen = function () {
      setStatus("ok");
      sendHello();
    };

    ws.onmessage = function (ev) {
      var msg = JSON.parse(ev.data);
      if (msg.type === "hello") {
        myId = msg.id;
        npcs = msg.npcs || [];
        me.x = 400;
        me.y = 300;
        peers = {};
        var pl = msg.players || [];
        for (var j = 0; j < pl.length; j++) {
          var p = pl[j];
          peers[p.id] = { x: p.x, y: p.y, name: p.name };
        }
      } else if (msg.type === "join") {
        peers[msg.id] = { x: msg.x, y: msg.y, name: msg.name };
      } else if (msg.type === "leave") {
        delete peers[msg.id];
      } else if (msg.type === "move") {
        if (msg.id === myId) return;
        peers[msg.id] = { x: msg.x, y: msg.y, name: msg.name };
      }
    };

    ws.onclose = function () { setStatus("disconnected"); };
    ws.onerror = function () { setStatus("error"); };
  }

  function sendHello() {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    var name = (nameInput.value || "Guest").trim().slice(0, 32) || "Guest";
    me.name = name;
    ws.send(JSON.stringify({ type: "hello", name: name }));
  }

  function sendMove() {
    if (!ws || ws.readyState !== WebSocket.OPEN || !myId) return;
    ws.send(JSON.stringify({
      type: "move",
      x: me.x,
      y: me.y,
      name: me.name
    }));
  }

  window.onkeydown = function (e) {
    keys[e.key.toLowerCase()] = true;
  };
  window.onkeyup = function (e) {
    keys[e.key.toLowerCase()] = false;
  };

  nameInput.onchange = sendHello;

  function tick() {
    var dx = 0, dy = 0;
    if (keys["arrowleft"] || keys["a"]) dx -= 1;
    if (keys["arrowright"] || keys["d"]) dx += 1;
    if (keys["arrowup"] || keys["w"]) dy -= 1;
    if (keys["arrowdown"] || keys["s"]) dy += 1;
    if (dx !== 0 || dy !== 0) {
      var len = Math.sqrt(dx * dx + dy * dy) || 1;
      me.x = Math.max(12, Math.min(canvas.width - 12, me.x + (dx / len) * speed));
      me.y = Math.max(12, Math.min(canvas.height - 12, me.y + (dy / len) * speed));
      sendMove();
    }
    requestAnimationFrame(tick);
  }

  connect();
  requestAnimationFrame(draw);
  requestAnimationFrame(tick);
})();
</script>
</body>
</html>
```

---

## 9. Run the server (development)

With the virtual environment **activated** and your shell in the **project root**:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- **`--host 0.0.0.0`** — allows other computers on the network to connect (needed for the Pi and other devices).  
- **`--reload`** — auto-restarts when you edit Python files (fine on your laptop; on the Pi you may omit it later).

Open in a browser:

- This machine: **http://127.0.0.1:8000/**  
- Another device on the same network: **http://\<pi-ip-address\>:8000/**  

Open two browser windows to confirm both dots appear and move in real time.

Stop the server with **Ctrl+C**.

---

## 10. Confirm SQLite was created

After the first successful start, you should see:

```text
data/game.db
```

That file is your relational database (no separate database server).

---

## 11. Later: run on boot (Raspberry Pi)

You said this can wait—that is fine. When you are ready, the usual approach is a **systemd** service that runs `uvicorn` with the same `app.main:app` target and `WorkingDirectory` set to your project folder. We can add exact unit-file steps when you get there.

---

## Quick folder map

```text
your-project/
  INITIAL_SETUP.md
  requirements.txt
  .gitignore
  app/
    __init__.py
    main.py
  static/
    index.html
  data/
    game.db          ← appears after first run
```

You now have a working **single-screen multiplayer test** with **two NPCs**, **SQLite** bootstrapped, and room to grow the game step by step.
