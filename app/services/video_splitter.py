"""Video splitting utility using ffmpeg stream copy to stay below file size limits."""

from __future__ import annotations

import math
from pathlib import Path
import re
import shutil
import subprocess
from typing import List, Optional

from app.utils.logging import get_logger

logger = get_logger(__name__)


def find_ffmpeg_binary(configured_bin: Optional[str] = None) -> Optional[str]:
    """Find a valid ffmpeg binary from configuration, PATH, or standard user directories."""
    if configured_bin and configured_bin.strip():
        return configured_bin.strip()

    # Check PATH
    which_ffmpeg = shutil.which("ffmpeg")
    if which_ffmpeg:
        return which_ffmpeg

    # Common user install locations (e.g. ~/bin/ffmpeg on PaaS without root)
    home_bin = Path.home() / "bin" / "ffmpeg"
    if home_bin.exists() and home_bin.is_file():
        return str(home_bin)

    local_bin = Path("/usr/local/bin/ffmpeg")
    if local_bin.exists() and local_bin.is_file():
        return str(local_bin)

    return None


def get_video_duration(video_path: Path, ffmpeg_bin: str = "ffmpeg") -> Optional[float]:
    """Extract media duration in seconds using ffmpeg output."""
    try:
        proc = subprocess.run(
            [ffmpeg_bin, "-i", str(video_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr)
        if match:
            hours, minutes, seconds = match.groups()
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except Exception as exc:
        logger.debug(f"Could not extract duration from {video_path}: {exc}")
    return None


def split_video_file(
    video_path: Path,
    target_part_size: int = 48 * 1024 * 1024,
    ffmpeg_bin: Optional[str] = None,
) -> List[Path]:
    """Split an oversized video into multiple smaller parts using stream copy (-c copy).

    Returns a list of created part Paths, or [video_path] if splitting is not needed or failed.
    """
    if not video_path.exists():
        return []

    file_size = video_path.stat().st_size
    if file_size <= target_part_size:
        return [video_path]

    actual_ffmpeg = find_ffmpeg_binary(ffmpeg_bin)
    if not actual_ffmpeg:
        logger.warning(
            f"ffmpeg introuvable : impossible de scinder la vidéo volumineuse ({file_size} octets)."
        )
        return [video_path]

    duration = get_video_duration(video_path, ffmpeg_bin=actual_ffmpeg)
    if not duration or duration <= 0:
        logger.warning(f"Durée introuvable pour {video_path}, scission impossible.")
        return [video_path]

    num_parts = math.ceil(file_size / target_part_size)
    if num_parts <= 1:
        return [video_path]

    segment_duration = duration / num_parts
    parts: List[Path] = []
    base_name = video_path.stem
    ext = video_path.suffix

    logger.info(
        f"Découpe de {video_path.name} ({file_size // (1024*1024)} Mo, {duration:.1f}s) en {num_parts} parties..."
    )

    for i in range(num_parts):
        part_idx = i + 1
        start_time = round(i * segment_duration, 2)
        out_part = video_path.parent / f"{base_name}_part{part_idx}_of_{num_parts}{ext}"

        cmd = [
            actual_ffmpeg,
            "-y",
            "-ss",
            str(start_time),
            "-i",
            str(video_path),
            "-t",
            str(round(segment_duration, 2)),
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            "-reset_timestamps",
            "1",
            str(out_part),
        ]

        try:
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            if res.returncode == 0 and out_part.exists() and out_part.stat().st_size > 0:
                parts.append(out_part)
            else:
                logger.warning(f"Échec création partie {part_idx}/{num_parts} de {video_path.name}")
                for p in parts:
                    p.unlink(missing_ok=True)
                return [video_path]
        except Exception as exc:
            logger.warning(f"Erreur ffmpeg lors de la découpe de {video_path.name}: {exc}")
            for p in parts:
                p.unlink(missing_ok=True)
            return [video_path]

    # Découpe réussie : suppression du fichier source trop volumineux
    video_path.unlink(missing_ok=True)
    logger.info(f"{num_parts} parties créées avec succès pour {video_path.name}.")
    return parts
