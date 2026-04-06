import os
import asyncio
import logging
import re
from pathlib import Path
from dotenv import load_dotenv

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    WebAppInfo,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode, ChatAction
import yt_dlp

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

load_dotenv(Path(__file__).with_name(".env"))

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip().strip('"').strip("'")
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "./downloads"))
DOWNLOAD_DIR.mkdir(exist_ok=True)
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "2000"))

# URL where your Mini App is hosted (set in .env)
# e.g. https://yourdomain.com  or  https://yourapp.ngrok.io
MINI_APP_URL = (os.getenv("MINI_APP_URL") or "").strip().strip('"').strip("'")

user_sessions: dict[int, dict] = {}


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def sizeof_fmt(num_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name)[:100]


def get_ydl_base_opts() -> dict:
    from pathlib import Path
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    # Use cookies.txt if available
    cookies_file = Path("/app/cookies.txt")
    if cookies_file.exists():
        opts["cookiefile"] = str(cookies_file)
        logger.info("Using cookies.txt for authentication")
    else:
        logger.warning("cookies.txt not found — downloads may be limited")
    return opts


def extract_info_safe(url: str, opts: dict) -> dict | None:
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        logger.error(f"yt-dlp extract error: {e}")
        return None


def build_format_keyboard(formats: list[dict], info_id: str) -> InlineKeyboardMarkup:
    buttons = []
    buttons.append([InlineKeyboardButton("⚡ Best Quality (auto)", callback_data=f"dl|{info_id}|bestvideo+bestaudio/best|video")])
    buttons.append([InlineKeyboardButton("🎵 Audio Only (best)", callback_data=f"dl|{info_id}|bestaudio/best|audio")])

    seen = set()
    video_formats = []
    for f in formats:
        if f.get("vcodec") == "none":
            continue
        height = f.get("height")
        if not height:
            continue
        label = f"{height}p"
        if label in seen:
            continue
        seen.add(label)
        video_formats.append((height, label, f["format_id"]))

    video_formats.sort(key=lambda x: x[0], reverse=True)

    for height, label, fmt_id in video_formats[:8]:
        dl_fmt = f"{fmt_id}+bestaudio/best"
        buttons.append([
            InlineKeyboardButton(
                f"🎬 {label} + Best Audio",
                callback_data=f"dl|{info_id}|{dl_fmt}|video"
            )
        ])

    audio_formats = []
    seen_audio = set()
    for f in formats:
        if f.get("vcodec") != "none":
            continue
        ext = f.get("ext", "?")
        abr = f.get("abr") or 0
        label = f"{ext.upper()} ~{int(abr)}kbps" if abr else ext.upper()
        if label in seen_audio:
            continue
        seen_audio.add(label)
        audio_formats.append((abr, label, f["format_id"]))

    audio_formats.sort(key=lambda x: x[0], reverse=True)
    for _, label, fmt_id in audio_formats[:4]:
        buttons.append([
            InlineKeyboardButton(
                f"🎵 {label}",
                callback_data=f"dl|{info_id}|{fmt_id}|audio"
            )
        ])

    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(buttons)


