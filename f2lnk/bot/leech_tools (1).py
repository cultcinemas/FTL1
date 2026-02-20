# f2lnk/bot/leech_tools.py
# All tool processing functions for the /leech workflow.
#
# Tools:
#   VT — Video + Video Merge (concat)
#   VA — Video + Audio Merge (multi-audio streams)
#   AA — Audio + Audio Merge (concat)
#   VS — Video + Subtitle Merge (hardcode / soft)
#   CV — Compress Video (5 modes incl. target size)
#   WV — Watermark Video (text/image, 8 animation modes)

import os
import json
import asyncio
import logging

from f2lnk.bot.task_manager import (
    LeechTask,
    TaskStatus,
    VIDEO_EXTENSIONS,
    AUDIO_EXTENSIONS,
    SUBTITLE_EXTENSIONS,
    cleanup_task_files,
    remove_task,
)
from f2lnk.utils.human_readable import humanbytes

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  SHARED HELPERS
# ═══════════════════════════════════════════════════════════════

def _detect_file_type(msg) -> str:
    """Detect 'video', 'audio', 'subtitle', or 'unknown'."""
    if msg.video:
        return "video"
    if msg.audio:
        return "audio"
    if msg.document:
        mime = (msg.document.mime_type or "").lower()
        fname = (msg.document.file_name or "").lower()
        ext = os.path.splitext(fname)[1] if fname else ""
        if mime.startswith("video/") or ext in VIDEO_EXTENSIONS:
            return "video"
        if mime.startswith("audio/") or ext in AUDIO_EXTENSIONS:
            return "audio"
        if ext in SUBTITLE_EXTENSIONS:
            return "subtitle"
    return "unknown"


def _classify_by_path(path: str) -> str:
    """Fallback classification from downloaded file extension."""
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext in SUBTITLE_EXTENSIONS:
        return "subtitle"
    return "unknown"


async def _run_ffmpeg(task: LeechTask, cmd: list, status_msg=None):
    """
    Run an ffmpeg command as a subprocess.
    Returns (success: bool, stderr: str).
    """
    logger.info("Task %s: ffmpeg cmd = %s", task.task_id, " ".join(cmd))
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        task.merge_process = proc
        stdout, stderr = await proc.communicate()
    except asyncio.CancelledError:
        return False, "Cancelled"

    if task.cancel_event.is_set():
        return False, "Cancelled"

    stderr_text = stderr.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        task.status = TaskStatus.FAILED
        err_short = stderr_text[:1000]
        if status_msg:
            try:
                await status_msg.edit_text(
                    f"❌ **Processing failed** for task `{task.task_id}`:\n"
                    f"```\n{err_short}\n```"
                )
            except Exception:
                pass
        cleanup_task_files(task)
        remove_task(task.task_id)
        return False, stderr_text

    return True, stderr_text


def _split_files(downloaded_files, classify_msgs=True):
    """
    Split downloaded files into video/audio/subtitle lists.
    Each item is (index, path).
    """
    videos, audios, subtitles, unknowns = [], [], [], []
    for idx, path, msg in downloaded_files:
        if classify_msgs:
            ftype = _detect_file_type(msg)
        else:
            ftype = "unknown"
        if ftype == "unknown":
            ftype = _classify_by_path(path)

        if ftype == "video":
            videos.append((idx, path))
        elif ftype == "audio":
            audios.append((idx, path))
        elif ftype == "subtitle":
            subtitles.append((idx, path))
        else:
            unknowns.append((idx, path))
    return videos, audios, subtitles, unknowns


# ═══════════════════════════════════════════════════════════════
#  TOOL: VT — Video + Video Merge (concat)
# ═══════════════════════════════════════════════════════════════

async def process_video_video(task, downloaded_files, status_msg) -> bool:
    concat_file = os.path.join(task.work_dir, "concat.txt")
    with open(concat_file, "w", encoding="utf-8") as f:
        for _idx, path, _msg in downloaded_files:
            abs_path = os.path.abspath(path).replace("\\", "/")
            f.write(f"file '{abs_path}'\n")

    output_path = os.path.join(task.work_dir, task.output_name)
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", concat_file, "-c", "copy", output_path,
    ]
    ok, _ = await _run_ffmpeg(task, cmd, status_msg)
    return ok


