"""
Google Cloud Storage service for audio file persistence.
Files are private; access via presigned URLs or direct IAM.
"""
import os
from datetime import timedelta
from google.cloud import storage

BUCKET_NAME = os.environ.get("GCS_AUDIO_BUCKET", "bandami-dev-audio")


def _get_bucket():
    client = storage.Client()
    return client.bucket(BUCKET_NAME)


def upload_audio_bytes(exam_id: str, audio_bytes: bytes, content_type: str = "audio/webm") -> str:
    """Upload audio bytes to GCS. Returns the blob path."""
    blob = _get_bucket().blob(f"audio/{exam_id}.webm")
    blob.upload_from_string(audio_bytes, content_type=content_type)
    return blob.name


def get_audio_url(exam_id: str) -> str:
    """Generate a 10-minute presigned GET URL for audio playback."""
    blob = _get_bucket().blob(f"audio/{exam_id}.webm")
    if not blob.exists():
        raise FileNotFoundError(f"Audio not found: {exam_id}")
    return blob.generate_signed_url(
        expiration=timedelta(minutes=10),
        method="GET",
    )


def delete_audio(exam_id: str):
    """Hard-delete an audio file. Used in account deletion flow."""
    blob = _get_bucket().blob(f"audio/{exam_id}.webm")
    blob.delete(ignore_not_found=True)
