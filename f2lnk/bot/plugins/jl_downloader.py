# f2lnk/bot/plugins/jl_downloader.py
# /jl — Universal Media Downloader
# Strategy: yt-dlp (1000+ sites) → HTML scraping with cookies → direct download

import os
import re
import time
import shutil
import asyncio
import logging
import aiohttp
import mimetypes
from urllib.parse import urlparse, unquote, urljoin, quote_plus

from pyrogram import filters, Client
from pyrogram.types import Message
from pyrogram.errors import MessageNotModified
from pyromod.exceptions import ListenerTimeout

from f2lnk.bot import StreamBot
from f2lnk.vars import Var
from f2lnk.utils.database import Database
from f2lnk.utils.human_readable import humanbytes
from f2lnk.utils.split_upload import upload_file_or_split

logger = logging.getLogger(__name__)
db = Database(Var.DATABASE_URL, Var.name)

DOWNLOAD_ROOT = "./jl_downloads"
os.makedirs(DOWNLOAD_ROOT, exist_ok=True)

# ══════════════════════════════════════════════════════
# Browser headers  — makes requests look like real Chrome
# ══════════════════════════════════════════════════════
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


# ══════════════════════════════════════════════════════
# STRATEGY 1: yt-dlp  (supports 1000+ sites)
# ══════════════════════════════════════════════════════
_YTDLP_BASE = [
    "yt-dlp",
    "--no-warnings",
    "--no-playlist",
    "--no-check-certificates",
    "--prefer-insecure",
    "--geo-bypass",
    "--user-agent", BROWSER_HEADERS["User-Agent"],
    "--socket-timeout", "30",
    "--retries", "3",
    "--fragment-retries", "3",
]


async def _try_ytdlp_info(url: str) -> dict | None:
    """Get media info via yt-dlp. Returns None on failure."""
    import json
    strategies = [
        _YTDLP_BASE + ["--no-download", "--dump-json", url],
        _YTDLP_BASE + ["--no-download", "--dump-json",
                        "--allow-unplayable-formats", url],
        _YTDLP_BASE + ["--no-download", "--dump-json",
                        "--force-generic-extractor", url],
    ]
    for cmd in strategies:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=45)
            if proc.returncode == 0 and stdout.strip():
                return json.loads(stdout.decode("utf-8", errors="replace"))
        except Exception:
            pass
    return None


async def _try_ytdlp_download(url: str, out_dir: str,
                              status_msg: Message) -> list:
    """Download via yt-dlp with multiple format fallbacks. Returns file list."""
    out_tpl = os.path.join(out_dir, "%(title).100s.%(ext)s")
    format_tries = [
        ["-f", "bv*+ba/b/best", "--merge-output-format", "mp4"],
        ["-f", "best/bestvideo+bestaudio", "--merge-output-format", "mp4"],
        ["-f", "best", "--allow-unplayable-formats"],
        ["-f", "best", "--force-generic-extractor"],
    ]
    for fmt in format_tries:
        cmd = _YTDLP_BASE + fmt + ["-o", out_tpl, "--newline", url]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        last_ts = time.time()
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            txt = line.decode("utf-8", errors="replace").strip()
            now = time.time()
            if now - last_ts > 3:
                if "[download]" in txt and "%" in txt:
                    try:
                        await status_msg.edit_text(
                            f"⬇️ **Downloading...**\n`{txt}`")
                    except Exception:
                        pass
                    last_ts = now
                elif "[Merger]" in txt or "[ffmpeg]" in txt:
                    try:
                        await status_msg.edit_text(
                            "🔄 **Merging audio & video...**")
                    except Exception:
                        pass
                    last_ts = now
        await proc.wait()
        files = _find_files(out_dir)
        if files:
            return files
    return []


# ══════════════════════════════════════════════════════
# STRATEGY 2: HTML scraping  (cookies + browser headers)
# Works for sites that embed videos in HTML source
# ══════════════════════════════════════════════════════

