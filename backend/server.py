#!/usr/bin/env python3
"""
Castle Gate — backend server (Python stdlib only, no pip dependencies).

Two jobs:
  1. Proxy persuasion judgments to the Drex decision API (the API key
     stays server-side; the browser never sees it).
  2. Hall of Fame: burn winning lines so no other player can reuse them.

Env vars:
  DREX_API_KEY     Drex API key (required for live judgments)
  PORT             listen port (default 8080; Render/Railway/Fly set this)
  DB_PATH          sqlite file (default ./halloffame.db)
  FRONTEND_ORIGIN  CORS origin (default "*")
  MOCK_DREX        "1" = return canned probabilities, no Drex call (local testing)

Endpoints:
  GET  /api/health       -> {"ok": true}
  GET  /api/scenes       -> scene metadata (title, brief, threshold)
  GET  /api/hall-of-fame -> latest winning lines
  POST /api/judge        {"scene": "gate", "text": "..."}
                         -> {"probability": 0.84, "success": true, ...}
                         Burned lines are rejected BEFORE spending API credit.
"""

import hashlib
import json
import os
import re
import sqlite3
import threading
import urllib.request
import urllib.error
from urllib.parse import urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DREX_URL = "https://console.nace.ai/v1/systemone"
DREX_MODEL = "drex-v1.5"
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "halloffame.db"))
PORT = int(os.environ.get("PORT", "8080"))
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "*")
MOCK_DREX = os.environ.get("MOCK_DREX") == "1"

# Words that carry no meaning for duplicate detection.
STOPWORDS = set(
    "a an the and or but of to in on at for with please just really very so my me i you your he she it we they".split()
)

# The game's question: Drex proved to be a reliable sincerity detector —
# sincere pleas score ~0.5-0.8, manipulative/deceptive lines ~0.03-0.16,
# consistently across scenes (verified live 2026-10-18). The fiction:
# these NPCs have heard every trick; only genuine words move them.
SINCERITY_QUESTION = (
    "Is the traveler's plea sincere and genuinely moving, "
    "rather than manipulative or deceptive?"
)

SCENES = {
    "gate": {
        "title": "The Castle Gate",
        "threshold": 0.45,
        "npc": "Brom, a sleepy but dutiful castle guard",
        "brief": (
            "Midnight. Rain hammers the cobblestones. Brom the guard stands before the castle "
            "gate, spear crossed. No one enters after dark — and Brom has heard every lie ever told."
        ),
        "question": SINCERITY_QUESTION,
    },
    "merchant": {
        "title": "The Merchant's Stall",
        "threshold": 0.45,
        "npc": "Sarella, a sly merchant with a magnificent mustache",
        "question": SINCERITY_QUESTION,
        "brief": (
            "Dusk in the market square. Sarella has survived twenty years of hagglers and con "
            "artists. The traveler needs the silver amulet but hasn't the coin — tricks won't work."
        ),
    },
    "throne": {
        "title": "The Throne Room",
        "threshold": 0.45,
        "npc": "Queen Maren, stern ruler of the realm",
        "question": SINCERITY_QUESTION,
        "brief": (
            "Torchlight flickers in the throne room. Queen Maren has been flattered by experts. "
            "The traveler begs soldiers to defend the village — only plain, honest words stand a chance."
        ),
    },
}

# ---------------------------------------------------------------- DB ---

_db_lock = threading.Lock()


def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS burned_lines ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " scene TEXT NOT NULL,"
        " text TEXT NOT NULL,"
        " norm TEXT NOT NULL,"
        " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_burned_scene ON burned_lines(scene)")
    return conn


_DB = db()


def normalize(text):
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def content_words(norm):
    return set(w for w in norm.split() if w not in STOPWORDS)


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def check_burn(scene, text):
    """Return (kind, matched_text) if this line is already burned, else None.
    kind is 'exact' or 'similar'."""
    norm = normalize(text)
    if not norm:
        return None
    words = content_words(norm)
    with _db_lock:
        rows = _DB.execute(
            "SELECT text, norm FROM burned_lines WHERE scene = ?", (scene,)
        ).fetchall()
    for orig, onorm in rows:
        if norm == onorm:
            return ("exact", orig)
        w2 = content_words(onorm)
        # Fuzzy: only for meaty lines, so short distinct attempts never collide.
        if len(words) >= 5 and len(w2) >= 5 and jaccard(words, w2) >= 0.70:
            return ("similar", orig)
    return None


def burn_line(scene, text):
    with _db_lock:
        _DB.execute(
            "INSERT INTO burned_lines (scene, text, norm) VALUES (?, ?, ?)",
            (scene, text.strip(), normalize(text)),
        )
        _DB.commit()