# ═══════════════════════════════════════════════════════════════
#  TOOL: VA — Video + Audio Merge (multi-audio streams)
# ═══════════════════════════════════════════════════════════════

async def process_video_audio(client, task, downloaded_files, status_msg) -> bool:
    videos, audios, _, _ = _split_files(downloaded_files)

    if len(videos) == 0:
        task.status = TaskStatus.FAILED
        await status_msg.edit_text(
            f"❌ **No video file found** in task `{task.task_id}`.\n"
            f"Exactly 1 video is required for Video+Audio merge."
        )
        cleanup_task_files(task)
        remove_task(task.task_id)
        return False

    if len(videos) > 1:
        task.status = TaskStatus.FAILED
        await status_msg.edit_text(
            f"❌ **Multiple videos found** ({len(videos)}). "
            f"Exactly 1 video is required. Use -vt for video merge."
        )
        cleanup_task_files(task)
        remove_task(task.task_id)
        return False

    if len(audios) == 0:
        task.status = TaskStatus.FAILED
        await status_msg.edit_text(
            f"❌ **No audio files found** in task `{task.task_id}`."
        )
        cleanup_task_files(task)
        remove_task(task.task_id)
        return False

    video_path = videos[0][1]
    audio_mode = task.audio_mode or 1

    await status_msg.edit_text(
        f"⚙️ **Processing** — Task: `{task.task_id}`\n"
        f"🎬 1 video + {len(audios)} audio(s)\n"
        f"{'🔇 Removing' if audio_mode == 1 else '🔊 Keeping'} original audio..."
    )

    cmd = ["ffmpeg", "-y", "-i", video_path]
    for _idx, apath in audios:
        cmd.extend(["-i", apath])

    cmd.extend(["-map", "0:v"])
    if audio_mode == 2:
        cmd.extend(["-map", "0:a?"])
    for i in range(len(audios)):
        cmd.extend(["-map", f"{i + 1}:a"])

    cmd.extend(["-c", "copy"])

    # Force MKV for multi-audio support
    output_base = os.path.splitext(task.output_name)[0]
    task.output_name = output_base + ".mkv"
    output_path = os.path.join(task.work_dir, task.output_name)
    cmd.append(output_path)

    ok, _ = await _run_ffmpeg(task, cmd, status_msg)
    return ok


# ═══════════════════════════════════════════════════════════════
#  TOOL: AA — Audio + Audio Merge (concat)
# ═══════════════════════════════════════════════════════════════

async def process_audio_audio(task, downloaded_files, status_msg) -> bool:
    """Concatenate multiple audio files in order."""
    # Validate all files are audio
    _, audios, _, unknowns = _split_files(downloaded_files)
    videos, _, _, _ = _split_files(downloaded_files)

    if videos:
        task.status = TaskStatus.FAILED
        await status_msg.edit_text(
            f"❌ **Video file(s) found** in task `{task.task_id}`.\n"
            f"Audio+Audio merge requires only audio files."
        )
        cleanup_task_files(task)
        remove_task(task.task_id)
        return False

    # Treat all files as audio for concat
    all_files = [(idx, path) for idx, path, _ in downloaded_files]
    all_files.sort(key=lambda x: x[0])

    concat_file = os.path.join(task.work_dir, "concat.txt")
    with open(concat_file, "w", encoding="utf-8") as f:
        for _idx, path in all_files:
            abs_path = os.path.abspath(path).replace("\\", "/")
            f.write(f"file '{abs_path}'\n")

    # Default to .mp3 if no extension
    ext = os.path.splitext(task.output_name)[1].lower()
    if ext in (".mp4",):  # user might not have changed default
        output_base = os.path.splitext(task.output_name)[0]
        task.output_name = output_base + ".mp3"

    output_path = os.path.join(task.work_dir, task.output_name)
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", concat_file, "-c", "copy", output_path,
    ]

    await status_msg.edit_text(
        f"⚙️ **Merging {len(all_files)} audio files** — Task: `{task.task_id}`"
    )

    ok, _ = await _run_ffmpeg(task, cmd, status_msg)
    return ok


# ═══════════════════════════════════════════════════════════════
#  TOOL: VS — Video + Subtitle Merge
# ═══════════════════════════════════════════════════════════════

