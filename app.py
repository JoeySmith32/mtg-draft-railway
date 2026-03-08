"""
MTG Pick-2 Draft Server
Run this file with VS Code's Run button (F5) or python app.py
The browser will open automatically at http://localhost:5000
"""

import subprocess
import sys
import os

# ── Auto-install dependencies if missing ─────────────────────────────────────
def install_dependencies():
    req_file = os.path.join(os.path.dirname(__file__), "requirements.txt")
    try:
        import flask
        import flask_socketio
        import requests
    except ImportError:
        print("Installing dependencies...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-r", req_file,
             "--user", "-q", "--no-warn-script-location"],
        )
        print("Done! Restarting...")
        # Re-launch this same script so the newly installed packages are found
        os.execv(sys.executable, [sys.executable] + sys.argv)

install_dependencies()

# ── Imports (after install check) ────────────────────────────────────────────
import uuid
import random
import threading
import webbrowser

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room
import requests as req

# Use inspect to get the real file path - most reliable method on Windows
import inspect
BASE_DIR = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')
os.chdir(BASE_DIR)

print(f'  Script dir:      {BASE_DIR}')
print(f'  Templates dir:   {TEMPLATE_DIR}')
print(f'  Templates exist: {os.path.isdir(TEMPLATE_DIR)}')

app = Flask(__name__, template_folder=TEMPLATE_DIR)
app.secret_key = "mtg-draft-secret-key"
socketio = SocketIO(app, cors_allowed_origins="*")

games = {}

SCRYFALL_NAMED = "https://api.scryfall.com/cards/named"


# ── Card fetching ─────────────────────────────────────────────────────────────

def fetch_card(name: str) -> dict | None:
    try:
        r = req.get(SCRYFALL_NAMED, params={"fuzzy": name}, timeout=5)
        if r.status_code == 200:
            data = r.json()
            return {
                "id": data["id"],
                "name": data["name"],
                "image": (
                    data["image_uris"]["normal"]
                    if "image_uris" in data
                    else data["card_faces"][0]["image_uris"]["normal"]
                ),
                "mana_cost": data.get("mana_cost", ""),
                "type_line": data.get("type_line", ""),
                "oracle_text": data.get("oracle_text", ""),
            }
    except Exception as e:
        print(f"Scryfall error for '{name}': {e}")
    return None


def resolve_cards(card_names: list[str]) -> list[dict]:
    cards = []
    for name in card_names:
        card = fetch_card(name)
        if card:
            cards.append(card)
        else:
            print(f"  Could not resolve: {name}")
    return cards


# ── Draft logic helpers ───────────────────────────────────────────────────────

def pass_direction(pack_num: int) -> str:
    """Packs 1 & 3 pass left, pack 2 passes right."""
    return "left" if pack_num in (1, 3) else "right"


def next_seat(current: int, direction: str, total: int = 4) -> int:
    return (current + 1) % total if direction == "left" else (current - 1) % total


def all_packs_empty(game: dict) -> bool:
    return all(len(p) == 0 for p in game["packs"].values())


def advance_to_next_pack(game: dict):
    """Move to pack 2 or 3, or end the draft."""
    game["pack_num"] += 1
    if game["pack_num"] > 3:
        game["phase"] = "done"
        return

    order = game["player_order"]
    for pid in order:
        game["packs"][pid] = game["player_packs"][pid][game["pack_num"] - 1]

    game["waiting_to_pick"] = set(order)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/create", methods=["POST"])
