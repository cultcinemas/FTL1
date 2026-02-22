# f2lnk/utils/split_upload.py
# Central utility for uploading files with auto-split for >2GB files

import os
import math
import asyncio
import logging

logger = logging.getLogger(__name__)

# Telegram Bot API limit: 2GB. Use 1.95GB to be safe.
TG_MAX_SIZE = int(1.95 * 1024 * 1024 * 1024)


async def split_file(file_path: str, max_bytes: int = TG_MAX_SIZE) -> list:
    """
    Split a file into parts of max_bytes each.
    Returns list of part file paths.
    If file is already small enough, returns [file_path].
    """
    file_size = os.path.getsize(file_path)
    if file_size <= max_bytes:
        return [file_path]

    base_name = os.path.basename(file_path)
    split_dir = os.path.join(os.path.dirname(file_path), f"{base_name}_parts")
    os.makedirs(split_dir, exist_ok=True)

    num_parts = math.ceil(file_size / max_bytes)
    part_paths = []

    logger.info("Splitting %s (%d bytes) into %d parts", base_name, file_size, num_parts)

    # Use binary split via Python for cross-platform compatibility
    with open(file_path, "rb") as f:
        for i in range(num_parts):
            part_name = f"{base_name}.part{i + 1:02d}"
            part_path = os.path.join(split_dir, part_name)
            with open(part_path, "wb") as pf:
                remaining = max_bytes
                while remaining > 0:
                    chunk = f.read(min(remaining, 8 * 1024 * 1024))  # 8MB chunks
                    if not chunk:
                        break
                    pf.write(chunk)
                    remaining -= len(chunk)
            if os.path.getsize(part_path) > 0:
                part_paths.append(part_path)
                logger.info("  Part %d: %s (%d bytes)", i + 1, part_name, os.path.getsize(part_path))

    return part_paths


def _humanbytes(size: int) -> str:
    """Quick human-readable size."""
    if not size:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"


async def upload_file_or_split(
    client,
    chat_id: int,
    file_path: str,
    caption: str = "",
    file_name: str = None,
    reply_to: int = None,
    progress=None,
) -> list:
    """
    Upload a file to Telegram. If >1.95GB, auto-split and upload parts as documents.
    Returns list of sent Message objects.
    """
    if not os.path.isfile(file_path):
        logger.error("File not found: %s", file_path)
        return []

    file_size = os.path.getsize(file_path)
    fname = file_name or os.path.basename(file_path)
    sent_messages = []

    if file_size <= TG_MAX_SIZE:
        # Normal upload — detect type by extension
        ext = os.path.splitext(file_path)[1].lower()
        try:
            if ext in (".mp4", ".mkv", ".avi", ".webm", ".mov", ".flv", ".ts"):
                msg = await client.send_video(
                    chat_id, file_path, caption=caption,
                    file_name=fname, reply_to_message_id=reply_to,
                    progress=progress,
                )
            elif ext in (".mp3", ".aac", ".ogg", ".flac", ".wav", ".m4a", ".opus", ".mka"):
                msg = await client.send_audio(
                    chat_id, file_path, caption=caption,
                    file_name=fname, reply_to_message_id=reply_to,
                    progress=progress,
                )
            else:
                msg = await client.send_document(
                    chat_id, file_path, caption=caption,
                    file_name=fname, reply_to_message_id=reply_to,
                    progress=progress,
                )
            sent_messages.append(msg)
        except Exception as e:
            logger.error("Upload failed for %s: %s", fname, e)
            raise
    else:
        # Split and upload as documents
        logger.info("File %s is %s (>1.95GB), splitting...", fname, _humanbytes(file_size))
        parts = await split_file(file_path)

        for i, part_path in enumerate(parts):
            part_size = os.path.getsize(part_path)
            part_name = os.path.basename(part_path)
            part_caption = (
                f"📦 **Split Upload** ({i + 1}/{len(parts)})\n"
                f"📁 `{part_name}`\n"
                f"📦 Size: `{_humanbytes(part_size)}`\n"
            )
            if i == 0 and caption:
                part_caption = caption + "\n\n" + part_caption

            try:
                msg = await client.send_document(
                    chat_id, part_path, caption=part_caption,
                    file_name=part_name, reply_to_message_id=reply_to,
                    progress=progress,
                )
                sent_messages.append(msg)
            except Exception as e:
                logger.error("Split upload failed for part %d: %s", i + 1, e)
                raise
            finally:
                # Clean up part file after upload
                try:
                    os.remove(part_path)
                except Exception:
                    pass

        # Clean up split directory
        split_dir = os.path.dirname(parts[0]) if parts else None
        if split_dir and split_dir != os.path.dirname(file_path):
            try:
                os.rmdir(split_dir)
            except Exception:
                pass

    return sent_messages