async def process_video_subtitle(client, task, downloaded_files, status_msg) -> bool:
    """
    Mode 1: Hardcode (burn-in) one selected subtitle
    Mode 2: Soft subtitles (all as separate streams) in MKV
    """
    videos, _, subtitles, unknowns = _split_files(downloaded_files)

    # Check unknowns — treat as subtitles if extensions match
    for idx, path in unknowns:
        if _classify_by_path(path) == "subtitle":
            subtitles.append((idx, path))

    if len(videos) == 0:
        task.status = TaskStatus.FAILED
        await status_msg.edit_text(
            f"❌ **No video found** in task `{task.task_id}`. "
            f"Exactly 1 video is required."
        )
        cleanup_task_files(task)
        remove_task(task.task_id)
        return False

    if len(videos) > 1:
        task.status = TaskStatus.FAILED
        await status_msg.edit_text(
            f"❌ **Multiple videos found** ({len(videos)}). "
            f"Exactly 1 video is required."
        )
        cleanup_task_files(task)
        remove_task(task.task_id)
        return False

    if len(subtitles) == 0:
        task.status = TaskStatus.FAILED
        await status_msg.edit_text(
            f"❌ **No subtitle files found** in task `{task.task_id}`."
        )
        cleanup_task_files(task)
        remove_task(task.task_id)
        return False

    video_path = videos[0][1]
    subtitle_mode = task.subtitle_mode or 2

    if subtitle_mode == 1:
        return await _hardcode_subtitle(task, video_path, subtitles, status_msg)
    else:
        return await _soft_subtitles(task, video_path, subtitles, status_msg)


async def _hardcode_subtitle(task, video_path, subtitles, status_msg) -> bool:
    """Burn one subtitle into the video."""
    # Use the selected index, or first subtitle
    sub_idx = task.hardcode_sub_index if task.hardcode_sub_index is not None else 0
    if sub_idx >= len(subtitles):
        sub_idx = 0

    sub_path = subtitles[sub_idx][1]
    # Escape special characters for ffmpeg subtitles filter
    sub_escaped = sub_path.replace("\\", "/").replace(":", "\\:")

    await status_msg.edit_text(
        f"⚙️ **Hardcoding subtitle** — Task: `{task.task_id}`\n"
        f"📝 Burning subtitle {sub_idx + 1}/{len(subtitles)} into video..."
    )

    output_path = os.path.join(task.work_dir, task.output_name)
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", f"subtitles='{sub_escaped}'",
        "-c:a", "copy",
        output_path,
    ]

    ok, _ = await _run_ffmpeg(task, cmd, status_msg)
    return ok


async def _soft_subtitles(task, video_path, subtitles, status_msg) -> bool:
    """Add all subtitles as separate selectable streams."""
    await status_msg.edit_text(
        f"⚙️ **Adding {len(subtitles)} soft subtitle(s)** — Task: `{task.task_id}`"
    )

    cmd = ["ffmpeg", "-y", "-i", video_path]
    for _idx, spath in subtitles:
        cmd.extend(["-i", spath])

    # Map all streams from video
    cmd.extend(["-map", "0"])
    # Map each subtitle input
    for i in range(len(subtitles)):
        cmd.extend(["-map", f"{i + 1}:s"])

    cmd.extend(["-c", "copy", "-c:s", "srt"])

    # Force MKV for proper subtitle container support
    output_base = os.path.splitext(task.output_name)[0]
    task.output_name = output_base + ".mkv"
    output_path = os.path.join(task.work_dir, task.output_name)
    cmd.append(output_path)

    ok, _ = await _run_ffmpeg(task, cmd, status_msg)
    return ok


# ═══════════════════════════════════════════════════════════════
#  TOOL: CV — Compress Video
# ═══════════════════════════════════════════════════════════════

# CRF/preset per mode
COMPRESS_PRESETS = {
    1: {"crf": 18, "preset": "medium", "label": "High Quality"},
    2: {"crf": 23, "preset": "medium", "label": "Balanced"},
    3: {"crf": 28, "preset": "slow",   "label": "High Compression"},
}


