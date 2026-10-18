# Deploying Castle Gate (Path 1)

You need two free accounts: one for the **backend** (Python server) and one for the
**frontend** (static files). Total clicking: ~15 minutes.

## What you need first

- A GitHub account (to hold the code)
- A Render account (backend) — sign up with your email; free tier is fine
- A Cloudflare account (frontend) — free
- Your Drex API key (from console.nace.ai)

## 1. Put the code on GitHub

```bash
cd drex-castle-gate
git init
git add .
git commit -m "Castle Gate game"
# create an empty repo on github.com, then:
git remote add origin https://github.com/<you>/castle-gate.git
git push -u origin main
```

## 2. Deploy the backend on Render

1. Render dashboard → **New +** → **Blueprint** → connect your `castle-gate` repo.
   (Render reads `render.yaml` and creates the web service automatically.)
   - If you prefer manual: New → Web Service → connect repo → Runtime: **Docker**,
     Dockerfile path: `./backend/Dockerfile`, plan: **Free**.
2. In the service's **Environment** tab, add:
   - `DREX_API_KEY` = your Drex key (paste the value; never commit it)
   - `FRONTEND_ORIGIN` = `*` (already the default; tighten to your frontend URL later)
3. **Deploy.** Note your backend URL, e.g. `https://castle-gate-backend.onrender.com`.
4. Sanity check: open `https://<your-backend>/api/health` — you should see `{"ok": true}`.

Notes:
- Render's free tier sleeps when idle: the first request after a while takes ~50s
  to wake up. That's fine for a demo; the game shows a "Judging…" state meanwhile.
- The Hall of Fame SQLite file lives on the container's ephemeral disk: it resets
  if Render rebuilds/redeploys. For a demo that's fine. (Upgrade path: add a
  Render Disk mounted at `/app/data` and set `DB_PATH=/app/data/halloffame.db`.)

## 3. Deploy the frontend on Cloudflare Pages

1. Edit `frontend/config.js` — set `window.CASTLE_GATE_BACKEND` to your Render URL
   (no trailing slash). Commit and push.
2. Cloudflare dashboard → **Workers & Pages** → **Create** → **Pages** →
   **Connect to Git** → select your `castle-gate` repo.
3. Build settings: Framework preset **None**, **Root directory** = `frontend`,
   Build command empty, Output directory empty (files are served as-is).
4. **Deploy.** You get a `https://castle-gate.pages.dev` URL — that's your LinkedIn link.

(Alternative: GitHub Pages — repo Settings → Pages → deploy from the `/frontend` folder.)

## 4. Play locally (no deploy needed)

Backend (mock mode = no Drex calls, for testing the game flow):

```bash
cd backend
MOCK_DREX=1 PORT=8080 python3 server.py
```

Backend (live, needs your key):

```bash
cd backend
DREX_API_KEY=<your-key> PORT=8080 python3 server.py
```

Frontend: serve the folder (don't just double-click the file — `fetch` needs http):

```bash
cd frontend
python3 -m http.server 8000
# open http://localhost:8000, with config.js pointing at http://localhost:8080
```

If the backend isn't reachable, the game offers **Demo Mode**: judgments are
simulated in-browser and the Hall of Fame is local-only. Good for playtesting
the feel without spending API credit.
