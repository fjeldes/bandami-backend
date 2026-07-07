"""
Google Cloud Storage service for audio and image file persistence.
Files are private for audio (presigned URLs); images are public.
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


def download_audio_bytes(exam_id: str) -> tuple[bytes, str]:
    """Download audio bytes from GCS. Returns (bytes, content_type)."""
    from google.cloud.exceptions import NotFound
    blob = _get_bucket().blob(f"audio/{exam_id}.webm")
    try:
        return blob.download_as_bytes(), blob.content_type or "audio/webm"
    except NotFound:
        raise FileNotFoundError(f"Audio not found: {exam_id}")


def delete_audio(exam_id: str):
    """Hard-delete an audio file. Used in account deletion flow."""
    blob = _get_bucket().blob(f"audio/{exam_id}.webm")
    blob.delete(ignore_not_found=True)


# ---- Question Images ----------------------------------------------------

QUESTION_IMAGES_BUCKET = os.environ.get("GCS_IMAGES_BUCKET", "bandami-prod-audio")


def _get_images_bucket():
    client = storage.Client()
    return client.bucket(QUESTION_IMAGES_BUCKET)


def upload_question_image(question_id: str, image_bytes: bytes, content_type: str = "image/png") -> str:
    """Upload question image to GCS. Returns the public URL."""
    ext = "png" if content_type == "image/png" else "jpg"
    blob_path = f"questions/{question_id}.{ext}"
    blob = _get_images_bucket().blob(blob_path)
    blob.upload_from_string(image_bytes, content_type=content_type)
    blob.make_public()
    return f"https://storage.googleapis.com/{QUESTION_IMAGES_BUCKET}/{blob_path}"


def delete_question_image(question_id: str):
    """Delete question images (both png and jpg)."""
    for ext in ["png", "jpg"]:
        blob_path = f"questions/{question_id}.{ext}"
        blob = _get_images_bucket().blob(blob_path)
        blob.delete(ignore_not_found=True)