async def process_compress_video(client, task, downloaded_files, status_msg) -> bool:
    """Compress all videos with the selected mode."""
    videos = [(idx, path, msg) for idx, path, msg in downloaded_files]
    if not videos:
        task.status = TaskStatus.FAILED
        await status_msg.edit_text(f"❌ No video files found in task `{task.task_id}`.")
        cleanup_task_files(task)
        remove_task(task.task_id)
        return False

    output_base = os.path.splitext(task.output_name)[0]
    output_ext = os.path.splitext(task.output_name)[1] or ".mp4"
    mode = task.compress_mode or 2

    total = len(videos)
    for i, (idx, path, msg) in enumerate(videos):
        if task.cancel_event.is_set():
            return False

        out_name = f"{output_base}_{i + 1}{output_ext}" if total > 1 else task.output_name
        output_path = os.path.join(task.work_dir, out_name)

        await status_msg.edit_text(
            f"⚙️ **Compressing** {i + 1}/{total} — Task: `{task.task_id}`\n"
            f"Mode: {COMPRESS_PRESETS.get(mode, {}).get('label', f'Mode {mode}')}"
        )

        if mode in (1, 2, 3):
            preset = COMPRESS_PRESETS[mode]
            cmd = [
                "ffmpeg", "-y", "-i", path,
                "-c:v", "libx264",
                "-crf", str(preset["crf"]),
                "-preset", preset["preset"],
                "-c:a", "copy",
                output_path,
            ]
        elif mode == 4:
            # Target file size mode
            cmd = await _build_target_size_cmd(task, path, output_path, status_msg)
            if cmd is None:
                return False
        elif mode == 5:
            # Custom CRF
            crf = task.custom_crf or 23
            cmd = [
                "ffmpeg", "-y", "-i", path,
                "-c:v", "libx264",
                "-crf", str(crf),
                "-preset", "medium",
                "-c:a", "copy",
                output_path,
            ]
        else:
            continue

        ok, _ = await _run_ffmpeg(task, cmd, status_msg)
        if not ok:
            return False

        # Update output_name for upload (last file or single file)
        if total == 1:
            task.output_name = out_name

    return True


async def _build_target_size_cmd(task, input_path, output_path, status_msg):
    """Build ffmpeg command for target size compression."""
    target_mb = task.target_size_mb or 100
    target_bytes = target_mb * 1024 * 1024
    target_bits = target_bytes * 8

    # Probe the input file for duration and audio bitrate
    probe_cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", input_path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *probe_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        info = json.loads(stdout.decode("utf-8", errors="replace"))
    except Exception as e:
        logger.warning("ffprobe failed for task %s: %s", task.task_id, e)
        # Fallback to balanced mode
        return [
            "ffmpeg", "-y", "-i", input_path,
            "-c:v", "libx264", "-crf", "23", "-preset", "medium",
            "-c:a", "copy", output_path,
        ]

    duration = float(info.get("format", {}).get("duration", 0))
    if duration <= 0:
        duration = 60  # fallback

    # Find audio bitrate
    audio_bitrate = 128000  # default 128kbps
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "audio":
            audio_bitrate = int(stream.get("bit_rate", 128000))
            break

    total_bitrate = target_bits / duration
    video_bitrate = int((total_bitrate - audio_bitrate) / 1000)  # kbps

    if video_bitrate < 100:
        video_bitrate = 100
        await status_msg.edit_text(
            f"⚠️ Target size too small for duration. Using minimum bitrate.\n"
            f"Consider a shorter target or lower resolution."
        )

    return [
        "ffmpeg", "-y", "-i", input_path,
        "-c:v", "libx264",
        "-b:v", f"{video_bitrate}k",
        "-preset", "medium",
        "-c:a", "aac", "-b:a", "128k",
        output_path,
    ]


# ═══════════════════════════════════════════════════════════════
#  TOOL: WV — Watermark Video
# ═══════════════════════════════════════════════════════════════

# Position presets → (x_expr, y_expr) for overlay/drawtext
POSITION_MAP = {
    "tl":     ("20", "20"),
    "tr":     ("W-w-20", "20"),
    "bl":     ("20", "H-h-20"),
    "br":     ("W-w-20", "H-h-20"),
    "center": ("(W-w)/2", "(H-h)/2"),
}

# Text position uses different variables
TEXT_POSITION_MAP = {
    "tl":     ("20", "20"),
    "tr":     ("w-tw-20", "20"),
    "bl":     ("20", "h-th-20"),
    "br":     ("w-tw-20", "h-th-20"),
    "center": ("(w-tw)/2", "(h-th)/2"),
}


