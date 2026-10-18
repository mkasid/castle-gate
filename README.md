# 🏰 Castle Gate

A persuasion adventure where an AI judges your words. The probability *is* the dice roll.

Three souls stand between you and dawn: a **guard**, a **merchant**, a **queen**.
There are no dialogue options and no dice. You type what you say — anything at all —
and [Drex](https://nace.ai/drex), a System 1 decision model, returns the probability
that your words are **sincere and genuinely moving**. Cross the threshold and the
story continues. Fall short and you'd better find better words.

The twist the game is built on: these three have heard every lie ever told.
Bribes, flattery, fake authority and sob-story manipulation consistently score
near zero. Only genuine words open doors — the model turned out to be a sincerity
detector, and the game leans into it.

Win with a line and it gets **burned**: immortalized in the public Hall of Fame,
never usable again. Exact copies *and* close paraphrases are rejected, so every
player has to find their own honest words.

## How it works

- **Frontend** (`frontend/`) — static page: cartoon scenes, animated rain, torch
  flicker, typewriter dialogue, an animated probability meter, and the Hall of Fame.
  No build step; edit `frontend/config.js` to point at your backend.
- **Backend** (`backend/server.py`) — Python, stdlib only, no dependencies. Two jobs:
  1. Proxies each persuasion attempt to the Drex `/v1/systemone` API
     (the API key stays server-side; the browser never sees it).
  2. Runs the Hall of Fame: winning lines are stored in SQLite and burned —
     exact plus fuzzy matching rejects reuse *before* spending API credit.
- One Drex call per attempt (a single `noul` question), sub-second, fractions of
  a cent each.

## Run it locally

Backend (mock mode — simulated judgments, no API key needed):

```bash
cd backend
MOCK_DREX=1 PORT=8080 python3 server.py
```

Backend (live):

```bash
cd backend
DREX_API_KEY=<your-drex-key> PORT=8080 python3 server.py
```

Frontend (serve over http so `fetch` works):

```bash
cd frontend
python3 -m http.server 8000
# open http://localhost:8000  (config.js points at http://localhost:8080 by default)
```

If the backend isn't reachable, the game offers a demo mode with simulated
judgments and a local-only Hall of Fame.

## Deploy it

See [DEPLOY.md](DEPLOY.md) for the full guide: backend on Render (free tier,
Docker, `render.yaml` blueprint included), frontend on Cloudflare Pages or
GitHub Pages, plus custom-domain wiring.

## API

- `GET /api/health` → `{"ok": true}`
- `GET /api/scenes` → scene metadata (title, brief, NPC, threshold)
- `GET /api/hall-of-fame` → winning lines, newest first
- `POST /api/judge` `{"scene": "gate"|"merchant"|"throne", "text": "..."}`
  → `{"probability": 0.83, "threshold": 0.45, "success": true}`
  or `{"burned": true, "reason": "exact"|"similar", "matched": "..."}`