# Media URL patterns to search in page HTML
_MEDIA_PATTERNS = [
    # Direct video/audio URLs
    r'https?://[^\s"\'<>]+\.(?:mp4|mkv|webm|avi|mov|flv|m4v|ts|m3u8)(?:\?[^\s"\'<>]*)?',
    # JSON-embedded URLs
    r'"(?:file|src|url|source|video_url|videoUrl|stream_url|download_url|mp4|hls)"'
    r'\s*:\s*"(https?://[^"]+)"',
    # HTML5 video source
    r'<source[^>]+src=["\']([^"\']+)["\']',
    r'<video[^>]+src=["\']([^"\']+)["\']',
    # og:video meta
    r'<meta[^>]+(?:content|value)=["\']([^"\']+\.(?:mp4|m3u8|webm)[^"\']*)["\']',
    # Player embed URLs
    r'(?:player|embed|iframe)[^"\']*["\']([^"\']+\.(?:mp4|m3u8|webm)[^"\']*)["\']',
    # data-src attributes
    r'data-(?:src|url|video)["\s=]+["\']?([^"\'>\s]+\.(?:mp4|m3u8|webm)[^"\'>\s]*)',
]


async def _try_scrape_and_download(url: str, out_dir: str,
                                   status_msg: Message) -> list:
    """
    Scrape the page HTML for video URLs, then download the best one found.
    Uses browser headers + cookies for maximum compatibility.
    """
    try:
        await status_msg.edit_text("🔍 **Scraping page for media...**")
    except Exception:
        pass

    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    headers = {**BROWSER_HEADERS, "Referer": url, "Origin": origin}

    # Fetch page HTML
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(url, headers=headers,
                                allow_redirects=True) as resp:
                if resp.status != 200:
                    return []
                html = await resp.text()
                final_url = str(resp.url)
    except Exception as e:
        logger.warning("Scrape fetch failed: %s", e)
        return []

    # Extract all candidate media URLs
    candidates = []
    for pattern in _MEDIA_PATTERNS:
        for m in re.finditer(pattern, html, re.IGNORECASE):
            found = m.group(m.lastindex or 0)
            found = found.strip().strip('"').strip("'")
            if found.startswith("//"):
                found = "https:" + found
            elif found.startswith("/"):
                found = urljoin(final_url, found)
            if found.startswith("http") and found not in candidates:
                candidates.append(found)

    if not candidates:
        logger.info("No media URLs found in page HTML")
        return []

    logger.info("Found %d candidate URLs in page", len(candidates))

    # Try candidates — prefer mp4, then m3u8, then others
    def _sort_key(u):
        u_lower = u.lower()
        if ".mp4" in u_lower:
            return 0
        if ".m3u8" in u_lower:
            return 1
        if ".webm" in u_lower:
            return 2
        return 3
    candidates.sort(key=_sort_key)

    # Try to download the best candidate
    for media_url in candidates[:5]:  # try top 5
        logger.info("Trying scraped URL: %s", media_url[:80])

        is_hls = ".m3u8" in media_url.lower()
        if is_hls:
            # Use ffmpeg for HLS
            files = await _download_hls(media_url, out_dir, status_msg,
                                        headers)
            if files:
                return files
        else:
            # Direct download
            files = await _direct_download(media_url, out_dir, status_msg,
                                           headers)
            if files:
                return files

    return []


async def _download_hls(stream_url: str, out_dir: str,
                        status_msg: Message, headers: dict) -> list:
    """Download HLS stream using ffmpeg."""
    try:
        await status_msg.edit_text(
            "⬇️ **Downloading HLS stream via FFmpeg...**\n"
            "(Re-encoding for smooth playback)")
    except Exception:
        pass

    ffmpeg_headers = {k: v for k, v in headers.items() if k != "Range"}
    header_str = "".join(f"{k}: {v}\r\n" for k, v in ffmpeg_headers.items())

    fname = "video.mp4"
    fpath = os.path.join(out_dir, fname)

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-headers", header_str,
        "-i", stream_url,
        "-c:v", "libx264", "-c:a", "aac",
        "-b:a", "128k", "-crf", "23",
        "-movflags", "+faststart",
        fpath,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.warning("FFmpeg HLS failed: %s",
                       stderr.decode(errors="replace")[-200:])
        return []
    if os.path.exists(fpath) and os.path.getsize(fpath) > 0:
        return [fpath]
    return []


# ══════════════════════════════════════════════════════
# STRATEGY 3: Direct aiohttp download  (final fallback)
# ══════════════════════════════════════════════════════