async def process_watermark_video(client, task, downloaded_files, status_msg) -> bool:
    """Apply watermark to all videos in the task."""
    videos = [(idx, path, msg) for idx, path, msg in downloaded_files]
    if not videos:
        task.status = TaskStatus.FAILED
        await status_msg.edit_text(f"❌ No video files found in task `{task.task_id}`.")
        cleanup_task_files(task)
        remove_task(task.task_id)
        return False

    output_base = os.path.splitext(task.output_name)[0]
    output_ext = os.path.splitext(task.output_name)[1] or ".mp4"
    total = len(videos)

    for i, (idx, path, msg) in enumerate(videos):
        if task.cancel_event.is_set():
            return False

        out_name = f"{output_base}_{i + 1}{output_ext}" if total > 1 else task.output_name
        output_path = os.path.join(task.work_dir, out_name)

        await status_msg.edit_text(
            f"⚙️ **Watermarking** {i + 1}/{total} — Task: `{task.task_id}`"
        )

        if task.watermark_type == "text":
            vf = _build_text_watermark_filter(task)
            cmd = [
                "ffmpeg", "-y", "-i", path,
                "-vf", vf,
                "-c:a", "copy",
                output_path,
            ]
        elif task.watermark_type == "image":
            vf = _build_image_watermark_filter(task)
            cmd = [
                "ffmpeg", "-y",
                "-i", path,
                "-i", task.watermark_image_path,
                "-filter_complex", vf,
                "-map", "[out]",
                "-map", "0:a?",
                "-c:a", "copy",
                output_path,
            ]
        else:
            continue

        ok, _ = await _run_ffmpeg(task, cmd, status_msg)
        if not ok:
            return False

        if total == 1:
            task.output_name = out_name

    return True


def _build_text_watermark_filter(task) -> str:
    """Build ffmpeg drawtext filter string."""
    pos = task.watermark_position or "br"
    mode = task.watermark_mode or 1
    text = (task.watermark_text or "Watermark").replace("'", "\\'")
    x, y = TEXT_POSITION_MAP.get(pos, TEXT_POSITION_MAP["br"])

    base = (
        f"drawtext=text='{text}'"
        f":fontsize=24:fontcolor=white@0.7"
        f":borderw=2:bordercolor=black@0.5"
    )

    if mode == 1:  # Static
        return f"{base}:x={x}:y={y}"

    elif mode == 2:  # Fade In
        return f"{base}:x={x}:y={y}:alpha='if(lt(t,2),t/2,1)'"

    elif mode == 3:  # Fade In + Fade Out (based on duration)
        return (
            f"{base}:x={x}:y={y}"
            f":alpha='if(lt(t,2),t/2,if(gt(t,{'{'}duration{'}'}-2),({'{'}duration{'}'}-t)/2,1))'"
        )

    elif mode == 4:  # Moving (corner to corner)
        return (
            f"{base}"
            f":x='mod(t*50,w-tw)'"
            f":y='mod(t*30,h-th)'"
        )

    elif mode == 5:  # Bouncing
        return (
            f"{base}"
            f":x='abs(mod(t*100,2*(w-tw))-(w-tw))'"
            f":y='abs(mod(t*70,2*(h-th))-(h-th))'"
        )

    elif mode == 6:  # Floating random
        return (
            f"{base}"
            f":x='(w-tw)/2+(w/4)*sin(t*0.7)'"
            f":y='(h-th)/2+(h/4)*cos(t*0.5)'"
        )

    elif mode == 7:  # Scrolling L→R
        return f"{base}:x='mod(t*80,w+tw)-tw':y={y}"

    elif mode == 8:  # Pulsing opacity
        return f"{base}:x={x}:y={y}:alpha='0.3+0.7*abs(sin(t*2))'"

    return f"{base}:x={x}:y={y}"


