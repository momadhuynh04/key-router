"""SQLite job store for audio (video-to-SFX) jobs.

Reuses the video store implementation but with its own database file and media
directory, so audio and video jobs never mix.
"""

from typing import Optional

from audio.errors import AudioIdempotencyConflict
from video.errors import IdempotencyConflict as _VideoIdempotencyConflict
from video.jobs import VideoJobStore


class AudioJobStore(VideoJobStore):
    def __init__(self, data_dir: Optional[str] = None):
        super().__init__(
            data_dir,
            db_filename="audio_jobs.db",
            media_subdir="audio",
            media_ext=".wav",
            id_prefix="aud_",
        )

    def create_or_get(self, **kwargs):
        # Re-raise the video store's conflict as the audio one so callers only
        # need to know about audio.errors.
        try:
            return super().create_or_get(**kwargs)
        except _VideoIdempotencyConflict:
            raise AudioIdempotencyConflict()


audio_job_store = AudioJobStore()