def get_playlist_info(url: str) -> dict | None:
    opts = {
        **get_ydl_base_opts(),
        "noplaylist": False,
        "extract_flat": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        logger.error(f"Playlist extract error: {e}")
        return None


# ─────────────────────────────────────────────
#  Download with progress
# ─────────────────────────────────────────────

class ProgressHook:
    def __init__(self, loop, chat_id, message_id, bot, label="Downloading"):
        self.loop = loop
        self.chat_id = chat_id
        self.message_id = message_id
        self.bot = bot
        self.label = label
        self.last_percent = -1

    def __call__(self, d: dict):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            speed = d.get("speed") or 0
            eta = d.get("eta") or 0

            if total:
                percent = int(downloaded / total * 100)
                if percent != self.last_percent and (percent % 5 == 0):
                    self.last_percent = percent
                    bar = "█" * (percent // 5) + "░" * (20 - percent // 5)
                    text = (
                        f"⬇️ *{self.label}*\n"
                        f"`[{bar}]` {percent}%\n"
                        f"📦 {sizeof_fmt(downloaded)} / {sizeof_fmt(total)}\n"
                        f"🚀 {sizeof_fmt(int(speed))}/s  ⏱ {eta}s"
                    )
                    asyncio.run_coroutine_threadsafe(
                        self.bot.edit_message_text(
                            chat_id=self.chat_id,
                            message_id=self.message_id,
                            text=text,
                            parse_mode=ParseMode.MARKDOWN,
                        ),
                        self.loop,
                    )


async def download_media(url, fmt, media_type, chat_id, status_msg, context):
    loop = asyncio.get_event_loop()
    hook = ProgressHook(loop, chat_id, status_msg.message_id, context.bot)
    out_template = str(DOWNLOAD_DIR / f"{chat_id}_%(title).80s.%(ext)s")

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

    downloaded_file = None

    def _run_download():
        nonlocal downloaded_file
        last_error = None
        
        for attempt_fmt in format_chain:
            try:
                ydl_opts = {
                    **get_ydl_base_opts(),
                    "format": attempt_fmt,
                    "outtmpl": out_template,
                    "progress_hooks": [hook] if attempt_fmt == fmt else [],
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
                    filename = ydl.prepare_filename(info)
                    p = Path(filename)
                    for ext in ["mp4", "mkv", "webm", "mp3", "m4a", "opus", "ogg"]:
                        candidate = p.with_suffix(f".{ext}")
                        if candidate.exists():
                            downloaded_file = candidate
                            if attempt_fmt != fmt:
                                logger.info(f"Download succeeded with fallback format: {attempt_fmt}")
                            return
                    if p.exists():
                        downloaded_file = p
                        if attempt_fmt != fmt:
                            logger.info(f"Download succeeded with fallback format: {attempt_fmt}")
                        return
            except Exception as e:
                last_error = e
                logger.debug(f"Format {attempt_fmt} failed: {e}")
                continue
        
        if last_error:
            raise last_error

    await asyncio.to_thread(_run_download)
    return downloaded_file


# ─────────────────────────────────────────────
#  Main keyboard with Mini App button
# ─────────────────────────────────────────────

def get_main_keyboard() -> ReplyKeyboardMarkup | None:
    if not MINI_APP_URL:
        return None
    keyboard = [[
        KeyboardButton(
            "🚀 Open YouTube Downlader App",
            web_app=WebAppInfo(url=MINI_APP_URL)
        )
    ]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)


# ─────────────────────────────────────────────
#  Command Handlers
# ─────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = get_main_keyboard()

    mini_app_line = (
        f"\n\n🚀 *[Open Mini App]({MINI_APP_URL})* — Full UI with format picker & progress"
        if MINI_APP_URL else ""
    )

    text = (
        "👋 *Welcome to YouTube Downlader Bot!*\n\n"
        "Download videos, audio, and playlists from *1000+ sites* powered by yt-dlp.\n\n"
        "📌 *How to use:*\n"
        "• Just send me any media URL\n"
        "• Pick your format — resolution, audio-only, etc.\n"
        "• I'll download and send it here\n"
        f"{mini_app_line}\n\n"
        "📋 *Commands:*\n"
        "/start — This message\n"
        "/help — Detailed help\n"
        "/playlist `<url>` — Playlist mode\n"
        "/audio `<url>` — Best audio directly\n"
        "/info `<url>` — Media info\n"
        "/cancel — Cancel download"
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
        reply_markup=kb,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *YouTube Downlader Bot — Help*\n\n"
        "*Basic Usage:*\n"
        "Send any supported URL → choose format → receive file.\n\n"
        "*Format Selection:*\n"
        "• ⚡ Best Quality — auto best video+audio\n"
        "• 🎬 720p/1080p + Best Audio — specific res merged with best audio\n"
        "• 🎵 Audio Only → MP3 192kbps\n\n"
        "*Commands:*\n"
        "`/audio <url>` — Skip picker, download best audio\n"
        "`/playlist <url>` — Browse & download playlist entries\n"
        "`/info <url>` — Show title, duration, formats\n"
        "`/cancel` — Cancel ongoing download\n\n"
        "*Supported:* YouTube, Instagram, TikTok, Twitter/X, Reddit, Vimeo, Twitch, SoundCloud, Bilibili, and 1000+ more."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/info <url>`", parse_mode=ParseMode.MARKDOWN)
        return

    url = context.args[0]
    msg = await update.message.reply_text("🔍 Fetching info...")
    opts = {**get_ydl_base_opts(), "noplaylist": True}
    info = await asyncio.to_thread(extract_info_safe, url, opts)

    if not info:
        await msg.edit_text("❌ Could not fetch info. Check the URL or try again.")
        return

    title = info.get("title", "Unknown")
    uploader = info.get("uploader", "Unknown")
    duration = info.get("duration")
    view_count = info.get("view_count")
    formats = info.get("formats", [])

    dur_str = f"{int(duration // 60)}m {int(duration % 60)}s" if duration else "Unknown"
    views_str = f"{view_count:,}" if view_count else "Unknown"
    heights = sorted(set(
        f["height"] for f in formats if f.get("height") and f.get("vcodec") != "none"
    ), reverse=True)
    res_str = ", ".join(f"{h}p" for h in heights[:6]) or "N/A"

    await msg.edit_text(
        f"📹 *{title}*\n\n"
        f"👤 {uploader}\n"
        f"⏱ {dur_str}  👁 {views_str}\n"
        f"🎬 Resolutions: {res_str}\n\n"
        "Send the URL (without /info) to download.",
        parse_mode=ParseMode.MARKDOWN
    )


async def audio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/audio <url>`", parse_mode=ParseMode.MARKDOWN)
        return
    url = context.args[0]
    await process_download(update, context, url, "bestaudio/best", "audio")


async def playlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/playlist <url>`", parse_mode=ParseMode.MARKDOWN)
        return

    url = context.args[0]
    user_id = update.effective_user.id
    msg = await update.message.reply_text("🔍 Fetching playlist info...")

    info = await asyncio.to_thread(get_playlist_info, url)
    if not info:
        await msg.edit_text("❌ Could not fetch playlist. Check the URL.")
        return

    entries = [e for e in (info.get("entries") or []) if e]
    if not entries:
        await msg.edit_text("❌ No entries found.")
        return

    playlist_title = info.get("title", "Playlist")
    user_sessions[user_id] = {
        "type": "playlist",
        "url": url,
        "entries": entries,
        "playlist_title": playlist_title,
        "page": 0,
    }

    await show_playlist_page(update, context, msg, user_id)


async def show_playlist_page(update, context, msg, user_id: int, page: int = 0):
    session = user_sessions.get(user_id)
    if not session:
        return

    entries = session["entries"]
    playlist_title = session["playlist_title"]
    per_page = 8
    total = len(entries)
    start = page * per_page
    end = min(start + per_page, total)

    buttons = []
    for i, entry in enumerate(entries[start:end], start=start):
        title = (entry.get("title") or f"Entry {i+1}")[:40]
        buttons.append([InlineKeyboardButton(f"{i+1}. {title}", callback_data=f"plentry|{i}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"plpage|{page-1}"))
    if end < total:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"plpage|{page+1}"))
    if nav:
        buttons.append(nav)

    buttons.append([
        InlineKeyboardButton("⬇️ Download All (Best)", callback_data="plall|bestvideo+bestaudio/best|video"),
        InlineKeyboardButton("🎵 All Audio", callback_data="plall|bestaudio/best|audio"),
    ])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])

    await msg.edit_text(
        f"📋 *{playlist_title}*\n{total} videos — showing {start+1}–{end}\n\nPick a video:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    session["page"] = page


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_sessions:
        user_sessions[user_id]["cancelled"] = True
        user_sessions.pop(user_id, None)
    await update.message.reply_text("✅ Cancelled.")


# ─────────────────────────────────────────────
#  URL Message Handler
# ─────────────────────────────────────────────

async def url_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    user_id = update.effective_user.id

    msg = await update.message.reply_text("🔍 Fetching media info...")

    opts = {**get_ydl_base_opts(), "noplaylist": True}
    info = await asyncio.to_thread(extract_info_safe, url, opts)

    if not info:
        await msg.edit_text(
            "❌ Could not fetch this URL.\n\n"
            "• Check the URL is correct\n"
            "• Some sites need login/cookies\n"
            "• Try `/info <url>` for details",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    title = info.get("title", "Unknown")
    duration = info.get("duration")
    dur_str = f"⏱ {int(duration // 60)}m {int(duration % 60)}s" if duration else ""
    formats = info.get("formats", [])

    info_id = str(user_id)
    user_sessions[user_id] = {
        "type": "single",
        "url": url,
        "info": info,
        "info_id": info_id,
        "cancelled": False,
    }

    keyboard = build_format_keyboard(formats, info_id)

    # Add Mini App button if configured
    if MINI_APP_URL:
        new_buttons = [[
            InlineKeyboardButton("🚀 Open in Mini App", web_app=WebAppInfo(url=MINI_APP_URL))
        ]] + list(keyboard.inline_keyboard)

        keyboard = InlineKeyboardMarkup(new_buttons)

    await msg.edit_text(
        f"📹 *{title}*\n{dur_str}\n\nChoose a format:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard,
    )


# ─────────────────────────────────────────────
#  Callback Query Handler
# ─────────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data == "cancel":
        if user_id in user_sessions:
            user_sessions[user_id]["cancelled"] = True
            user_sessions.pop(user_id, None)
        await query.edit_message_text("✅ Cancelled.")
        return

    if data.startswith("plpage|"):
        page = int(data.split("|")[1])
        await show_playlist_page(update, context, query.message, user_id, page)
        return

    if data.startswith("plentry|"):
        idx = int(data.split("|")[1])
        session = user_sessions.get(user_id)
        if not session:
            await query.edit_message_text("❌ Session expired. Send the URL again.")
            return
        entry = session["entries"][idx]
        entry_url = entry.get("url") or entry.get("webpage_url", "")
        await query.edit_message_text(f"🔍 Fetching formats for entry {idx+1}...")

        opts = {**get_ydl_base_opts(), "noplaylist": True}
        info = await asyncio.to_thread(extract_info_safe, entry_url, opts)
        if not info:
            await query.edit_message_text("❌ Could not fetch this entry.")
            return

        info_id = f"{user_id}_pl_{idx}"
        user_sessions[user_id] = {
            "type": "single",
            "url": entry_url,
            "info": info,
            "info_id": info_id,
            "cancelled": False,
        }
        title = info.get("title", f"Entry {idx+1}")
        formats = info.get("formats", [])
        keyboard = build_format_keyboard(formats, info_id)
        await query.edit_message_text(
            f"📹 *{title}*\n\nChoose a format:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
        return

    if data.startswith("plall|"):
        _, fmt, media_type = data.split("|")
        session = user_sessions.get(user_id)
        if not session:
            await query.edit_message_text("❌ Session expired.")
            return
        entries = session["entries"]
        playlist_title = session["playlist_title"]
        await query.edit_message_text(
            f"⬇️ Starting playlist: *{playlist_title}* ({len(entries)} videos)",
            parse_mode=ParseMode.MARKDOWN
        )
        for i, entry in enumerate(entries):
            if user_sessions.get(user_id, {}).get("cancelled"):
                break
            entry_url = entry.get("url") or entry.get("webpage_url", "")
            title = entry.get("title", f"Entry {i+1}")
            status = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"⬇️ [{i+1}/{len(entries)}] *{title}*",
                parse_mode=ParseMode.MARKDOWN,
            )
            await process_download_raw(update, context, entry_url, fmt, media_type, status)
        return

    if data.startswith("dl|"):
        _, info_id, fmt, media_type = data.split("|", 3)
        session = user_sessions.get(user_id)
        if not session:
            await query.edit_message_text("❌ Session expired. Please send the URL again.")
            return
        url = session["url"]
        await query.edit_message_text("⬇️ Starting download...")
        await process_download_raw(update, context, url, fmt, media_type, query.message)


# ─────────────────────────────────────────────
#  Core download + send
# ─────────────────────────────────────────────

async def process_download(update, context, url, fmt, media_type):
    msg = await update.message.reply_text("⬇️ Starting download...")
    await process_download_raw(update, context, url, fmt, media_type, msg)


async def process_download_raw(update, context, url, fmt, media_type, status_msg):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    filepath = None

    try:
        filepath = await download_media(url, fmt, media_type, chat_id, status_msg, context)

        if not filepath or not filepath.exists():
            await status_msg.edit_text("❌ Download failed. File could not be saved.")
            return

        file_size = filepath.stat().st_size
        size_mb = file_size / (1024 * 1024)

        if size_mb > MAX_FILE_SIZE_MB:
            await status_msg.edit_text(
                f"❌ File too large ({sizeof_fmt(file_size)}).\n"
                f"Max: {MAX_FILE_SIZE_MB} MB."
            )
            filepath.unlink(missing_ok=True)
            return

        await status_msg.edit_text(
            f"📤 *Uploading* ({sizeof_fmt(file_size)})...",
            parse_mode=ParseMode.MARKDOWN,
        )
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)

        with open(filepath, "rb") as f:
            if media_type == "audio":
                await context.bot.send_audio(
                    chat_id=chat_id, audio=f, filename=filepath.name,
                    read_timeout=300, write_timeout=300, connect_timeout=60,
                )
            else:
                try:
                    await context.bot.send_video(
                        chat_id=chat_id, video=f, filename=filepath.name,
                        supports_streaming=True,
                        read_timeout=600, write_timeout=600, connect_timeout=60,
                    )
                except Exception:
                    f.seek(0)
                    await context.bot.send_document(
                        chat_id=chat_id, document=f, filename=filepath.name,
                        read_timeout=600, write_timeout=600, connect_timeout=60,
                    )

        await status_msg.edit_text(f"✅ Done! `{filepath.name}`", parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.exception(f"Download/upload error: {e}")
        await status_msg.edit_text(f"❌ Error: {str(e)[:200]}")
    finally:
        try:
            if filepath and filepath.exists():
                filepath.unlink()
        except Exception:
            pass
        user_sessions.pop(user_id, None)


# ─────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────

def main():
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Please set BOT_TOKEN in .env")
        return

    if MINI_APP_URL:
        print(f"🌐 Mini App URL: {MINI_APP_URL}")
    else:
        print("ℹ️  MINI_APP_URL not set — Mini App button disabled")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("info", info_command))
    app.add_handler(CommandHandler("audio", audio_command))
    app.add_handler(CommandHandler("playlist", playlist_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, url_handler))

    print("🤖 YouTube Downlader Bot running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