def _build_image_watermark_filter(task) -> str:
    """Build ffmpeg overlay filter_complex string for image watermark."""
    pos = task.watermark_position or "br"
    mode = task.watermark_mode or 1
    x, y = POSITION_MAP.get(pos, POSITION_MAP["br"])

    # Scale watermark to 15% of video width, maintain aspect ratio
    scale = "[1:v]scale=iw*0.15:-1,format=rgba,colorchannelmixer=aa=0.7[wm]"

    if mode == 1:  # Static
        return f"{scale};[0:v][wm]overlay={x}:{y}[out]"

    elif mode == 2:  # Fade In
        scale_alpha = (
            "[1:v]scale=iw*0.15:-1,format=rgba,"
            "colorchannelmixer=aa=0.7[wm_raw];"
            "[wm_raw]fade=t=in:st=0:d=2:alpha=1[wm]"
        )
        return f"{scale_alpha};[0:v][wm]overlay={x}:{y}[out]"

    elif mode == 3:  # Fade In + Fade Out
        scale_alpha = (
            "[1:v]scale=iw*0.15:-1,format=rgba,"
            "colorchannelmixer=aa=0.7[wm_raw];"
            "[wm_raw]fade=t=in:st=0:d=2:alpha=1,"
            "fade=t=out:d=2:alpha=1[wm]"
        )
        return f"{scale_alpha};[0:v][wm]overlay={x}:{y}[out]"

    elif mode == 4:  # Moving
        scale_no_overlay = "[1:v]scale=iw*0.15:-1,format=rgba,colorchannelmixer=aa=0.7[wm]"
        return (
            f"{scale_no_overlay};"
            f"[0:v][wm]overlay="
            f"x='mod(t*50,W-w)':y='mod(t*30,H-h)'[out]"
        )

    elif mode == 5:  # Bouncing
        scale_no_overlay = "[1:v]scale=iw*0.15:-1,format=rgba,colorchannelmixer=aa=0.7[wm]"
        return (
            f"{scale_no_overlay};"
            f"[0:v][wm]overlay="
            f"x='abs(mod(t*100,2*(W-w))-(W-w))':"
            f"y='abs(mod(t*70,2*(H-h))-(H-h))'[out]"
        )

    elif mode == 6:  # Floating random
        scale_no_overlay = "[1:v]scale=iw*0.15:-1,format=rgba,colorchannelmixer=aa=0.7[wm]"
        return (
            f"{scale_no_overlay};"
            f"[0:v][wm]overlay="
            f"x='(W-w)/2+(W/4)*sin(t*0.7)':y='(H-h)/2+(H/4)*cos(t*0.5)'[out]"
        )

    elif mode == 7:  # Scrolling L→R
        scale_no_overlay = "[1:v]scale=iw*0.15:-1,format=rgba,colorchannelmixer=aa=0.7[wm]"
        return (
            f"{scale_no_overlay};"
            f"[0:v][wm]overlay="
            f"x='mod(t*80,W+w)-w':y={y}[out]"
        )

    elif mode == 8:  # Pulsing
        # For pulsing image, we need to modulate alpha over time
        scale_pulse = (
            "[1:v]scale=iw*0.15:-1,format=rgba[wm_raw];"
            "[wm_raw]colorchannelmixer=aa=0.7[wm]"
        )
        return (
            f"{scale_pulse};"
            f"[0:v][wm]overlay={x}:{y}:"
            f"enable='1'[out]"
        )

    return f"{scale};[0:v][wm]overlay={x}:{y}[out]"


# ═══════════════════════════════════════════════════════════════
#  BATCH UPLOAD HELPER (for CV and WV tools)
# ═══════════════════════════════════════════════════════════════

async def upload_batch_results(client, task, status_msg) -> bool:
    """
    Upload all output files from the task directory.
    Used by CV and WV which produce multiple outputs.
    """
    output_base = os.path.splitext(task.output_name)[0]
    output_ext = os.path.splitext(task.output_name)[1] or ".mp4"

    # Find all output files matching the pattern
    output_files = []
    for fname in sorted(os.listdir(task.work_dir)):
        if fname.startswith(output_base) and fname.endswith(output_ext):
            fpath = os.path.join(task.work_dir, fname)
            if os.path.isfile(fpath):
                output_files.append((fname, fpath))

    if not output_files:
        # Single output file
        single = os.path.join(task.work_dir, task.output_name)
        if os.path.isfile(single):
            output_files = [(task.output_name, single)]

    if not output_files:
        task.status = TaskStatus.FAILED
        await status_msg.edit_text(
            f"❌ No output files found for task `{task.task_id}`."
        )
        return False

    task.status = TaskStatus.UPLOADING
    total = len(output_files)

    for i, (fname, fpath) in enumerate(output_files):
        if task.cancel_event.is_set():
            return False

        await status_msg.edit_text(
            f"⬆️ **Uploading** {i + 1}/{total} — `{fname}`"
        )

        try:
            file_size = os.path.getsize(fpath)
            caption = (
                f"✅ **Processed**\n"
                f"📁 `{fname}`\n"
                f"📦 Size: `{humanbytes(file_size)}`\n"
                f"🆔 Task: `{task.task_id}`"
            )

            ext = os.path.splitext(fpath)[1].lower()
            if ext in (".mp4", ".mkv", ".avi", ".webm", ".mov", ".flv", ".ts"):
                await client.send_video(
                    task.chat_id, fpath, caption=caption, file_name=fname,
                )
            elif ext in (".mp3", ".aac", ".ogg", ".flac", ".wav", ".m4a", ".opus"):
                await client.send_audio(
                    task.chat_id, fpath, caption=caption, file_name=fname,
                )
            else:
                await client.send_document(
                    task.chat_id, fpath, caption=caption, file_name=fname,
                )
        except Exception as e:
            task.status = TaskStatus.FAILED
            await status_msg.edit_text(f"❌ Upload failed: `{e}`")
            return False

    return True