async def _direct_download(url: str, out_dir: str,
                           status_msg: Message,
                           headers: dict = None) -> list:
    """Download file directly via aiohttp."""
    if headers is None:
        headers = {**BROWSER_HEADERS, "Referer": url}

    try:
        timeout = aiohttp.ClientTimeout(total=600)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(url, headers=headers,
                                allow_redirects=True) as resp:
                if resp.status not in (200, 206):
                    return []

                ctype = resp.headers.get("Content-Type", "")
                if "text/html" in ctype:
                    return []  # not a file

                # Determine filename
                fname = "download"
                if "Content-Disposition" in resp.headers:
                    cd = resp.headers["Content-Disposition"]
                    match = re.findall(
                        r'filename[^;=\n]*=[\'"]?([^\'";\n]+)', cd)
                    if match:
                        fname = match[0].strip()
                if fname == "download":
                    path = urlparse(str(resp.url)).path
                    fname = unquote(os.path.basename(path)) or "download"

                # Sanitize
                fname = re.sub(r'[<>:"/\\|?*]', '_', fname)
                if not os.path.splitext(fname)[1]:
                    ext = mimetypes.guess_extension(ctype.split(";")[0]) or ".mp4"
                    fname += ext

                total = int(resp.headers.get("Content-Length", 0))
                fpath = os.path.join(out_dir, fname)
                downloaded = 0
                last_ts = time.time()

                with open(fpath, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        f.write(chunk)
                        downloaded += len(chunk)
                        now = time.time()
                        if now - last_ts > 3:
                            prog = f"⬇️ **Downloading...**\n`{humanbytes(downloaded)}`"
                            if total:
                                prog += f" / `{humanbytes(total)}`"
                            try:
                                await status_msg.edit_text(prog)
                            except Exception:
                                pass
                            last_ts = now

                if os.path.exists(fpath) and os.path.getsize(fpath) > 0:
                    return [fpath]
    except Exception as e:
        logger.warning("Direct download failed: %s", e)
    return []


# ══════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════

def _find_files(directory: str) -> list:
    """Find all non-empty files in directory."""
    files = []
    for f in os.listdir(directory):
        fp = os.path.join(directory, f)
        if os.path.isfile(fp) and os.path.getsize(fp) > 0:
            files.append(fp)
    return files


# ══════════════════════════════════════════════════════
# MAIN PROCESS — tries all 3 strategies in order
# ══════════════════════════════════════════════════════

async def process_jl_task(client: Client, message: Message, page_url: str):
    status_msg = None
    temp_dir = os.path.join(DOWNLOAD_ROOT,
                            f"{message.from_user.id}_{int(time.time())}")
    os.makedirs(temp_dir, exist_ok=True)

    try:
        status_msg = await message.reply_text(
            "🔍 **Analyzing URL...**", quote=True)

        # ── Get media info (optional, don't fail if unavailable) ──
        title = "download"
        ext = "mp4"
        filesize = 0
        site = ""
        try:
            info = await _try_ytdlp_info(page_url)
            if info:
                title = info.get("title", "download")
                ext = info.get("ext", "mp4")
                filesize = info.get("filesize") or info.get(
                    "filesize_approx") or 0
                site = info.get("extractor", "")
                dur = info.get("duration", 0)

                info_text = f"✅ **Media found!**\n📁 `{title[:80]}`\n"
                if site:
                    info_text += f"🌐 {site}\n"
                if dur:
                    m, s = divmod(int(dur), 60)
                    h, m = divmod(m, 60)
                    info_text += f"⏱ {f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'}\n"
                if filesize:
                    info_text += f"📦 ~{humanbytes(filesize)}\n"
                await status_msg.edit_text(info_text)
        except Exception:
            pass

        # ── Ask for filename ──
        try:
            default_name = re.sub(r'[<>:"/\\|?*]', '', title)[:80]
            if not default_name:
                default_name = "download"
            default_name += f".{ext}"

            ask = await message.chat.ask(
                f"📝 Send filename or **/skip** for default:\n"
                f"`{default_name}`",
                timeout=60,
            )
            if ask.text and ask.text.strip().lower() != "/skip":
                custom_name = ask.text.strip()
            else:
                custom_name = None
        except ListenerTimeout:
            custom_name = None

        filename = custom_name or default_name

        # ── STRATEGY 1: yt-dlp ──
        await status_msg.edit_text("⬇️ **Trying yt-dlp...**")
        downloaded_files = await _try_ytdlp_download(
            page_url, temp_dir, status_msg)

        # ── STRATEGY 2: HTML scraping ──
        if not downloaded_files:
            logger.info("yt-dlp failed, trying HTML scraping for %s",
                        page_url)
            await status_msg.edit_text(
                "⬇️ **yt-dlp failed, trying page scraping...**")
            downloaded_files = await _try_scrape_and_download(
                page_url, temp_dir, status_msg)

        # ── STRATEGY 3: Direct download ──
        if not downloaded_files:
            logger.info("Scraping failed, trying direct download for %s",
                        page_url)
            await status_msg.edit_text(
                "⬇️ **Trying direct download...**")
            downloaded_files = await _direct_download(
                page_url, temp_dir, status_msg)

        # ── All failed ──
        if not downloaded_files:
            await status_msg.edit_text(
                "❌ **Download failed**\n\n"
                "All methods failed (yt-dlp, scraping, direct).\n"
                "The site may require login or special access.")
            return

        # ── Rename if custom name ──
        if custom_name and len(downloaded_files) == 1:
            old = downloaded_files[0]
            old_ext = os.path.splitext(old)[1]
            if not os.path.splitext(custom_name)[1]:
                custom_name += old_ext
            new = os.path.join(temp_dir, custom_name)
            os.rename(old, new)
            downloaded_files = [new]
            filename = custom_name

        # ── Upload ──
        total = len(downloaded_files)
        for i, fp in enumerate(downloaded_files):
            fname = os.path.basename(fp)
            fsize = os.path.getsize(fp)

            try:
                await status_msg.edit_text(
                    f"⬆️ **Uploading** {i+1}/{total}: `{fname}`\n"
                    f"📦 Size: `{humanbytes(fsize)}`")
            except Exception:
                pass

            # Footer
            try:
                user_info = await db.get_user_info(message.from_user.id)
                footer = user_info.get("footer", "")
            except Exception:
                footer = ""

            caption = f"**{fname}**"
            if footer:
                caption += f"\n\n{footer}"

            try:
                await upload_file_or_split(
                    client, message.chat.id, fp,
                    caption=caption, file_name=fname,
                    reply_to=message.id,
                )
            except Exception as e:
                await message.reply_text(
                    f"❌ Upload failed for `{fname}`: `{e}`")

        try:
            await status_msg.delete()
        except Exception:
            pass

    except Exception as e:
        logger.error("JL error: %s", e, exc_info=True)
        if status_msg:
            try:
                await status_msg.edit_text(f"❌ Error: `{e}`")
            except Exception:
                pass
    finally:
        if os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


# ══════════════════════════════════════════════════════
# Command Handler
# ══════════════════════════════════════════════════════

@StreamBot.on_message(filters.command(["jl_downloader", "jl"]) & filters.private)
async def jl_handler(client: Client, m: Message):
    try:
        from f2lnk.bot.plugins.restart import touch_activity
        touch_activity()
    except Exception:
        pass

    if len(m.command) < 2 and not m.reply_to_message:
        await m.reply_text(
            "🎬 **Universal Media Downloader**\n\n"
            "**Usage:** `/jl <URL>`\n\n"
            "**How it works:**\n"
            "1️⃣ yt-dlp (1000+ sites)\n"
            "2️⃣ HTML scraping (video sites)\n"
            "3️⃣ Direct download (any URL)\n\n"
            "**Supported:** YouTube, Instagram, Facebook,\n"
            "TikTok, Twitter/X, Vimeo, Dailymotion,\n"
            "Reddit, SoundCloud, Twitch, xHamster,\n"
            "Pornhub, and thousands more!\n\n"
            "Files > 2GB auto-split 📦",
        )
        return

    url = (m.command[1].strip()
           if len(m.command) > 1
           else m.reply_to_message.text.strip())
    if not url.startswith("http"):
        await m.reply_text("❌ Please send a valid URL.")
        return

    await process_jl_task(client, m, url)
                                    
