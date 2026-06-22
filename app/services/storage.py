"""
Google Cloud Storage service for presigned URLs — secure audio file access.
Files are private; only accessible via short-lived presigned URLs.
"""
import os
from datetime import timedelta
from google.cloud import storage

BUCKET_NAME = os.environ.get("GCS_AUDIO_BUCKET", "bandami-audio-private")


def _get_bucket():
    client = storage.Client()
    return client.bucket(BUCKET_NAME)


def generate_upload_url(exam_id: str, content_type: str = "audio/webm") -> str:
    """Generate a 5-minute presigned PUT URL for direct audio upload."""
    blob = _get_bucket().blob(f"audio/{exam_id}.webm")
    return blob.generate_signed_url(
        expiration=timedelta(minutes=5),
        method="PUT",
        content_type=content_type,
    )


def generate_download_url(exam_id: str) -> str:
    """Generate a 10-minute presigned GET URL for audio playback."""
    blob = _get_bucket().blob(f"audio/{exam_id}.webm")
    return blob.generate_signed_url(
        expiration=timedelta(minutes=10),
        method="GET",
    )


def delete_audio(exam_id: str):
    """Hard-delete an audio file. Used in account deletion flow."""
    blob = _get_bucket().blob(f"audio/{exam_id}.webm")
    blob.delete(ignore_not_found=True)