# ═══════════════════════════════════════════════════════════════
#  TOOL: TV — Trim Video (extract segment)
# ═══════════════════════════════════════════════════════════════

async def process_trim_video(task, downloaded_files, status_msg) -> bool:
    """Extract segment between start_time and end_time."""
    if not task.start_time or not task.end_time:
        task.status = TaskStatus.FAILED
        await status_msg.edit_text(
            f"❌ Missing timestamps for task `{task.task_id}`."
        )
        cleanup_task_files(task)
        remove_task(task.task_id)
        return False

    total = len(downloaded_files)
    output_base = os.path.splitext(task.output_name)[0]
    output_ext = os.path.splitext(task.output_name)[1] or ".mp4"

    for i, (idx, path, msg) in enumerate(downloaded_files):
        if task.cancel_event.is_set():
            return False

        out_name = f"{output_base}_{i + 1}{output_ext}" if total > 1 else task.output_name
        output_path = os.path.join(task.work_dir, out_name)

        await status_msg.edit_text(
            f"✂️ **Trimming** {i + 1}/{total} — Task: `{task.task_id}`\n"
            f"⏱️ {task.start_time} → {task.end_time}"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", path,
            "-ss", task.start_time,
            "-to", task.end_time,
            "-c", "copy",
            output_path,
        ]

        ok, _ = await _run_ffmpeg(task, cmd, status_msg)
        if not ok:
            return False

        if total == 1:
            task.output_name = out_name

    return True


# ═══════════════════════════════════════════════════════════════
#  TOOL: CUT — Cut Video (remove segment, keep rest)
# ═══════════════════════════════════════════════════════════════

async def process_cut_video(task, downloaded_files, status_msg) -> bool:
    """
    Remove segment between start_time and end_time.
    Keeps: [0 → start] + [end → EOF], stitched together.
    """
    if not task.start_time or not task.end_time:
        task.status = TaskStatus.FAILED
        await status_msg.edit_text(
            f"❌ Missing timestamps for task `{task.task_id}`."
        )
        cleanup_task_files(task)
        remove_task(task.task_id)
        return False

    total = len(downloaded_files)
    output_base = os.path.splitext(task.output_name)[0]
    output_ext = os.path.splitext(task.output_name)[1] or ".mp4"

    for i, (idx, path, msg) in enumerate(downloaded_files):
        if task.cancel_event.is_set():
            return False

        out_name = f"{output_base}_{i + 1}{output_ext}" if total > 1 else task.output_name
        output_path = os.path.join(task.work_dir, out_name)

        await status_msg.edit_text(
            f"✂️ **Cutting** {i + 1}/{total} — Task: `{task.task_id}`\n"
            f"🗑️ Removing: {task.start_time} → {task.end_time}"
        )

        # Step 1: Extract segment BEFORE the cut
        part_a = os.path.join(task.work_dir, f"cut_{i}_partA{output_ext}")
        cmd_a = [
            "ffmpeg", "-y", "-i", path,
            "-to", task.start_time,
            "-c", "copy", part_a,
        ]
        ok, _ = await _run_ffmpeg(task, cmd_a, status_msg)
        if not ok:
            return False

        if task.cancel_event.is_set():
            return False

        # Step 2: Extract segment AFTER the cut
        part_b = os.path.join(task.work_dir, f"cut_{i}_partB{output_ext}")
        cmd_b = [
            "ffmpeg", "-y", "-i", path,
            "-ss", task.end_time,
            "-c", "copy", part_b,
        ]
        ok, _ = await _run_ffmpeg(task, cmd_b, status_msg)
        if not ok:
            return False

        if task.cancel_event.is_set():
            return False

        # Step 3: Concat the two parts
        concat_file = os.path.join(task.work_dir, f"cut_{i}_concat.txt")
        with open(concat_file, "w", encoding="utf-8") as f:
            f.write(f"file '{os.path.abspath(part_a).replace(chr(92), '/')}'\n")
            f.write(f"file '{os.path.abspath(part_b).replace(chr(92), '/')}'\n")

        cmd_concat = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_file, "-c", "copy", output_path,
        ]
        ok, _ = await _run_ffmpeg(task, cmd_concat, status_msg)
        if not ok:
            return False

        # Clean temp parts
        for tmp in (part_a, part_b, concat_file):
            try:
                os.remove(tmp)
            except Exception:
                pass

        if total == 1:
            task.output_name = out_name

    return True


