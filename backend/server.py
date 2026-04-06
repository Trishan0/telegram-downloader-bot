"""
YouTube Downlader Mini App — FastAPI Backend
Handles info fetching, download jobs, and sending files via the Telegram Bot API.
"""

import os
import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import yt_dlp
import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[1] / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip().strip('"').strip("'")
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "./downloads"))
DOWNLOAD_DIR.mkdir(exist_ok=True)
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "2000"))

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ── Job store ──────────────────────────────────────────────────────────────────
# job_id -> { status, progress, speed, eta, error, chat_id, title }
jobs: dict[str, dict] = {}


# ── Models ─────────────────────────────────────────────────────────────────────

class InfoRequest(BaseModel):
    url: str

class DownloadRequest(BaseModel):
    url: str
    format_id: str        # e.g. "bestvideo+bestaudio/best" or specific fmt_id
    media_type: str       # "video" or "audio"
    chat_id: int
    title: str = ""

class PlaylistRequest(BaseModel):
    url: str


# ── Helpers ────────────────────────────────────────────────────────────────────

def sizeof_fmt(num_bytes: float) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"


def get_base_opts() -> dict:
    from pathlib import Path
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    # Use cookies.txt if available
    cookies_file = Path("/app/cookies.txt")
    if cookies_file.exists():
        opts["cookiefile"] = str(cookies_file)
        logger.info("Using cookies.txt for authentication")
    else:
        logger.warning("cookies.txt not found — downloads may be limited")
    return opts


def _extract_info(url: str, extra: dict) -> dict | None:
    opts = {**get_base_opts(), **extra}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        logger.error(f"extract_info error: {e}")
        return None


def _parse_formats(formats: list[dict]) -> dict:
    video_fmts = []
    audio_fmts = []
    seen_v, seen_a = set(), set()

    for f in formats:
        vcodec = f.get("vcodec", "none")
        acodec = f.get("acodec", "none")
        height = f.get("height")
        ext = f.get("ext", "?")
        abr = f.get("abr") or 0
        filesize = f.get("filesize") or f.get("filesize_approx") or 0
        fmt_id = f.get("format_id", "")

        if vcodec != "none" and height:
            label = f"{height}p"
            if label not in seen_v:
                seen_v.add(label)
                video_fmts.append({
                    "format_id": fmt_id,
                    "label": label,
                    "height": height,
                    "ext": ext,
                    "filesize": filesize,
                    "filesize_str": sizeof_fmt(filesize) if filesize else "?",
                    "dl_format": f"{fmt_id}+bestaudio/best",
                    "media_type": "video",
                })

        elif vcodec == "none" and acodec != "none":
            label = f"{ext.upper()} ~{int(abr)}kbps" if abr else ext.upper()
            if label not in seen_a:
                seen_a.add(label)
                audio_fmts.append({
                    "format_id": fmt_id,
                    "label": label,
                    "ext": ext,
                    "abr": abr,
                    "filesize": filesize,
                    "filesize_str": sizeof_fmt(filesize) if filesize else "?",
                    "dl_format": fmt_id,
                    "media_type": "audio",
                })

    video_fmts.sort(key=lambda x: x["height"], reverse=True)
    audio_fmts.sort(key=lambda x: x["abr"], reverse=True)
    return {"video": video_fmts[:10], "audio": audio_fmts[:6]}


async def send_file_to_telegram(chat_id: int, filepath: Path, media_type: str, title: str):
    """Upload file to Telegram via Bot API."""
    async with httpx.AsyncClient(timeout=600) as client:
        with open(filepath, "rb") as f:
            data = {"chat_id": str(chat_id)}
            if media_type == "audio":
                files = {"audio": (filepath.name, f, "audio/mpeg")}
                url = f"{TELEGRAM_API}/sendAudio"
            else:
                files = {"video": (filepath.name, f, "video/mp4")}
                url = f"{TELEGRAM_API}/sendVideo"
                data["supports_streaming"] = "true"

            resp = await client.post(url, data=data, files=files)
            if not resp.is_success:
                # Fallback: send as document
                f.seek(0)
                files = {"document": (filepath.name, f, "application/octet-stream")}
                url = f"{TELEGRAM_API}/sendDocument"
                resp = await client.post(url, data={"chat_id": str(chat_id)}, files=files)
                resp.raise_for_status()


async def send_text_to_telegram(chat_id: int, text: str):
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        })


# ── Background download job ────────────────────────────────────────────────────