def hall_of_fame(limit=50):
    with _db_lock:
        rows = _DB.execute(
            "SELECT scene, text, created_at FROM burned_lines "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [{"scene": s, "text": t, "at": a} for s, t, a in rows]


# --------------------------------------------------------------- Drex ---

def drex_judge(scene_id, text):
    """Return P(yes) for the scene's noul question. Mock mode for local testing."""
    scene = SCENES[scene_id]
    if MOCK_DREX:
        # Deterministic-ish demo probability from the text itself.
        h = int(hashlib.sha256(text.encode()).hexdigest(), 16)
        words = content_words(normalize(text))
        base = 0.25 + min(len(words) / 30.0, 1.0) * 0.45
        jitter = (h % 100) / 100.0 * 0.2
        return round(min(max(base + jitter, 0.02), 0.98), 3)

    api_key = os.environ.get("DREX_API_KEY")
    if not api_key:
        raise RuntimeError("DREX_API_KEY is not set")

    state = (
        f"Scene: {scene['brief']}\n"
        f"Character: {scene['npc']}\n"
        f'The traveler says: "{text.strip()}"'
    )
    body = {
        "model": DREX_MODEL,
        "state": state,
        "questions": {
            # NOTE: noul questions REQUIRE the "instructions" field (not "question").
            "persuaded": {"type": "noul", "instructions": scene["question"]},
        },
    }
    req = urllib.request.Request(
        DREX_URL,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:500]
        raise RuntimeError(f"Drex API HTTP {e.code}: {detail}")
    return _extract_probability(payload, "persuaded")


def _as_number(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        # Live Drex shape: {"type": "noul", "noul": 0.3074}
        for k in ("noul", "probability", "p", "confidence", "score", "value"):
            if isinstance(v.get(k), (int, float)) and not isinstance(v.get(k), bool):
                return float(v[k])
        if "answer" in v:  # boolean answer + confidence
            conf = float(v.get("confidence", 0.5))
            return conf if v["answer"] in (True, "yes", "Yes", "YES") else 1.0 - conf
    return None


def _extract_probability(payload, qname):
    """Tolerantly pull P(yes) out of the Drex response (shape may vary)."""
    candidates = []
    if isinstance(payload, dict):
        for key in ("answers", "results", "decisions", "data"):
            node = payload.get(key)
            if isinstance(node, dict) and qname in node:
                candidates.append(node[qname])
        if qname in payload:
            candidates.append(payload[qname])
        # last resort: first dict value that looks like an answer
        for v in payload.values():
            if isinstance(v, dict):
                candidates.append(v)
    for c in candidates:
        n = _as_number(c)
        if n is not None and 0.0 <= n <= 1.0:
            return round(n, 4)
    raise RuntimeError(f"Could not parse probability from Drex response: {str(payload)[:300]}")


# ---------------------------------------------------------------- HTTP ---

class Handler(BaseHTTPRequestHandler):
    server_version = "CastleGate/1.0"

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", FRONTEND_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json({"ok": True, "mock": MOCK_DREX})
        elif path == "/api/scenes":
            self._json(
                {
                    sid: {
                        "title": s["title"],
                        "brief": s["brief"],
                        "npc": s["npc"],
                        "threshold": s["threshold"],
                    }
                    for sid, s in SCENES.items()
                }
            )
        elif path == "/api/hall-of-fame":
            self._json({"winners": hall_of_fame()})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/judge":
            self._json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode() or "{}")
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "invalid JSON body"}, 400)
            return

        scene_id = data.get("scene")
        text = (data.get("text") or "").strip()
        if scene_id not in SCENES:
            self._json({"error": f"unknown scene '{scene_id}'"}, 400)
            return
        if not text:
            self._json({"error": "text is required"}, 400)
            return
        if len(text) > 500:
            self._json({"error": "text must be 500 characters or fewer"}, 400)
            return

        # Burned lines are rejected BEFORE spending Drex API credit.
        hit = check_burn(scene_id, text)
        if hit:
            kind, matched = hit
            self._json(
                {
                    "burned": True,
                    "reason": kind,  # "exact" or "similar"
                    "matched": matched,
                    "message": "That line already won — be more original!",
                }
            )
            return

        try:
            p = drex_judge(scene_id, text)
        except RuntimeError as e:
            self._json({"error": str(e)}, 502)
            return

        scene = SCENES[scene_id]
        success = p >= scene["threshold"]
        if success:
            burn_line(scene_id, text)  # a winning line can never be used again

        self._json(
            {
                "burned": False,
                "probability": p,
                "threshold": scene["threshold"],
                "success": success,
            }
        )

    def log_message(self, fmt, *args):  # quieter logs
        pass


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Castle Gate backend on :{PORT} (mock_drex={MOCK_DREX})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