def create_game():
    data = request.json
    card_list_raw = data.get("cards", "")
    player_names = data.get("players", ["Player 1", "Player 2", "Player 3", "Player 4"])

    card_names = [c.strip() for c in card_list_raw.strip().splitlines() if c.strip()]

    if len(card_names) < 12:
        return jsonify({"error": "Need at least 12 cards to start a draft."}), 400

    print(f"\nResolving {len(card_names)} cards via Scryfall...")
    resolved = resolve_cards(card_names)
    print(f"Resolved {len(resolved)} cards successfully.\n")

    if len(resolved) < 12:
        return jsonify({"error": f"Only resolved {len(resolved)} cards. Need at least 12."}), 400

    random.shuffle(resolved)

    # Each of 12 packs (4 players x 3 packs) should have exactly 15 cards.
    # If fewer than 180 cards provided, trim pack size proportionally but keep all packs equal.
    PACK_SIZE = 15
    NUM_PACKS = 12  # 4 players x 3 packs

    if len(resolved) < NUM_PACKS:
        return jsonify({"error": f"Need at least {NUM_PACKS} cards (got {len(resolved)})."}), 400

    # Use exactly PACK_SIZE per pack if we have enough, otherwise divide evenly
    pack_size = min(PACK_SIZE, len(resolved) // NUM_PACKS)
    pack_size = max(pack_size, 1)

    # Trim to exact multiple so every pack is identical size
    total_used = pack_size * NUM_PACKS
    resolved = resolved[:total_used]

    all_pack_list = [resolved[i * pack_size:(i + 1) * pack_size] for i in range(NUM_PACKS)]

    game_id = str(uuid.uuid4())[:8]
    player_ids = [str(uuid.uuid4())[:8] for _ in range(4)]

    # Each player gets 3 packs
    player_packs = {}
    for i, pid in enumerate(player_ids):
        player_packs[pid] = [
            all_pack_list[i * 3],
            all_pack_list[i * 3 + 1],
            all_pack_list[i * 3 + 2],
        ]

    game = {
        "id": game_id,
        "phase": "drafting",
        "pack_num": 1,
        "players": {pid: {"name": player_names[i], "pool": []} for i, pid in enumerate(player_ids)},
        "player_packs": player_packs,
        "packs": {pid: player_packs[pid][0] for pid in player_ids},
        "pending_picks": {pid: [] for pid in player_ids},
        "waiting_to_pick": set(player_ids),
        "player_order": player_ids,
    }
    games[game_id] = game

    join_links = {
        game["players"][pid]["name"]: f"/draft/{game_id}/{pid}"
        for pid in player_ids
    }
    return jsonify({"game_id": game_id, "links": join_links, "player_ids": player_ids})


@app.route("/draft/<game_id>/<player_id>")
def draft_view(game_id, player_id):
    game = games.get(game_id)
    if not game or player_id not in game["players"]:
        return "Game not found", 404
    player_name = game["players"][player_id]["name"]
    return render_template("draft.html", game_id=game_id, player_id=player_id, player_name=player_name)


@app.route("/api/state/<game_id>/<player_id>")
def get_state(game_id, player_id):
    game = games.get(game_id)
    if not game:
        return jsonify({"error": "Game not found"}), 404

    waiting = player_id in game.get("waiting_to_pick", set())

    return jsonify({
        "phase": game["phase"],
        "pack_num": game["pack_num"],
        "direction": pass_direction(game["pack_num"]) if game["phase"] != "done" else None,
        "pack": game["packs"].get(player_id, []),
        "pool": game["players"][player_id]["pool"],
        "pending": game["pending_picks"].get(player_id, []),
        "waiting_to_pick": waiting,
        "players": {
            pid: {
                "name": info["name"],
                "pool_size": len(info["pool"]),
                "waiting": pid in game.get("waiting_to_pick", set()),
            }
            for pid, info in game["players"].items()
        },
    })


# ── Socket events ─────────────────────────────────────────────────────────────

@socketio.on("join")
def on_join(data):
    join_room(data["game_id"])
    emit("joined", {"player_id": data["player_id"]})


@socketio.on("stage_pick")
def on_stage_pick(data):
    game_id  = data["game_id"]
    player_id = data["player_id"]
    card_id   = data["card_id"]

    game = games.get(game_id)
    if not game or game["phase"] != "drafting":
        return

    if player_id not in game.get("waiting_to_pick", set()):
        emit("error", {"msg": "Not your turn to pick"})
        return

    pending = game["pending_picks"][player_id]
    pack    = game["packs"].get(player_id, [])
    card    = next((c for c in pack if c["id"] == card_id), None)

    if not card:
        emit("error", {"msg": "Card not in your pack"})
        return

    # Toggle selection
    if card_id in [c["id"] for c in pending]:
        game["pending_picks"][player_id] = [c for c in pending if c["id"] != card_id]
    else:
        if len(pending) >= 2:
            emit("error", {"msg": "You can only pick 2 cards"})
            return
        game["pending_picks"][player_id].append(card)

    emit("staged", {"pending": game["pending_picks"][player_id]})


@socketio.on("confirm_picks")
def on_confirm_picks(data):
    game_id   = data["game_id"]
    player_id = data["player_id"]

    game    = games.get(game_id)
    if not game or game["phase"] != "drafting":
        return

    pending = game["pending_picks"].get(player_id, [])
    pack    = game["packs"].get(player_id, [])

    # Must pick 2, unless only 1 card remains in pack
    required = min(2, len(pack))
    if len(pending) < required:
        emit("error", {"msg": f"Must select {required} card(s) before confirming"})
        return
    if len(pending) == 0:
        emit("error", {"msg": "No cards selected"})
        return

    # Move pending picks into the player's pool
    picked_ids = {c["id"] for c in pending}
    game["players"][player_id]["pool"].extend(pending)
    game["packs"][player_id] = [c for c in pack if c["id"] not in picked_ids]
    game["pending_picks"][player_id] = []
    game["waiting_to_pick"].discard(player_id)

    socketio.emit("player_picked", {
        "player_id": player_id,
        "player_name": game["players"][player_id]["name"],
        "num_picked": len(pending),
    }, room=game_id)

    # Once everyone has picked, pass packs
    if len(game["waiting_to_pick"]) == 0:
        direction = pass_direction(game["pack_num"])
        order     = game["player_order"]
        new_packs = {}

        for i, pid in enumerate(order):
            donor = order[next_seat(i, direction, len(order))]
            new_packs[pid] = game["packs"][donor]

        for pid in order:
            game["packs"][pid] = new_packs[pid]

        game["waiting_to_pick"] = set(order)

        if all_packs_empty(game):
            advance_to_next_pack(game)

        socketio.emit("packs_passed", {
            "pack_num": game["pack_num"],
            "phase":    game["phase"],
            "direction": pass_direction(game["pack_num"]) if game["phase"] != "done" else None,
        }, room=game_id)


# ── Entry point ───────────────────────────────────────────────────────────────

def open_browser():
    """Open the browser after a short delay to let the server start."""
    webbrowser.open("http://localhost:5000")

if __name__ == "__main__":
    print("\n" + "="*50)
    print("  MTG Pick-2 Draft Server")
    print("  Opening http://localhost:5000 ...")
    print("="*50 + "\n")
    # Open browser after 1.5s so server is ready
    threading.Timer(1.5, open_browser).start()
    socketio.run(app, debug=True, use_reloader=False, port=5000, host="0.0.0.0")