async def run_download_job(job_id: str, url: str, fmt: str, media_type: str, chat_id: int, title: str):
    jobs[job_id].update({"status": "downloading", "progress": 0})
    filepath: Path | None = None

    def progress_hook(d: dict):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            speed = d.get("speed") or 0
            eta = d.get("eta") or 0
            pct = int(downloaded / total * 100) if total else 0
            jobs[job_id].update({
                "progress": pct,
                "speed": sizeof_fmt(int(speed)) + "/s" if speed else "—",
                "eta": f"{eta}s" if eta else "—",
                "downloaded": sizeof_fmt(downloaded),
                "total": sizeof_fmt(total) if total else "?",
            })

    # Fallback format chain
    format_chain = [fmt]
    if media_type == "video":
        if fmt == "bestvideo+bestaudio/best":
            format_chain.extend(["best[ext=mp4]", "best[ext=webm]", "best"])
        elif "+" in fmt:
            format_chain.extend(["best[ext=mp4]", "best"])
    elif media_type == "audio":
        if fmt == "bestaudio/best":
            format_chain.extend(["best[ext=m4a]", "best[ext=webm]", "best"])

    out_tmpl = str(DOWNLOAD_DIR / f"{chat_id}_{job_id}_%(title).60s.%(ext)s")

    try:
        def _dl():
            nonlocal filepath
            last_error = None
            
            for attempt_fmt in format_chain:
                try:
                    ydl_opts = {
                        **get_base_opts(),
                        "format": attempt_fmt,
                        "outtmpl": out_tmpl,
                        "progress_hooks": [progress_hook] if attempt_fmt == fmt else [],
                        "merge_output_format": "mp4" if media_type == "video" else None,
                        "postprocessors": [],
                    }

                    if media_type == "audio":
                        ydl_opts["postprocessors"].append({
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": "192",
                        })

                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        raw = ydl.prepare_filename(info)
                        p = Path(raw)
                        for ext in ["mp4", "mkv", "webm", "mp3", "m4a", "opus", "ogg"]:
                            c = p.with_suffix(f".{ext}")
                            if c.exists():
                                filepath = c
                                if attempt_fmt != fmt:
                                    logger.info(f"Download succeeded with fallback format: {attempt_fmt}")
                                return
                        if p.exists():
                            filepath = p
                            if attempt_fmt != fmt:
                                logger.info(f"Download succeeded with fallback format: {attempt_fmt}")
                            return
                except Exception as e:
                    last_error = e
                    logger.debug(f"Format {attempt_fmt} failed: {e}")
                    continue
            
            if last_error:
                raise last_error

        await asyncio.to_thread(_dl)

        if not filepath or not filepath.exists():
            raise RuntimeError("Downloaded file not found on disk.")

        size_mb = filepath.stat().st_size / (1024 * 1024)
        if size_mb > MAX_FILE_SIZE_MB:
            raise RuntimeError(f"File too large ({size_mb:.0f} MB). Limit: {MAX_FILE_SIZE_MB} MB.")

        jobs[job_id].update({"status": "uploading", "progress": 100})
        await send_file_to_telegram(chat_id, filepath, media_type, title)
        await send_text_to_telegram(chat_id, f"✅ *{title or filepath.stem}*\nDownloaded via YouTube Downlader Mini App")
        jobs[job_id].update({"status": "done", "progress": 100})

    except Exception as e:
        logger.exception(f"Job {job_id} failed: {e}")
        jobs[job_id].update({"status": "error", "error": str(e)[:300]})
        await send_text_to_telegram(chat_id, f"❌ Download failed: {str(e)[:200]}")
    finally:
        try:
            if filepath and filepath.exists():
                filepath.unlink()
        except Exception:
            pass


# ── FastAPI app ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("YouTube Downlader API server started")
    yield
    logger.info("YouTube Downlader API server stopped")

app = FastAPI(title="YouTube Downlader API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok", "bot_configured": bool(BOT_TOKEN)}


@app.post("/api/info")
async def get_info(req: InfoRequest):
    info = await asyncio.to_thread(_extract_info, req.url, {"noplaylist": True})
    if not info:
        raise HTTPException(status_code=422, detail="Could not fetch media info. Check the URL.")

    formats = info.get("formats", [])
    parsed = _parse_formats(formats)
    duration = info.get("duration")

    return {
        "title": info.get("title", "Unknown"),
        "thumbnail": info.get("thumbnail"),
        "uploader": info.get("uploader"),
        "duration": duration,
        "duration_str": f"{int(duration // 60)}:{int(duration % 60):02d}" if duration else None,
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "webpage_url": info.get("webpage_url", req.url),
        "extractor": info.get("extractor_key", ""),
        "formats": parsed,
        # Quick presets
        "presets": [
            {"label": "⚡ Best Quality", "dl_format": "bestvideo+bestaudio/best", "media_type": "video"},
            {"label": "🎵 Audio Only (MP3)", "dl_format": "bestaudio/best", "media_type": "audio"},
        ],
    }


@app.post("/api/playlist")
async def get_playlist(req: PlaylistRequest):
    opts = {**get_base_opts(), "noplaylist": False, "extract_flat": True}
    info = await asyncio.to_thread(_extract_info, req.url, {"noplaylist": False, "extract_flat": True})
    if not info:
        raise HTTPException(status_code=422, detail="Could not fetch playlist.")

    entries = [e for e in (info.get("entries") or []) if e]
    return {
        "title": info.get("title", "Playlist"),
        "count": len(entries),
        "entries": [
            {
                "index": i,
                "title": e.get("title", f"Entry {i+1}"),
                "url": e.get("url") or e.get("webpage_url", ""),
                "thumbnail": e.get("thumbnail"),
                "duration": e.get("duration"),
            }
            for i, e in enumerate(entries)
        ],
    }


@app.post("/api/download")
async def start_download(req: DownloadRequest, background_tasks: BackgroundTasks):
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="Bot token not configured.")

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        "status": "queued",
        "progress": 0,
        "speed": "—",
        "eta": "—",
        "downloaded": "0 B",
        "total": "?",
        "error": None,
        "chat_id": req.chat_id,
        "title": req.title,
    }

    background_tasks.add_task(
        run_download_job,
        job_id, req.url, req.format_id, req.media_type, req.chat_id, req.title
    )
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/job/{job_id}")
async def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


# Serve frontend static files
frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "9000"))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=True)
