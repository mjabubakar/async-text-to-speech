import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def cleanup_old_audio(directory: Path, max_age_seconds: float) -> int:
    """Delete .mp3 and leftover .tmp files older than max_age_seconds."""
    cutoff = time.time() - max_age_seconds
    removed = 0
    for path in directory.iterdir():
        if path.suffix not in {".mp3", ".tmp"} or not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            logger.warning("Could not clean up %s", path, exc_info=True)
    return removed
