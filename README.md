# 🎬 YouTube Downlader — Bot + Mini App

A Telegram bot + Mini App for downloading media from 1000+ sites via yt-dlp.

```
YouTube Downlader/
├── bot.py                  ← Telegram bot (your existing bot, updated)
├── backend/
│   └── server.py           ← FastAPI backend (serves Mini App API)
├── frontend/
│   └── index.html          ← Telegram Mini App UI
├── requirements.txt
├── .env
└── downloads/              ← Temp files (auto-created)
```

---

## Architecture

```
User opens Mini App
       │
       ▼
  [index.html]  ──POST /api/info──►  [server.py FastAPI]
                                            │
                                       yt-dlp fetch info
                                            │
  User picks format ──POST /api/download──► │
                                            │
                                     yt-dlp downloads
                                            │
                                     Sends file via Bot API
                                            │
                                            ▼
                                    File arrives in user's chat
```

---

## Setup

### 1. Install

```bash
pip install -r requirements.txt
sudo apt install ffmpeg   # required for video+audio merge
```

### 2. Configure `.env`

```env
BOT_TOKEN=your_token_from_botfather
MINI_APP_URL=https://yourdomain.com   # HTTPS required
MAX_FILE_SIZE_MB=2000
```

### 3. Register Mini App with BotFather

1. Open [@BotFather](https://t.me/BotFather)
2. `/mybots` → select your bot
3. **Bot Settings** → **Menu Button** → **Configure menu button**
4. Set URL to your `MINI_APP_URL`
5. Set button text: `Open YouTube Downlader`

### 4. Run both processes

**Terminal 1 — API backend:**
```bash
cd backend
python server.py
# Runs on http://localhost:8000
```

**Terminal 2 — Telegram bot:**
```bash
python bot.py
```

---

## Hosting (Production)

### Option A — Same server, Nginx reverse proxy

```nginx
server {
    listen 443 ssl;
    server_name yourdomain.com;

    # Serve frontend
    root /path/to/YouTube Downlader/frontend;
    index index.html;

    # Proxy API calls to FastAPI
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
    }
}
```

Then set `MINI_APP_URL=https://yourdomain.com` in `.env`.

### Option B — Local dev with ngrok

```bash
# Run FastAPI serving frontend at port 8000
cd backend && python server.py

# In another terminal
ngrok http 8000

# Copy the https://xxx.ngrok.io URL into .env MINI_APP_URL
```

> ⚠️ Telegram Mini Apps **require HTTPS**. ngrok gives you this for free during dev.

### Option C — Docker

```bash
docker compose up -d
```

---

## How the Mini App works

| Step | What happens |
|---|---|
| User opens Mini App | `Telegram.WebApp.initDataUnsafe.user.id` gives their chat ID |
| User pastes URL | Frontend calls `POST /api/info` → returns title, thumbnail, formats |
| User picks format | Frontend stores selection |
| User taps Download | Frontend calls `POST /api/download` with `chat_id`, `url`, `format_id` |
| Backend downloads | yt-dlp runs in background thread, updates job progress |
| Frontend polls | `GET /api/job/{id}` every 1 second → shows live progress bar |
| Upload done | Backend sends file to user via Bot API, Mini App shows ✅ |

---

## Mini App Features

- 🔍 Fetch media info with thumbnail preview
- 🎬 Resolution picker (1080p, 720p, etc.) — always merged with best audio
- 🎵 Audio-only download (MP3)
- 📋 Playlist support — browse entries, download one or all
- 📊 Live download + upload progress bar
- 🌐 Supports 1000+ sites
- 📱 Mobile-first design, adapts to Telegram theme
- 📋 Clipboard paste button

---

## Telegram Mini App Notes

- Must be opened from within Telegram (uses `window.Telegram.WebApp`)
- `initDataUnsafe.user.id` gives the chat ID to send files to
- For security, validate `initData` on the server in production
- Mini Apps must be served over HTTPS