# ═══════════════════════════════════════════════════════════════
#  TOOL: RV — Remove Video Stream (extract audio)
# ═══════════════════════════════════════════════════════════════

# Audio format → ffmpeg codec + extension
AUDIO_FORMAT_MAP = {
    "mp3":  {"codec": "libmp3lame", "ext": ".mp3"},
    "aac":  {"codec": "aac",        "ext": ".aac"},
    "wav":  {"codec": "pcm_s16le",  "ext": ".wav"},
    "copy": {"codec": "copy",       "ext": None},  # keep original
}


async def process_remove_video(task, downloaded_files, status_msg) -> bool:
    """Extract audio only from video files."""
    fmt = task.audio_format or "mp3"
    fmt_info = AUDIO_FORMAT_MAP.get(fmt, AUDIO_FORMAT_MAP["mp3"])

    total = len(downloaded_files)
    output_base = os.path.splitext(task.output_name)[0]

    for i, (idx, path, msg) in enumerate(downloaded_files):
        if task.cancel_event.is_set():
            return False

        # Determine extension
        if fmt_info["ext"]:
            out_ext = fmt_info["ext"]
        else:
            # Keep original: guess from source
            out_ext = ".aac"  # safe default for copied audio

        out_name = f"{output_base}_{i + 1}{out_ext}" if total > 1 else f"{output_base}{out_ext}"
        output_path = os.path.join(task.work_dir, out_name)

        await status_msg.edit_text(
            f"🎵 **Extracting audio** {i + 1}/{total} — Task: `{task.task_id}`\n"
            f"Format: {fmt}"
        )

        if fmt_info["codec"] == "copy":
            cmd = [
                "ffmpeg", "-y", "-i", path,
                "-vn", "-c:a", "copy", output_path,
            ]
        else:
            cmd = [
                "ffmpeg", "-y", "-i", path,
                "-vn", "-c:a", fmt_info["codec"], output_path,
            ]

        ok, _ = await _run_ffmpeg(task, cmd, status_msg)
        if not ok:
            return False

        if total == 1:
            task.output_name = out_name

    return True


# ═══════════════════════════════════════════════════════════════
#  TOOL: EV — Extract Video Only (remove audio)
# ═══════════════════════════════════════════════════════════════

async def process_extract_video(task, downloaded_files, status_msg) -> bool:
    """Remove all audio streams, keep video only."""
    total = len(downloaded_files)
    output_base = os.path.splitext(task.output_name)[0]
    output_ext = os.path.splitext(task.output_name)[1] or ".mp4"

    for i, (idx, path, msg) in enumerate(downloaded_files):
        if task.cancel_event.is_set():
            return False

        out_name = f"{output_base}_{i + 1}{output_ext}" if total > 1 else task.output_name
        output_path = os.path.join(task.work_dir, out_name)

        await status_msg.edit_text(
            f"🎬 **Extracting video** {i + 1}/{total} — Task: `{task.task_id}`\n"
            f"Removing all audio streams..."
        )

        cmd = [
            "ffmpeg", "-y", "-i", path,
            "-an", "-c:v", "copy", output_path,
        ]

        ok, _ = await _run_ffmpeg(task, cmd, status_msg)
        if not ok:
            return False

        if total == 1:
            task.output_name = out_name

    return True
