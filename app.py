"""
MTG Pick-2 Draft Server
- Local: run with VS Code F5, opens browser automatically
- Production: Railway reads PORT env var and runs via Procfile
"""

import subprocess
import sys
import os
import inspect

# ── Auto-install dependencies if missing (local dev only) ────────────────────
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
        os.execv(sys.executable, [sys.executable] + sys.argv)

# Only auto-install when running locally, not on Railway
if not os.environ.get("RAILWAY_ENVIRONMENT"):
    install_dependencies()

# ── Imports ───────────────────────────────────────────────────────────────────
import uuid
import random
import threading
import webbrowser

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room
import requests as req

BASE_DIR = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
os.chdir(BASE_DIR)

app = Flask(__name__, template_folder=TEMPLATE_DIR)
app.secret_key = os.environ.get("SECRET_KEY", "mtg-draft-local-dev-key")

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="gevent" if os.environ.get("RAILWAY_ENVIRONMENT") else "threading",
)

games = {}
SCRYFALL_NAMED = "https://api.scryfall.com/cards/named"


# ── Card fetching ─────────────────────────────────────────────────────────────

def fetch_card(name):
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


def resolve_cards(card_names):
    cards = []
    for name in card_names:
        card = fetch_card(name)
        if card:
            cards.append(card)
        else:
            print(f"  Could not resolve: {name}")
    return cards


# ── Draft logic helpers ───────────────────────────────────────────────────────

def pass_direction(pack_num):
    return "left" if pack_num in (1, 3) else "right"

def next_seat(current, direction, total=4):
    return (current + 1) % total if direction == "left" else (current - 1) % total

def all_packs_empty(game):
    return all(len(p) == 0 for p in game["packs"].values())

def advance_to_next_pack(game):
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

    num_packs = 12
    base_size = max(len(resolved) // num_packs, 2)
    all_pack_list = []
    idx = 0
    for i in range(num_packs):
        all_pack_list.append(resolved[idx: idx + base_size])
        idx += base_size
    for i, card in enumerate(resolved[idx:]):
        all_pack_list[i % num_packs].append(card)

    game_id = str(uuid.uuid4())[:8]
    player_ids = [str(uuid.uuid4())[:8] for _ in range(4)]

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

    join_links = {game["players"][pid]["name"]: f"/draft/{game_id}/{pid}" for pid in player_ids}
    return jsonify({"game_id": game_id, "links": join_links, "player_ids": player_ids})


@app.route("/draft/<game_id>/<player_id>")
def draft_view(game_id, player_id):
    game = games.get(game_id)
    if not game or player_id not in game["players"]:
        return "Game not found", 404
    return render_template("draft.html", game_id=game_id, player_id=player_id,
                           player_name=game["players"][player_id]["name"])


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
            pid: {"name": info["name"], "pool_size": len(info["pool"]),
                  "waiting": pid in game.get("waiting_to_pick", set())}
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
    game_id, player_id, card_id = data["game_id"], data["player_id"], data["card_id"]
    game = games.get(game_id)
    if not game or game["phase"] != "drafting":
        return
    if player_id not in game.get("waiting_to_pick", set()):
        emit("error", {"msg": "Not your turn to pick"})
        return
    pending = game["pending_picks"][player_id]
    pack = game["packs"].get(player_id, [])
    card = next((c for c in pack if c["id"] == card_id), None)
    if not card:
        emit("error", {"msg": "Card not in your pack"})
        return
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
    game_id, player_id = data["game_id"], data["player_id"]
    game = games.get(game_id)
    if not game or game["phase"] != "drafting":
        return
    pending = game["pending_picks"].get(player_id, [])
    pack = game["packs"].get(player_id, [])
    required = min(2, len(pack))
    if len(pending) < required:
        emit("error", {"msg": f"Must select {required} card(s) before confirming"})
        return
    if len(pending) == 0:
        emit("error", {"msg": "No cards selected"})
        return
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
    if len(game["waiting_to_pick"]) == 0:
        direction = pass_direction(game["pack_num"])
        order = game["player_order"]
        new_packs = {pid: game["packs"][order[next_seat(i, direction, len(order))]]
                     for i, pid in enumerate(order)}
        for pid in order:
            game["packs"][pid] = new_packs[pid]
        game["waiting_to_pick"] = set(order)
        if all_packs_empty(game):
            advance_to_next_pack(game)
        socketio.emit("packs_passed", {
            "pack_num": game["pack_num"],
            "phase": game["phase"],
            "direction": pass_direction(game["pack_num"]) if game["phase"] != "done" else None,
        }, room=game_id)


# ── Entry point ───────────────────────────────────────────────────────────────

def open_browser():
    webbrowser.open("http://localhost:5000")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    is_local = not os.environ.get("RAILWAY_ENVIRONMENT")

    print("\n" + "="*50)
    print("  MTG Pick-2 Draft Server")
    if is_local:
        print(f"  Opening http://localhost:{port} ...")
        threading.Timer(1.5, open_browser).start()
    print("="*50 + "\n")

    socketio.run(app, debug=is_local, use_reloader=False, port=port, host="0.0.0.0")
