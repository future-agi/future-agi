"""Worker-start cleanup backstop; the v1 decoder itself creates no temp files."""

import logging
import shutil
import time
from pathlib import Path

from django.conf import settings


def sweep_audio_temp_files():
    cutoff = time.time() - 24 * 60 * 60
    root = Path(settings.VOICE_AUDIO_TMP_DIR)
    for path in root.glob("audio-analysis-*"):
        try:
            if path.is_symlink() or path.stat().st_mtime >= cutoff:
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError:
            logging.getLogger(__name__).warning("audio_metrics.temp_cleanup_failed")
